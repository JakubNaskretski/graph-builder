"""Mule extractor tests — handles() + extract() + a full build_graph(tmp_path).

Fictional fixtures only (Acme app, ordersFlow / validateSub / enrichSub).
"""
from pathlib import Path

import graphbuilder.resolvers as resolvers
from graphbuilder import build_graph
from graphbuilder.core import GraphBuilder
from graphbuilder.extractors.mule import MuleConfigExtractor

EX = MuleConfigExtractor()

_NS = (
    'xmlns="http://www.mulesoft.org/schema/mule/core" '
    'xmlns:http="http://www.mulesoft.org/schema/mule/http" '
    'xmlns:db="http://www.mulesoft.org/schema/mule/db"'
)

ORDERS_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<mule {_NS}>
  <flow name="ordersFlow">
    <http:listener config-ref="HTTP_Listener" path="/orders"/>
    <flow-ref name="validateSub"/>
    <flow-ref name="missingSub"/>
    <db:select config-ref="Db_Config"><db:sql>SELECT 1</db:sql></db:select>
  </flow>
  <sub-flow name="validateSub">
    <logger message="ok"/>
  </sub-flow>
</mule>
"""


def _w(tmp: Path, name: str, text: str) -> Path:
    p = tmp / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")
    return p


def _ids(nodes):
    return {n["id"]: n for n in nodes}


def _et(edges):
    return {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges}


def test_handles():
    assert EX.handles(Path("acme/src/main/mule/orders.xml")) is True
    assert EX.handles(Path("acme/src/main/resources/api.raml")) is False
    assert EX.handles(Path("force-app/main/default/My.flow-meta.xml")) is False


def test_extract_nodes_and_attrs(tmp_path):
    nodes, _ = EX.extract(_w(tmp_path / "src/main/mule", "orders.xml", ORDERS_XML))
    ids = _ids(nodes)
    flow = ids["muleflow/ordersFlow"]
    assert flow["type"] == "muleflow" and flow["label"] == "ordersFlow"
    assert flow["kind"] == "flow" and flow["file"] == "orders.xml"
    assert ids["muleflow/validateSub"]["kind"] == "sub-flow"
    assert ids["muleconnector/http"]["type"] == "muleconnector"
    assert "muleconnector/db" in ids


def test_extract_edges(tmp_path):
    _, edges = EX.extract(_w(tmp_path / "src/main/mule", "orders.xml", ORDERS_XML))
    et = _et(edges)
    fid = "muleflow/ordersFlow"
    assert (fid, "calls", "muleflow", "validateSub") in et
    assert (fid, "calls", "muleflow", "missingSub") in et
    assert (fid, "uses", "muleconnector", "http") in et
    assert (fid, "uses", "muleconnector", "db") in et


def test_build_graph_resolves_calls_and_stubs(tmp_path):
    app = tmp_path / "acme" / "src" / "main" / "mule"
    _w(app, "orders.xml", ORDERS_XML)
    g = (GraphBuilder().register(EX)
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))
    assert g["errors"] == [] and g["unresolved"] == []
    ids = {n["id"]: n for n in g["nodes"]}
    edges = {(e["src"], e["type"], e["dst"]) for e in g["edges"]}
    # a locally-defined sub-flow resolves to the REAL node; an undefined ref stubs
    assert ids["muleflow/validateSub"].get("external") is not True
    assert ids["muleflow/missingSub"].get("external") is True
    assert ("muleflow/ordersFlow", "calls", "muleflow/validateSub") in edges
    assert ("muleflow/ordersFlow", "calls", "muleflow/missingSub") in edges
    assert ("muleflow/ordersFlow", "uses", "muleconnector/db") in edges


def test_cross_file_flow_ref_resolves(tmp_path):
    app = tmp_path / "src" / "main" / "mule"
    _w(app, "orders.xml", ORDERS_XML)
    _w(app, "shared.xml", f"""<mule {_NS}>
  <sub-flow name="missingSub"><logger message="now defined"/></sub-flow>
</mule>""")
    g = build_graph(tmp_path)           # the registered extractor set incl. Mule
    ids = {n["id"]: n for n in g["nodes"]}
    # now defined in another file -> resolves to a real node, not a stub
    assert ids["muleflow/missingSub"].get("external") is not True


def test_discovered_by_build_graph(tmp_path):
    """The extractor is auto-registered (EXTRACTORS list) — no manual wiring."""
    _w(tmp_path / "src/main/mule", "orders.xml", ORDERS_XML)
    g = build_graph(tmp_path)
    assert "muleflow/ordersFlow" in {n["id"] for n in g["nodes"]}


def test_never_raises_on_broken_content(tmp_path):
    p = _w(tmp_path / "src/main/mule", "broken.xml", "<mule><flow name='x'>")
    nodes, edges = EX.extract(p)         # must not raise
    assert nodes == [] and edges == []
