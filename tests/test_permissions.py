"""Custom Permission / Custom Notification Type extractor tests."""
from pathlib import Path

import graphbuilder.resolvers as resolvers
from graphbuilder.core import GraphBuilder
from graphbuilder.extractors.permissions import (
    EXTRACTORS,
    CustomNotificationTypeExtractor,
    CustomPermissionExtractor,
)

CP = CustomPermissionExtractor()
CN = CustomNotificationTypeExtractor()


def _w(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")
    return p


def _ids(nodes):
    return {n["id"]: n for n in nodes}


def _et(edges):
    return {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges}


# A custom permission that depends on two others via <requiredPermission>.
VIEW_METERS_CP = """<?xml version="1.0" encoding="UTF-8"?>
<CustomPermission xmlns="http://soap.sforce.com/2006/04/metadata">
    <isLicensed>false</isLicensed>
    <label>View Acme Meters</label>
    <requiredPermission>Acme_Manage_Meters</requiredPermission>
    <requiredPermission>Globex_Base_Access</requiredPermission>
</CustomPermission>
"""

# A custom permission with no dependencies.
MANAGE_METERS_CP = """<?xml version="1.0" encoding="UTF-8"?>
<CustomPermission xmlns="http://soap.sforce.com/2006/04/metadata">
    <isLicensed>false</isLicensed>
    <label>Manage Acme Meters</label>
</CustomPermission>
"""

NOTIF_TYPE = """<?xml version="1.0" encoding="UTF-8"?>
<CustomNotificationType xmlns="http://soap.sforce.com/2006/04/metadata">
    <customNotificationTypeName>Acme_Meter_Alert</customNotificationTypeName>
    <desktop>true</desktop>
    <mobile>true</mobile>
    <title>Meter Alert</title>
</CustomNotificationType>
"""


def test_handles_routing():
    assert CP.handles(Path("x/Acme_View_Meters.customPermission-meta.xml"))
    assert not CP.handles(Path("x/Acme_Meter_Alert.customNotificationType-meta.xml"))
    assert CN.handles(Path("x/Acme_Meter_Alert.customNotificationType-meta.xml"))
    assert not CN.handles(Path("x/Acme_View_Meters.customPermission-meta.xml"))
    assert not CP.handles(Path("x/MeterPointTrigger.trigger"))


def test_custom_permission_node_and_requires_edges():
    f = _w(Path("/tmp/_acme_perm/Acme_View_Meters.customPermission-meta.xml"),
           VIEW_METERS_CP)
    nodes, edges = CP.extract(f)
    ids = _ids(nodes)

    assert "custompermission/Acme_View_Meters" in ids
    n = ids["custompermission/Acme_View_Meters"]
    assert n["type"] == "custompermission"
    assert n["label"] == "Acme_View_Meters"          # name, NOT the XML <label> text

    et = _et(edges)
    assert ("custompermission/Acme_View_Meters", "requires",
            "custompermission", "Acme_Manage_Meters") in et
    assert ("custompermission/Acme_View_Meters", "requires",
            "custompermission", "Globex_Base_Access") in et


def test_custom_permission_no_required():
    f = _w(Path("/tmp/_acme_perm/Acme_Manage_Meters.customPermission-meta.xml"),
           MANAGE_METERS_CP)
    nodes, edges = CP.extract(f)
    assert "custompermission/Acme_Manage_Meters" in _ids(nodes)
    assert edges == []


def test_no_value_text_leaks_into_attrs():
    """Confidentiality: the display label / title text must never be emitted."""
    f = _w(Path("/tmp/_acme_perm/Acme_View_Meters.customPermission-meta.xml"),
           VIEW_METERS_CP)
    nodes, _ = CP.extract(f)
    blob = repr(nodes)
    assert "View Acme Meters" not in blob


def test_custom_notification_type_node():
    f = _w(Path("/tmp/_acme_notif/Acme_Meter_Alert.customNotificationType-meta.xml"),
           NOTIF_TYPE)
    nodes, edges = CN.extract(f)
    ids = _ids(nodes)

    assert "customnotificationtype/Acme_Meter_Alert" in ids
    n = ids["customnotificationtype/Acme_Meter_Alert"]
    assert n["type"] == "customnotificationtype"
    assert n["label"] == "Acme_Meter_Alert"
    assert edges == []
    # title / subject text must not leak
    assert "Meter Alert" not in repr(nodes)


def test_broken_xml_skipped_not_raised():
    """Odd/broken input keeps the node but emits no edges, never raises."""
    f = _w(Path("/tmp/_acme_perm/Acme_Bad.customPermission-meta.xml"),
           "<CustomPermission><requiredPermission>oops")  # malformed
    nodes, edges = CP.extract(f)
    assert "custompermission/Acme_Bad" in _ids(nodes)
    assert edges == []


def test_build_in_isolation_nodes_and_requires_edges():
    """Full build: in-repo `requires` target resolves to its real node, the
    off-repo target resolves to an external stub."""
    root = Path("/tmp/_acme_perm_build")
    _w(root / "Acme_View_Meters.customPermission-meta.xml", VIEW_METERS_CP)
    _w(root / "Acme_Manage_Meters.customPermission-meta.xml", MANAGE_METERS_CP)
    _w(root / "Acme_Meter_Alert.customNotificationType-meta.xml", NOTIF_TYPE)

    result = (
        GraphBuilder()
        .register(*EXTRACTORS)
        .register_resolver(*resolvers.default_resolvers())
        .build(root)
    )
    nids = {n["id"] for n in result["nodes"]}
    assert "custompermission/Acme_View_Meters" in nids
    assert "custompermission/Acme_Manage_Meters" in nids
    assert "customnotificationtype/Acme_Meter_Alert" in nids
    assert result["errors"] == []

    edges = {(e["src"], e["type"], e["dst"]) for e in result["edges"]}
    # Acme_Manage_Meters exists -> real node; Globex_Base_Access off-repo -> external stub.
    assert ("custompermission/Acme_View_Meters", "requires",
            "custompermission/Acme_Manage_Meters") in edges
    assert ("custompermission/Acme_View_Meters", "requires",
            "custompermission/Globex_Base_Access") in edges
    ids = {n["id"]: n for n in result["nodes"]}
    assert ids["custompermission/Globex_Base_Access"].get("external") is True
    assert [u for u in result["unresolved"] if u["to_kind"] == "custompermission"] == []
