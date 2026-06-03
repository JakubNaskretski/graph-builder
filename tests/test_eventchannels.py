"""Platform Event / CDC channel extractor.

Covers the channel node, the member binding channel -> object (selectedEntity),
and a member re-emitting its channel node so the edge forms without the channel
file present.
"""
from pathlib import Path

from graphbuilder import core, resolvers
from graphbuilder.extractors.eventchannels import EXTRACTORS, EventChannelExtractor

NS = 'xmlns="http://soap.sforce.com/2006/04/metadata"'


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


def _channel_xml(ctype="data"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<PlatformEventChannel {NS}>
  <channelType>{ctype}</channelType>
  <label>Sales Changes</label>
</PlatformEventChannel>
"""


def _member_xml(channel, entity):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<PlatformEventChannelMember {NS}>
  <eventChannel>{channel}</eventChannel>
  <selectedEntity>{entity}</selectedEntity>
</PlatformEventChannelMember>
"""


def test_registry_exposes_instance():
    assert len(EXTRACTORS) == 1
    assert isinstance(EXTRACTORS[0], EventChannelExtractor)


def test_handles_both_suffixes():
    ex = EventChannelExtractor()
    assert ex.handles(Path("Sales.platformEventChannel-meta.xml"))
    assert ex.handles(Path("Sales_Account.platformEventChannelMember-meta.xml"))
    assert not ex.handles(Path("Sales.object-meta.xml"))


def test_channel_node(tmp_path):
    p = tmp_path / "Sales_Changes__chn.platformEventChannel-meta.xml"
    _w(p, _channel_xml("data"))
    nodes, edges = EventChannelExtractor().extract(p)
    assert nodes == [{
        "id": "platformeventchannel/Sales_Changes__chn",
        "type": "platformeventchannel", "label": "Sales_Changes__chn",
        "channel_type": "data",
    }]
    assert edges == []


def test_member_binds_channel_to_object(tmp_path):
    p = tmp_path / "Sales_Acct.platformEventChannelMember-meta.xml"
    _w(p, _member_xml("Sales_Changes__chn", "MeterPoint__c"))
    nodes, edges = EventChannelExtractor().extract(p)
    assert {"id": "platformeventchannel/Sales_Changes__chn",
            "type": "platformeventchannel", "label": "Sales_Changes__chn"} in nodes
    assert edges == [{
        "src": "platformeventchannel/Sales_Changes__chn",
        "type": "references", "to_kind": "object", "to_name": "MeterPoint__c",
    }]


def test_member_without_channel_skipped(tmp_path):
    p = tmp_path / "Orphan.platformEventChannelMember-meta.xml"
    _w(p, _member_xml("", "MeterPoint__c"))
    assert EventChannelExtractor().extract(p) == ([], [])


def test_broken_xml_skipped_not_raised(tmp_path):
    p = tmp_path / "Bad.platformEventChannelMember-meta.xml"
    _w(p, "<PlatformEventChannelMember><eventChannel>")  # truncated
    assert EventChannelExtractor().extract(p) == ([], [])


def test_build_channel_and_member_resolve(tmp_path):
    _w(tmp_path / "Sales.platformEventChannel-meta.xml", _channel_xml())
    _w(tmp_path / "Sales_M.platformEventChannelMember-meta.xml",
       _member_xml("Sales", "MeterPoint__c"))
    gb = core.GraphBuilder().register(*EXTRACTORS)
    gb.register_resolver(*resolvers.default_resolvers())
    g = gb.build(tmp_path)
    # channel node deduped to one (channel file + member re-emit)
    chans = [n for n in g["nodes"] if n["type"] == "platformeventchannel"]
    assert len(chans) == 1 and chans[0].get("channel_type") == "data"
    assert {"src": "platformeventchannel/Sales",
            "dst": "object/MeterPoint__c", "type": "references"} in g["edges"]
    assert g["errors"] == []
