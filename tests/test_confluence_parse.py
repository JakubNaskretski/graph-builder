"""Confluence parser tests — storage-format scanners + parse_page envelope.

Fictional fixtures only (Acme / MeterPoint, space ENG, made-up user keys).
"""
import json
from pathlib import Path

from graphbuilder.confluence.parse import (
    CPage, parse_page, iter_page_links, iter_attachment_refs,
    iter_user_mentions, iter_external_urls, body_text,
)

STORAGE = (
    '<p>Intro &amp; overview. See '
    '<ac:link><ri:page ri:content-title="Acme Platform" ri:space-key="ENG"/></ac:link> and '
    '<ac:link><ri:page ri:content-title="Local Page"/></ac:link>.</p>'
    '<p>File <ac:link><ri:attachment ri:filename="mapping.csv"/></ac:link>; '
    'image <ac:image><ri:attachment ri:filename="diagram.png"/></ac:image>.</p>'
    '<p>cc <ri:user ri:userkey="u-jdoe"/> and <ri:user ri:account-id="acc-9"/>.</p>'
    '<p>Link <a href="https://acme.lightning.force.com/lightning/o/MeterPoint__c/list">x</a>.</p>'
    '<ac:structured-macro ac:name="code"><ac:plain-text-body>'
    '<![CDATA[total = a + b;]]></ac:plain-text-body></ac:structured-macro>'
)


def test_iter_page_links():
    links = iter_page_links(STORAGE)
    assert ("Acme Platform", "ENG") in links     # space-qualified
    assert ("Local Page", "") in links           # same-space link omits the key


def test_iter_attachment_refs():
    assert iter_attachment_refs(STORAGE) == ["mapping.csv", "diagram.png"]


def test_iter_user_mentions_userkey_then_accountid():
    assert iter_user_mentions(STORAGE) == ["u-jdoe", "acc-9"]


def test_iter_external_urls():
    assert "https://acme.lightning.force.com/lightning/o/MeterPoint__c/list" in iter_external_urls(STORAGE)


def test_body_text_strips_tags_unescapes_keeps_cdata():
    t = body_text(STORAGE)
    assert "Intro & overview" in t        # &amp; unescaped
    assert "total = a + b" in t           # CDATA code content kept
    assert "<p>" not in t and "ri:page" not in t


def test_scanners_never_raise_on_broken_markup():
    for bad in ("", "<ri:page", "<<>>", '<ri:attachment ri:filename=', "&nbsp; <b>", None):
        assert isinstance(iter_page_links(bad), list)
        assert isinstance(iter_attachment_refs(bad), list)
        assert isinstance(iter_user_mentions(bad), list)
        assert isinstance(iter_external_urls(bad), list)
        assert isinstance(body_text(bad), str)


def _dump(tmp_path: Path, data) -> Path:
    p = tmp_path / "101.page.json"
    p.write_text(json.dumps(data), "utf-8")
    return p


def test_parse_page_envelope(tmp_path):
    data = {
        "id": "101", "title": "MeterPoint Sync", "space": {"key": "ENG"},
        "ancestors": [{"id": "100", "title": "Acme Platform"}],
        "version": {"number": 7, "by": {"userKey": "u-jdoe"}},
        "metadata": {"labels": {"results": [{"name": "integration"}, {"name": "architecture"}]}},
        "body": {"storage": {"value": STORAGE}},
        "_links": {"base": "https://wiki.example.internal", "webui": "/display/ENG/MeterPoint+Sync"},
    }
    p = parse_page(_dump(tmp_path, data))
    assert (p.id, p.title, p.space_key) == ("101", "MeterPoint Sync", "ENG")
    assert p.parent_id == "100" and p.parent_title == "Acme Platform"
    assert p.labels == ["integration", "architecture"]
    assert p.author == "u-jdoe" and p.version == 7
    assert p.url == "https://wiki.example.internal/display/ENG/MeterPoint+Sync"
    assert ("Acme Platform", "ENG") in p.links
    assert "mapping.csv" in p.attachments and "u-jdoe" in p.mentions
    assert any("MeterPoint__c" in u for u in p.urls)
    assert "overview" in p.body_text


def test_parse_page_tolerates_minimal(tmp_path):
    p = parse_page(_dump(tmp_path, {"id": "5", "title": "Bare"}))
    assert (p.id, p.title) == ("5", "Bare")
    assert p.space_key == "" and p.parent_id == "" and p.labels == [] and p.version == 0
    assert p.links == [] and p.body_text == ""


def test_parse_page_non_dict_returns_empty(tmp_path):
    f = tmp_path / "x.page.json"
    f.write_text("[]", "utf-8")
    assert parse_page(f) == CPage()


def test_username_mention_fallback_older_dc():
    # older Data Center exports carry ri:username instead of ri:userkey
    s = '<ri:user ri:username="jdoe"/> <ri:user ri:userkey="u-k" ri:username="ignored"/>'
    assert iter_user_mentions(s) == ["jdoe", "u-k"]   # userkey still wins when both present


CDATA_STORAGE = (
    '<p>Real link <ac:link><ri:page ri:content-title="Real Page"/></ac:link>.</p>'
    '<ac:structured-macro ac:name="code"><ac:plain-text-body><![CDATA['
    'example: <ri:page ri:content-title="Fake Page"/> '
    '<a href="https://x.lightning.force.com/lightning/o/Fake__c/list">x</a> CODEMARKER'
    ']]></ac:plain-text-body></ac:structured-macro>'
)


def test_cdata_content_not_scanned_for_references(tmp_path):
    """Example markup inside a code macro's CDATA must produce no reference —
    but the code text itself stays in body_text (it IS page content)."""
    p = tmp_path / "7.page.json"
    p.write_text(json.dumps({"id": "7", "title": "Code Doc", "space": {"key": "ENG"},
                             "body": {"storage": {"value": CDATA_STORAGE}}}), "utf-8")
    cp = parse_page(p)
    assert ("Real Page", "") in cp.links
    assert not any(t == "Fake Page" for t, _ in cp.links)    # CDATA example skipped
    assert not any("Fake__c" in u for u in cp.urls)          # CDATA URL skipped
    assert "CODEMARKER" in cp.body_text                      # code kept as text
