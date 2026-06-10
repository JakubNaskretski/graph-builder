"""Jira parser tests — wiki-markup scanners + issue envelope.

Fictional fixtures only (Acme / MeterPoint, project ACME, made-up user keys).
"""
import json
from pathlib import Path

from graphbuilder.jira.parse import JIssue, iter_mentions, iter_urls, parse_issue

ISSUE = {
    "id": "10001", "key": "ACME-101",
    "fields": {
        "summary": "MeterPoint sync drops readings",
        "description": (
            "Sync fails for MeterPoint__c.\n"
            "cc [~jdoe] and [~accountid:abc-123]\n"
            "See [runbook|https://wiki.example.internal/display/ENG/MeterPoint+Sync] "
            "and https://acme.lightning.force.com/lightning/o/MeterPoint__c/list."
        ),
        "issuetype": {"name": "Bug"},
        "status": {"name": "Open"},
        "labels": ["billing", "sync"],
        "assignee": {"name": "msmith"},
        "reporter": {"name": "jdoe"},
        "project": {"key": "ACME", "name": "Acme Platform"},
        "updated": "2026-06-01T10:00:00.000+0000",
        "issuelinks": [
            {"type": {"name": "Blocks"}, "outwardIssue": {"key": "ACME-102"}},
            {"type": {"name": "Relates"}, "inwardIssue": {"key": "OPS-7"}},
        ],
        "subtasks": [{"key": "ACME-103"}],
    },
    "_remotelinks": [
        {"object": {"url": "https://wiki.example.internal/pages/viewpage.action?pageId=100",
                    "title": "Acme Overview"},
         "application": {"name": "Confluence"}},
    ],
}


def _dump(tmp_path: Path, data, name="ACME-101.issue.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), "utf-8")
    return p


def test_iter_mentions():
    assert iter_mentions("cc [~jdoe] then [~accountid:abc-123] end") == ["jdoe", "abc-123"]
    assert iter_mentions("") == [] and iter_mentions(None) == []


def test_iter_urls():
    urls = iter_urls("see [x|https://a.example/p?q=1] and https://b.example/y. done")
    assert "https://a.example/p?q=1" in urls
    assert "https://b.example/y" in urls          # trailing dot stripped


def test_parse_issue_envelope(tmp_path):
    p = parse_issue(_dump(tmp_path, ISSUE))
    assert p.key == "ACME-101" and p.id == "10001"
    assert p.project_key == "ACME" and p.project_name == "Acme Platform"
    assert p.summary.startswith("MeterPoint sync")
    assert p.issue_type == "Bug" and p.status == "Open"
    assert p.labels == ["billing", "sync"]
    assert p.assignee == "msmith" and p.reporter == "jdoe"
    assert ("Blocks", "ACME-102") in p.links and ("Relates", "OPS-7") in p.links
    assert p.subtasks == ["ACME-103"]
    assert p.mentions == ["jdoe", "abc-123"]
    assert p.updated == "2026-06-01T10:00:00.000+0000"
    assert "MeterPoint__c" in p.text
    # urls: wiki link + bare URL from the description + the remote link
    assert any("display/ENG/MeterPoint+Sync" in u for u in p.urls)
    assert any("/lightning/o/MeterPoint__c" in u for u in p.urls)
    assert any("pageId=100" in u for u in p.urls)


def test_parse_issue_tolerates_minimal(tmp_path):
    p = parse_issue(_dump(tmp_path, {"key": "ACME-1"}, "ACME-1.issue.json"))
    assert p.key == "ACME-1" and p.links == [] and p.text == "" and p.urls == []


def test_parse_issue_non_dict_returns_empty(tmp_path):
    p = parse_issue(_dump(tmp_path, ["not", "a", "dict"], "x.issue.json"))
    assert p == JIssue()


def test_parse_issue_null_description(tmp_path):
    data = {"key": "ACME-2", "fields": {"description": None, "summary": "S"}}
    p = parse_issue(_dump(tmp_path, data, "ACME-2.issue.json"))
    assert p.text == "" and p.summary == "S"
