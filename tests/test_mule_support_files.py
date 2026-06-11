"""Unit tests for the Phase-3 Mule support-file extractors: RAML specs,
property files (keys only — never values) and build metadata. Fictional Acme
fixtures only.
"""
from pathlib import Path

from graphbuilder.extractors.mulebuild import MuleBuildExtractor
from graphbuilder.extractors.muleprops import (
    MulePropertiesExtractor, parse_properties, parse_yaml_keys,
)
from graphbuilder.extractors.raml import RamlExtractor, parse_raml

RAML_EX = RamlExtractor()
PROPS_EX = MulePropertiesExtractor()
BUILD_EX = MuleBuildExtractor()


def _w(tmp: Path, rel: str, text: str) -> Path:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")
    return p


# --------------------------------------------------------------------------- #
# RAML
# --------------------------------------------------------------------------- #
def test_parse_raml_nesting_and_methods():
    spec = parse_raml("""#%RAML 1.0
title: Acme API
/orders:
  get:
  /{orderId}:
    delete:
/health:
  get:
""")
    assert spec["title"] == "Acme API"
    assert spec["resources"] == {"/orders": ["get"],
                                 "/orders/{orderId}": ["delete"],
                                 "/health": ["get"]}


def test_parse_raml_rejects_fragments_and_non_raml():
    assert parse_raml("#%RAML 1.0 Trait\nusage: paging\n") is None
    assert parse_raml("openapi: 3.0.0\n") is None
    assert parse_raml("") is None


def test_parse_raml_ignores_lookalikes_in_content():
    spec = parse_raml("""#%RAML 1.0
title: Acme API
/orders:
  get:
    description: |
      /fake-resource:
        post:
    queryParameters:
      delete:
        type: boolean
types:
  Order:
    properties:
      put: string
""")
    # block-scalar text, deep parameter names and type properties stay out
    assert spec["resources"] == {"/orders": ["get"]}


def test_raml_extractor_nodes_edges(tmp_path):
    p = _w(tmp_path, "src/main/resources/api/orders.raml",
           "#%RAML 1.0\ntitle: Acme\n/orders:\n  get:\n")
    nodes, edges = RAML_EX.extract(p)
    ids = {n["id"]: n for n in nodes}
    assert ids["apispec/orders.raml"]["title"] == "Acme"
    assert ids["apispec/orders.raml"]["file"] == "api/orders.raml"
    assert ids["apiresource//orders"]["methods"] == ["get"]
    assert {(e["src"], e["type"], e["to_name"]) for e in edges} == {
        ("apispec/orders.raml", "declares", "/orders")}
    # a trait fragment in the same tree contributes nothing
    frag = _w(tmp_path, "src/main/resources/api/paged.raml",
              "#%RAML 1.0 Trait\nusage: paging\n")
    assert RAML_EX.extract(frag) == ([], [])
    assert RAML_EX.handles(Path("x/src/main/mule/orders.xml")) is False


# --------------------------------------------------------------------------- #
# property files — KEYS only, never values
# --------------------------------------------------------------------------- #
def test_parse_properties_keys():
    keys = parse_properties("""# comment
db.host=localhost
db.port: 3306
long.value=first \\
   continued-value-line=not-a-key
! old-style comment
bare-line-without-separator
""")
    assert keys == ["db.host", "db.port", "long.value"]


def test_parse_yaml_keys_nesting():
    keys = parse_yaml_keys("""# Acme dev config
http:
  host: localhost
  port: "8081"
db:
  host: localhost
banner: |
  not.a.key: inside block
list:
- item: x
""")
    assert keys == ["banner", "db.host", "http.host", "http.port"]


def test_props_extractor_scope_and_emissions(tmp_path):
    p = _w(tmp_path, "src/main/resources/config-dev.yaml", "db:\n  host: x\n")
    nodes, edges = PROPS_EX.extract(p)
    ids = {n["id"]: n for n in nodes}
    assert ids["propertyfile/config-dev.yaml"]["format"] == "yaml"
    assert "propertykey/db.host" in ids
    # no node or edge ever carries a property VALUE
    assert all("x" != v for n in nodes for v in n.values())
    assert {(e["src"], e["type"], e["to_name"]) for e in edges} == {
        ("propertyfile/config-dev.yaml", "defineskey", "db.host")}
    # yaml under the api/ spec tree is NOT a property file
    assert PROPS_EX.handles(
        Path("x/src/main/resources/api/example.yaml")) is False
    assert PROPS_EX.handles(Path("x/src/main/resources/cfg/dev.properties")) is True
    assert PROPS_EX.handles(Path("x/src/main/mule/flow.xml")) is False


# --------------------------------------------------------------------------- #
# build metadata
# --------------------------------------------------------------------------- #
POM = """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <artifactId>acme-app</artifactId>
  <dependencies>
    <dependency>
      <groupId>org.mule.connectors</groupId>
      <artifactId>mule-http-connector</artifactId>
      <version>${httpConnectorVersion}</version>
    </dependency>
  </dependencies>
</project>
"""


def test_pom_needs_a_mule_app_root(tmp_path):
    p = _w(tmp_path, "pom.xml", POM)
    assert BUILD_EX.extract(p) == ([], [])     # no src/main/mule sibling
    (tmp_path / "src" / "main" / "mule").mkdir(parents=True)
    nodes, edges = BUILD_EX.extract(p)
    ids = {n["id"]: n for n in nodes}
    assert ids["muleartifactdescriptor/app"]["label"] == "acme-app"
    dep = ids["pomdependency/org.mule.connectors:mule-http-connector"]
    assert dep["version"] == "${httpConnectorVersion}"      # raw, structural
    assert {(e["src"], e["type"], e["to_name"]) for e in edges} == {
        ("muleartifactdescriptor/app", "dependson",
         "org.mule.connectors:mule-http-connector")}


def test_descriptor_secure_keys(tmp_path):
    (tmp_path / "src" / "main" / "mule").mkdir(parents=True)
    p = _w(tmp_path, "mule-artifact.json",
           '{"minMuleVersion": "4.4.0", "secureProperties": ["db.password", 7]}')
    nodes, edges = BUILD_EX.extract(p)
    ids = {n["id"]: n for n in nodes}
    assert ids["muleartifactdescriptor/app"]["minMuleVersion"] == "4.4.0"
    assert ids["propertykey/db.password"]["secure"] is True
    assert ("propertykey/db.password", "securedby", "app") == \
        next((e["src"], e["type"], e["to_name"]) for e in edges)
    # malformed JSON -> nothing, never raises
    bad = _w(tmp_path, "x/mule-artifact.json", "{broken")
    (tmp_path / "x" / "src" / "main" / "mule").mkdir(parents=True)
    assert BUILD_EX.extract(bad) == ([], [])
