"""Agent query surface — find_nodes / node_text. Fictional fixtures only."""
from graphbuilder import find_nodes, node_text

G = {"nodes": [
        {"id": "object/MeterPoint__c", "type": "object", "label": "MeterPoint__c"},
        {"id": "object/Account", "type": "object", "label": "Account"},
        {"id": "apexclass/MeterPointService", "type": "apexclass", "label": "MeterPointService"},
        {"id": "page/ENG/Billing", "type": "page", "label": "Billing"},
     ], "edges": []}


def test_exact_match_scores_top():
    r = find_nodes(G, "MeterPointService")
    assert r[0]["id"] == "apexclass/MeterPointService" and r[0]["score"] == 1.0


def test_prefix_match():
    assert [x["id"] for x in find_nodes(G, "meterpoint", types=["object"])] == ["object/MeterPoint__c"]


def test_type_filter():
    assert all(x["type"] == "object" for x in find_nodes(G, "meter", types="object"))


def test_fuzzy_match():
    r = find_nodes(G, "meterpoit", types=["object"])
    assert r and r[0]["id"] == "object/MeterPoint__c"


def test_limit_caps_results():
    assert len(find_nodes(G, "meter", limit=1)) == 1


def test_no_match_is_empty():
    assert find_nodes(G, "zzzznothing") == []


def test_tolerant_of_garbage():
    assert find_nodes(None, "x") == []
    assert find_nodes(G, "") == []
    assert find_nodes({"nodes": [None, {"no": "id"}]}, "x") == []


def test_node_text_inline():
    assert node_text({"text": "hello"}) == "hello"


def test_node_text_from_pointer(tmp_path):
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "1.txt").write_text("body here", "utf-8")
    assert node_text({"content": "c/1.txt"}, root=tmp_path) == "body here"


def test_node_text_missing_or_bad(tmp_path):
    assert node_text({}, root=tmp_path) == ""
    assert node_text({"content": "nope.txt"}, root=tmp_path) == ""
    assert node_text("notadict") == ""
