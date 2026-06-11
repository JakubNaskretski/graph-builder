"""MuleSoft parser tests — parse_config + the path helpers.

Fictional fixtures only (Acme app, ordersFlow / validateSub).
"""
from pathlib import Path

from graphbuilder.mulesoft import (
    connector_of,
    is_config_path,
    local_name,
    parse_config,
    rel_path,
)

_NS = (
    'xmlns="http://www.mulesoft.org/schema/mule/core" '
    'xmlns:http="http://www.mulesoft.org/schema/mule/http" '
    'xmlns:db="http://www.mulesoft.org/schema/mule/db" '
    'xmlns:ee="http://www.mulesoft.org/schema/mule/ee/core"'
)

ORDERS_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<mule {_NS}>
  <flow name="ordersFlow">
    <http:listener config-ref="HTTP_Listener" path="/orders"/>
    <flow-ref name="validateSub"/>
    <db:select config-ref="Db_Config"><db:sql>SELECT 1</db:sql></db:select>
    <choice>
      <when expression="#[true]">
        <flow-ref name="enrichSub"/>
      </when>
    </choice>
    <ee:transform/>
  </flow>
  <sub-flow name="validateSub">
    <db:select config-ref="Db_Config"><db:sql>SELECT 2</db:sql></db:select>
  </sub-flow>
</mule>
"""


def _w(tmp: Path, name: str, text: str) -> Path:
    p = tmp / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")
    return p


def test_local_name_and_connector_of():
    assert local_name("{http://www.mulesoft.org/schema/mule/db}select") == "select"
    assert local_name("flow") == "flow"
    assert connector_of("{http://www.mulesoft.org/schema/mule/db}select") == "db"
    assert connector_of("{http://www.mulesoft.org/schema/mule/core}flow") == "core"
    assert connector_of("flow") == ""          # no namespace


def test_is_config_path():
    assert is_config_path(Path("acme/src/main/mule/orders.xml")) is True
    assert is_config_path(Path("acme/src/main/app/legacy.xml")) is True      # Mule 3
    assert is_config_path(Path("acme/src/main/mule/api/impl.xml")) is True   # nested
    assert is_config_path(Path("acme/src/main/resources/log4j2.xml")) is False
    assert is_config_path(Path("force-app/main/default/Foo.object-meta.xml")) is False
    assert is_config_path(Path("acme/src/main/mule/orders.yaml")) is False   # not xml


def test_rel_path():
    assert rel_path(Path("/x/acme/src/main/mule/api/impl.xml")) == "api/impl.xml"
    assert rel_path(Path("/x/acme/src/main/mule/orders.xml")) == "orders.xml"
    assert rel_path(Path("/x/acme/src/main/app/legacy.xml")) == "legacy.xml"
    assert rel_path(Path("/x/loose.xml")) == "loose.xml"                     # no root


def test_parse_config_flows_refs_connectors(tmp_path):
    flows = parse_config(_w(tmp_path / "src/main/mule", "orders.xml", ORDERS_XML))
    by_name = {f.name: f for f in flows}
    assert set(by_name) == {"ordersFlow", "validateSub"}

    orders = by_name["ordersFlow"]
    assert orders.kind == "flow" and orders.file == "orders.xml"
    assert orders.refs == {"validateSub", "enrichSub"}             # incl. inside <choice>
    assert orders.connectors == {"http", "db", "ee"}              # core excluded

    sub = by_name["validateSub"]
    assert sub.kind == "sub-flow"                                 # kept as muleflow kind
    assert sub.connectors == {"db"} and sub.refs == set()


def test_parse_config_skips_non_mule_and_broken(tmp_path):
    # right place, wrong root tag -> not a Mule config
    other = _w(tmp_path / "src/main/mule", "spring.xml",
               '<beans xmlns="http://x"><bean id="a"/></beans>')
    assert parse_config(other) == []
    # malformed XML -> [] (never raises)
    broken = _w(tmp_path / "src/main/mule", "broken.xml", "<mule><flow name='x'>")
    assert parse_config(broken) == []
