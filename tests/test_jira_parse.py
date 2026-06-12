"""Jira parser tests — wiki-markup scanners + issue envelope.

Fictional fixtures only (Acme / MeterPoint, project ACME, made-up user keys).
"""
import json
from pathlib import Path

from graphbuilder.jira.parse import JIssue, iter_mentions, iter_urls, parse_issue

ISSUE = {
    "id": "10001", "key": "ACME-101",
    "self": "https://jira.example.internal/rest/api/2/issue/10001",
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
        "priority": {"name": "High"},
        "resolution": {"name": "Fixed"},
        "labels": ["billing", "sync"],
        "components": [{"name": "Billing"}, {"name": "Sync Engine"}],
        "fixVersions": [{"name": "2026.06"}, {"name": "2026.07"}],
        "assignee": {"name": "msmith"},
        "reporter": {"name": "jdoe"},
        "project": {"key": "ACME", "name": "Acme Platform"},
        "created": "2026-05-20T09:00:00.000+0000",
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

# the Data Center greenhopper toString() shape for one sprint customfield entry
SPRINT_BLOB = ("com.atlassian.greenhopper.service.sprint.Sprint@6f8a"
               "[id=5,rapidViewId=2,state=ACTIVE,name=Sprint 7,"
               "startDate=2026-05-01T08:00:00.000Z,endDate=2026-05-15T08:00:00.000Z,"
               "completeDate=<null>,sequence=5,goal=Meter sync]")
FIELDS_MAP = {"customfield_10100": "Epic Link", "customfield_10101": "Sprint"}


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
    assert p.priority == "High" and p.resolution == "Fixed"
    assert p.labels == ["billing", "sync"]
    assert p.components == ["Billing", "Sync Engine"]
    assert p.fix_versions == ["2026.06", "2026.07"]
    assert p.assignee == "msmith" and p.reporter == "jdoe"
    assert ("Blocks", "ACME-102") in p.links and ("Relates", "OPS-7") in p.links
    assert p.subtasks == ["ACME-103"]
    assert p.mentions == ["jdoe", "abc-123"]
    assert p.created == "2026-05-20T09:00:00.000+0000"
    assert p.updated == "2026-06-01T10:00:00.000+0000"
    assert p.url == "https://jira.example.internal/browse/ACME-101"  # from `self`
    assert "MeterPoint__c" in p.text
    # urls: wiki link + bare URL from the description + the remote link
    assert any("display/ENG/MeterPoint+Sync" in u for u in p.urls)
    assert any("/lightning/o/MeterPoint__c" in u for u in p.urls)
    assert any("pageId=100" in u for u in p.urls)


def test_parse_issue_url_keeps_context_path_strips_query(tmp_path):
    data = {"key": "ACME-7", "self":
            "https://jira.example.internal:8443/jira/rest/api/2/issue/10500?expand=names"}
    p = parse_issue(_dump(tmp_path, data, "ACME-7.issue.json"))
    assert p.url == "https://jira.example.internal:8443/jira/browse/ACME-7"


def test_parse_issue_epic_and_sprints_from_fields_file(tmp_path):
    """_fields.json next to the dump resolves the Epic Link + Sprint customfields;
    both Data Center sprint value shapes (greenhopper string, dict) work."""
    (tmp_path / "_fields.json").write_text(json.dumps(FIELDS_MAP), "utf-8")
    data = {"key": "ACME-5", "fields": {
        "summary": "S", "customfield_10100": "ACME-50",
        "customfield_10101": [SPRINT_BLOB, {"id": 6, "name": "Sprint 8",
                                            "state": "FUTURE"}],
    }}
    p = parse_issue(_dump(tmp_path, data, "ACME-5.issue.json"))
    assert p.epic_key == "ACME-50"
    assert p.sprints == ["Sprint 7", "Sprint 8"]


def test_parse_issue_fields_file_in_dump_root(tmp_path):
    """The collector writes _fields.json at the dump ROOT while issues sit in
    per-project dirs — discovery must look one level up."""
    (tmp_path / "_fields.json").write_text(json.dumps(FIELDS_MAP), "utf-8")
    project_dir = tmp_path / "ACME"
    project_dir.mkdir()
    data = {"key": "ACME-6", "fields": {"customfield_10100": "ACME-50"}}
    p = parse_issue(_dump(project_dir, data, "ACME-6.issue.json"))
    assert p.epic_key == "ACME-50"


def test_parse_issue_explicit_fields_map_and_case_insensitive(tmp_path):
    data = {"key": "ACME-8", "fields": {"customfield_9": "ACME-50",
                                        "customfield_10": SPRINT_BLOB}}
    p = parse_issue(_dump(tmp_path, data, "ACME-8.issue.json"),
                    fields_map={"customfield_9": "EPIC LINK", "customfield_10": "sprint"})
    assert p.epic_key == "ACME-50" and p.sprints == ["Sprint 7"]


def test_parse_issue_missing_fields_file_tolerated(tmp_path):
    """No _fields.json -> the customfields are simply not resolvable: epic_key /
    sprints stay empty, everything else parses."""
    data = {"key": "ACME-9", "fields": {"summary": "S", "customfield_10100": "ACME-50",
                                        "customfield_10101": [SPRINT_BLOB]}}
    p = parse_issue(_dump(tmp_path, data, "ACME-9.issue.json"))
    assert p.epic_key == "" and p.sprints == [] and p.summary == "S"


def test_parse_issue_tolerates_minimal(tmp_path):
    p = parse_issue(_dump(tmp_path, {"key": "ACME-1"}, "ACME-1.issue.json"))
    assert p.key == "ACME-1" and p.links == [] and p.text == "" and p.urls == []
    assert p.priority == "" and p.resolution == "" and p.created == ""
    assert p.components == [] and p.fix_versions == [] and p.sprints == []
    assert p.epic_key == "" and p.url == ""        # no `self` -> no browse URL


def test_parse_issue_non_dict_returns_empty(tmp_path):
    p = parse_issue(_dump(tmp_path, ["not", "a", "dict"], "x.issue.json"))
    assert p == JIssue()


def test_parse_issue_null_description(tmp_path):
    data = {"key": "ACME-2", "fields": {"description": None, "summary": "S"}}
    p = parse_issue(_dump(tmp_path, data, "ACME-2.issue.json"))
    assert p.text == "" and p.summary == "S"
