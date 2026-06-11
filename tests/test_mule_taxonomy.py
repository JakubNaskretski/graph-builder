"""Phase-3 Mule taxonomy — parse layer + config-extractor emissions + a full
integration build over a realistic fictional app (Acme orders API).

Covers: APIkit flow-name decoding and router wiring, source triggers, global
configs, property reads (keys only), and the back-compat freeze (the Phase-1
``calls``/``uses``/id assertions keep holding on the same fixtures).
"""
from pathlib import Path

from graphbuilder import build_graph
from graphbuilder.extractors.mule import MuleConfigExtractor
from graphbuilder.mulesoft import (
    parse_apikit_flow_name, parse_artifacts, parse_config, prop_keys_of, spec_name,
)

EX = MuleConfigExtractor()

_NS = (
    'xmlns="http://www.mulesoft.org/schema/mule/core" '
    'xmlns:http="http://www.mulesoft.org/schema/mule/http" '
    'xmlns:db="http://www.mulesoft.org/schema/mule/db" '
    'xmlns:jms="http://www.mulesoft.org/schema/mule/jms" '
    'xmlns:apikit="http://www.mulesoft.org/schema/mule/mule-apikit" '
    'xmlns:secure-properties="http://www.mulesoft.org/schema/mule/secure-properties"'
)

# The API file: main flow (listener + router) + two APIkit-convention flows.
API_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<mule {_NS}>
  <flow name="acme-orders-main">
    <http:listener config-ref="httpListenerConfig" path="/api/*"/>
    <apikit:router config-ref="orders-config"/>
  </flow>
  <flow name="get:\\orders:orders-config">
    <flow-ref name="listOrders"/>
  </flow>
  <flow name="put:\\orders\\(orderId):application\\json:orders-config">
    <flow-ref name="updateOrder"/>
  </flow>
</mule>
"""

# The impl file: a scheduled flow, a JMS-sourced flow, config-refs + ${} reads.
IMPL_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<mule {_NS}>
  <flow name="listOrders">
    <db:select config-ref="dbConfig"><db:sql>SELECT 1</db:sql></db:select>
  </flow>
  <flow name="updateOrder">
    <db:update config-ref="dbConfig"/>
  </flow>
  <flow name="nightlySync">
    <scheduler>
      <scheduling-strategy><fixed-frequency frequency="60000"/></scheduling-strategy>
    </scheduler>
    <db:select config-ref="dbConfig"/>
    <flow-ref name="listOrders"/>
  </flow>
  <flow name="onOrderMessage">
    <jms:listener config-ref="jmsConfig" destination="${{jms.queue}}"/>
    <db:insert config-ref="dbConfig"/>
  </flow>
</mule>
"""

# Globals: listener/db configs (with ${} reads), apikit config bound to the RAML,
# a secure-properties config loading its own file, plus the app property files.
GLOBAL_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<mule {_NS}>
  <configuration-properties file="config-dev.yaml"/>
  <configuration-properties file="config-${{env}}.yaml"/>
  <http:listener-config name="httpListenerConfig">
    <http:listener-connection host="${{http.host}}" port="${{http.port}}"/>
  </http:listener-config>
  <db:config name="dbConfig">
    <db:my-sql-connection host="${{db.host}}" password="${{secure::db.password}}"/>
  </db:config>
  <apikit:config name="orders-config" api="resource::com.acme:orders-api:1.0.1:raml:zip:orders.raml"/>
  <secure-properties:config name="secureProps" file="secure.yaml" key="${{enc.key}}"/>
</mule>
"""


def _w(tmp: Path, rel: str, text: str) -> Path:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")
    return p


def _first_ids(nodes) -> dict:
    """id -> node, first emission winning — the registry's setdefault rule."""
    ids: dict = {}
    for n in nodes:
        ids.setdefault(n["id"], n)
    return ids


# --------------------------------------------------------------------------- #
# parse layer
# --------------------------------------------------------------------------- #
def test_parse_apikit_flow_name():
    assert parse_apikit_flow_name("get:\\orders:orders-config") == {
        "method": "get", "path": "/orders", "ctype": "", "config": "orders-config"}
    four = parse_apikit_flow_name("put:\\orders\\(orderId):application\\json:cfg")
    assert four == {"method": "put", "path": "/orders/{orderId}",
                    "ctype": "application/json", "config": "cfg"}
    # not the convention -> None, never guessed
    assert parse_apikit_flow_name("ordersFlow") is None
    assert parse_apikit_flow_name("get:orders:cfg") is None        # no backslash
    assert parse_apikit_flow_name("fetch:\\orders:cfg") is None    # not a method
    assert parse_apikit_flow_name("get:\\a:b:c:d:e") is None       # too many parts


def test_spec_name_normalization():
    assert spec_name("orders.raml") == "orders.raml"
    assert spec_name("api/orders.raml") == "orders.raml"
    assert spec_name("resource::com.acme:orders-api:1.0.1:raml:zip:orders.raml") \
        == "orders.raml"


def test_prop_keys_only_static(monkeypatch=None):
    assert prop_keys_of("${db.host}:${db.port}") == {"db.host", "db.port"}
    assert prop_keys_of("${secure::db.password}") == {"db.password"}
    # expressions / unusual placeholders are dynamic -> skipped
    assert prop_keys_of("#[vars.x] ${vars[0]} ${a b}") == set()
    assert prop_keys_of("plain text") == set()


def test_parse_config_phase3_fields(tmp_path):
    flows = {f.name: f for f in
             parse_config(_w(tmp_path, "src/main/mule/impl.xml", IMPL_XML))}
    sched = flows["nightlySync"].source
    assert sched == {"kind": "scheduler", "frequency": "60000"}
    jms = flows["onOrderMessage"].source
    assert jms["kind"] == "source" and jms["connector"] == "jms"
    assert jms["element"] == "listener" and jms["config"] == "jmsConfig"
    # a flow whose first child is a processor has NO source
    assert flows["listOrders"].source is None
    assert flows["listOrders"].config_refs == {"dbConfig"}
    assert flows["onOrderMessage"].prop_reads == {"jms.queue"}


def test_parse_config_router_and_api(tmp_path):
    flows = {f.name: f for f in
             parse_config(_w(tmp_path, "src/main/mule/api.xml", API_XML))}
    main = flows["acme-orders-main"]
    assert main.source == {"kind": "httplistener", "path": "/api/*",
                           "config": "httpListenerConfig"}
    assert main.routers == {"orders-config"} == main.apikit_refs
    assert main.config_refs == {"httpListenerConfig"}
    assert main.api is None
    get = flows["get:\\orders:orders-config"]
    assert get.api["path"] == "/orders" and get.api["config"] == "orders-config"


def test_parse_artifacts(tmp_path):
    arts = parse_artifacts(_w(tmp_path, "src/main/mule/global.xml", GLOBAL_XML))
    assert arts.property_files == ["config-dev.yaml", "config-${env}.yaml"]
    assert arts.prop_keys == {"env"}
    assert [a.name for a in arts.apikit_configs] == ["orders-config"]
    assert arts.apikit_configs[0].spec == "orders.raml"
    by_name = {g.name: g for g in arts.globals}
    assert set(by_name) == {"httpListenerConfig", "dbConfig", "secureProps"}
    assert by_name["dbConfig"].prop_keys == {"db.host", "db.password"}
    assert by_name["secureProps"].props_file == "secure.yaml"
    assert by_name["secureProps"].element == "secure-properties:config"


def test_parse_artifacts_never_raises(tmp_path):
    broken = _w(tmp_path, "src/main/mule/broken.xml", "<mule><flow name='x'>")
    a = parse_artifacts(broken)
    assert a.globals == [] and a.property_files == []
    not_mule = _w(tmp_path, "src/main/mule/other.xml", "<beans><x name='y'/></beans>")
    assert parse_artifacts(not_mule).globals == []


# --------------------------------------------------------------------------- #
# extractor emissions
# --------------------------------------------------------------------------- #
def test_extract_api_file(tmp_path):
    nodes, edges = EX.extract(_w(tmp_path, "src/main/mule/api.xml", API_XML))
    ids = _first_ids(nodes)
    et = {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges}
    get_flow = ids["muleflow/get:\\orders:orders-config"]
    assert get_flow["api_method"] == "get" and get_flow["api_path"] == "/orders"
    assert get_flow["api_config"] == "orders-config"
    main = ids["muleflow/acme-orders-main"]
    assert main["source_kind"] == "httplistener" and main["source_path"] == "/api/*"
    assert ids["httplistener//api/*"]["config"] == "httpListenerConfig"
    assert ids["apikitrouter/orders-config"]["flow"] == "acme-orders-main"
    fid = "muleflow/acme-orders-main"
    assert (fid, "exposedby", "httplistener", "/api/*") in et
    assert (fid, "contains", "apikitrouter", "orders-config") in et
    assert (fid, "usesconfig", "apikitconfig", "orders-config") in et
    assert (fid, "usesconfig", "globalconfig", "httpListenerConfig") in et
    assert ("apikitrouter/orders-config", "usesconfig", "apikitconfig",
            "orders-config") in et
    # the APIkit-named flows wire implements + routesto from their own name
    assert ("muleflow/get:\\orders:orders-config", "implements", "apiresource",
            "/orders") in et
    assert ("apikitrouter/orders-config", "routesto", "muleflow",
            "get:\\orders:orders-config") in et
    assert ("apikitrouter/orders-config", "routesto", "muleflow",
            "put:\\orders\\(orderId):application\\json:orders-config") in et


def test_extract_global_file(tmp_path):
    nodes, edges = EX.extract(_w(tmp_path, "src/main/mule/global.xml", GLOBAL_XML))
    ids = {n["id"]: n for n in nodes}
    et = {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges}
    assert ids["apikitconfig/orders-config"]["spec"] == "orders.raml"
    assert ids["globalconfig/dbConfig"]["element"] == "db:config"
    assert "muleartifactdescriptor/app" in ids
    assert ("apikitconfig/orders-config", "boundto", "apispec", "orders.raml") in et
    assert ("globalconfig/dbConfig", "reads", "propertykey", "db.host") in et
    assert ("globalconfig/dbConfig", "reads", "propertykey", "db.password") in et
    assert ("globalconfig/secureProps", "loads", "propertyfile", "secure.yaml") in et
    app = "muleartifactdescriptor/app"
    assert (app, "loads", "propertyfile", "config-dev.yaml") in et
    assert (app, "loads", "propertyfile", "config-${env}.yaml") in et
    assert (app, "reads", "propertykey", "env") in et


# --------------------------------------------------------------------------- #
# full build over the Acme app (config XML + RAML + props + pom + descriptor)
# --------------------------------------------------------------------------- #
RAML = """#%RAML 1.0
title: Acme Orders API
/orders:
  get:
  post:
  /{orderId}:
    get:
    put:
"""

YAML_PROPS = """http:
  host: localhost
  port: "8081"
db:
  host: localhost
"""

POM = """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <artifactId>acme-orders-api</artifactId>
  <dependencies>
    <dependency>
      <groupId>org.mule.connectors</groupId>
      <artifactId>mule-db-connector</artifactId>
      <version>1.14.0</version>
      <classifier>mule-plugin</classifier>
    </dependency>
  </dependencies>
</project>
"""

DESCRIPTOR = """{
  "name": "acme-orders-api",
  "minMuleVersion": "4.4.0",
  "secureProperties": ["db.password"]
}
"""


def _make_app(tmp: Path) -> Path:
    _w(tmp, "src/main/mule/api.xml", API_XML)
    _w(tmp, "src/main/mule/impl.xml", IMPL_XML)
    _w(tmp, "src/main/mule/global.xml", GLOBAL_XML)
    _w(tmp, "src/main/resources/api/orders.raml", RAML)
    _w(tmp, "src/main/resources/config-dev.yaml", YAML_PROPS)
    _w(tmp, "pom.xml", POM)
    _w(tmp, "mule-artifact.json", DESCRIPTOR)
    return tmp


def test_full_build_wires_the_taxonomy(tmp_path):
    g = build_graph(_make_app(tmp_path))
    assert g["errors"] == [] and g["unresolved"] == []
    ids = {n["id"]: n for n in g["nodes"]}
    edges = {(e["src"], e["type"], e["dst"]) for e in g["edges"]}

    # spec -> resource -> implementing flow, all REAL nodes (not stubs)
    assert ids["apispec/orders.raml"].get("external") is not True
    assert ids["apiresource//orders"]["methods"] == ["get", "post"]
    assert ("apikitconfig/orders-config", "boundto", "apispec/orders.raml") in edges
    assert ("apispec/orders.raml", "declares", "apiresource//orders/{orderId}") in edges
    assert ("muleflow/get:\\orders:orders-config", "implements",
            "apiresource//orders") in edges
    assert ids["apiresource//orders"].get("external") is not True
    assert ("muleflow/put:\\orders\\(orderId):application\\json:orders-config",
            "implements", "apiresource//orders/{orderId}") in edges

    # router -> routed flows; main flow -> router -> apikit config
    assert ("apikitrouter/orders-config", "routesto",
            "muleflow/get:\\orders:orders-config") in edges
    assert ("muleflow/acme-orders-main", "contains",
            "apikitrouter/orders-config") in edges
    assert ("apikitrouter/orders-config", "usesconfig",
            "apikitconfig/orders-config") in edges

    # source triggers
    assert ("muleflow/acme-orders-main", "exposedby", "httplistener//api/*") in edges
    assert ("muleflow/nightlySync", "triggeredby", "scheduler/nightlySync") in edges
    assert ids["scheduler/nightlySync"]["frequency"] == "60000"
    assert ("muleflow/onOrderMessage", "triggeredby", "mulesource/jms:listener") in edges

    # property chain: app loads file (real), file defines keys, flow/config reads
    assert ids["propertyfile/config-dev.yaml"].get("external") is not True
    assert ("muleartifactdescriptor/app", "loads",
            "propertyfile/config-dev.yaml") in edges
    assert ("propertyfile/config-dev.yaml", "defineskey",
            "propertykey/db.host") in edges
    assert ("globalconfig/dbConfig", "reads", "propertykey/db.host") in edges
    assert ("muleflow/onOrderMessage", "reads", "propertykey/jms.queue") in edges
    # the dynamic file name stays visible as an external stub, never guessed
    assert ids["propertyfile/config-${env}.yaml"].get("external") is True
    # the secure key is flagged by the descriptor and tied back to the app
    assert ids["propertykey/db.password"]["secure"] is True
    assert ("propertykey/db.password", "securedby",
            "muleartifactdescriptor/app") in edges

    # build metadata (descriptor sorts first -> its label wins the registry)
    assert ids["muleartifactdescriptor/app"]["label"] == "acme-orders-api"
    assert ids["muleartifactdescriptor/app"]["minMuleVersion"] == "4.4.0"
    assert ("muleartifactdescriptor/app", "dependson",
            "pomdependency/org.mule.connectors:mule-db-connector") in edges

    # an undeclared config-ref becomes a visible external stub
    assert ids["globalconfig/jmsConfig"].get("external") is True

    # ---- Phase-1 back-compat freeze: calls/uses semantics unchanged ----
    assert ("muleflow/nightlySync", "calls", "muleflow/listOrders") in edges
    assert ("muleflow/listOrders", "uses", "muleconnector/db") in edges
    assert ids["muleflow/listOrders"]["kind"] == "flow"


def test_full_build_without_support_files_stubs_cleanly(tmp_path):
    """Config XML alone (no RAML/props/pom retrieved): every cross-file target
    becomes an external stub — visible, never an error."""
    _w(tmp_path, "src/main/mule/api.xml", API_XML)
    _w(tmp_path, "src/main/mule/global.xml", GLOBAL_XML)
    g = build_graph(tmp_path)
    assert g["errors"] == [] and g["unresolved"] == []
    ids = {n["id"]: n for n in g["nodes"]}
    assert ids["apispec/orders.raml"].get("external") is True
    assert ids["apiresource//orders"].get("external") is True
    assert ids["propertyfile/config-dev.yaml"].get("external") is True
