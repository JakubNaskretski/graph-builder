"""Jira collector tests — NO network (the HTTP opener is injected).

Fictional fixtures only; asserts pagination, incremental behaviour, robustness,
and that the token is read from the environment and never written to disk.
"""
import json
import urllib.error
import urllib.parse

import pytest

from graphbuilder.jira.collect import collect, CollectError

NO_SLEEP = lambda *_a, **_k: None


class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b


class _FakeOpener:
    """Serves canned issues with real startAt/maxResults/total semantics, plus
    the /rest/api/2/field discovery list and per-issue remote links when the URL
    asks for them. Records every request's method + body for read-only asserts."""

    def __init__(self, issues_by_project, remotelinks=None, fields=None):
        self.issues_by_project = issues_by_project
        self.remotelinks = remotelinks or {}
        self.fields = fields or []          # canned /rest/api/2/field payload
        self.urls = []
        self.methods = []
        self.bodies = []

    def open(self, req, timeout=None):
        self.urls.append(req.full_url)
        self.methods.append(req.get_method())
        self.bodies.append(req.data)
        url = urllib.parse.urlparse(req.full_url)
        if url.path.endswith("/rest/api/2/field"):
            return _Resp(self.fields)
        if "/remotelink" in url.path:
            key = urllib.parse.unquote(url.path.split("/issue/")[1].split("/")[0])
            return _Resp(self.remotelinks.get(key, []))
        q = urllib.parse.parse_qs(url.query)
        project = q["jql"][0].split("=")[1].split("ORDER")[0].strip()
        start, limit = int(q["startAt"][0]), int(q["maxResults"][0])
        pool = self.issues_by_project.get(project, [])
        chunk = pool[start:start + limit]
        return _Resp({"issues": chunk, "startAt": start, "maxResults": limit,
                      "total": len(pool)})


def _issue(key, updated=None):
    i = {"id": key.split("-")[-1], "key": key,
         "fields": {"summary": f"S {key}", "project": {"key": key.split("-")[0]}}}
    if updated is not None:
        i["fields"]["updated"] = updated
    return i


def test_collect_writes_issues_and_paginates(tmp_path):
    op = _FakeOpener({"ACME": [_issue("ACME-1"), _issue("ACME-2"), _issue("ACME-3")]})
    summary = collect("https://jira.example.internal/", "ACME", tmp_path,
                      token="tok", per_page=2, opener=op, sleep=NO_SLEEP)
    assert summary["issues"] == 3 and summary["projects"]["ACME"] == 3
    assert (tmp_path / "ACME" / "ACME-1.issue.json").exists()
    assert (tmp_path / "ACME" / "ACME-3.issue.json").exists()
    # field discovery, then startAt=0 (2 of 3), then startAt=2 (total reached)
    assert len(op.urls) == 3


def test_token_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("JIRA_TOKEN", "env-tok")
    summary = collect("https://j", "ACME", tmp_path,
                      opener=_FakeOpener({"ACME": [_issue("ACME-1")]}), sleep=NO_SLEEP)
    assert summary["issues"] == 1


def test_missing_token_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("JIRA_TOKEN", raising=False)
    with pytest.raises(CollectError):
        collect("https://j", "ACME", tmp_path, opener=_FakeOpener({}), sleep=NO_SLEEP)


def test_bad_project_key_raises(tmp_path):
    with pytest.raises(CollectError):
        collect("https://j", 'ACME" OR 1=1', tmp_path, token="t",
                opener=_FakeOpener({}), sleep=NO_SLEEP)


def test_token_never_written_to_disk_or_summary(tmp_path):
    op = _FakeOpener({"ACME": [_issue("ACME-1")]},
                     fields=[{"id": "customfield_10100", "name": "Epic Link"},
                             {"id": "customfield_10101", "name": "Sprint"}])
    summary = collect("https://j", "ACME", tmp_path, token="SUPER-SECRET",
                      opener=op, sleep=NO_SLEEP)
    blob = (tmp_path / "ACME" / "ACME-1.issue.json").read_text("utf-8") \
        + (tmp_path / "_fields.json").read_text("utf-8") + json.dumps(summary)
    assert "SUPER-SECRET" not in blob


_FIELD_LIST = [  # canned /rest/api/2/field payload (fictional ids)
    {"id": "summary", "name": "Summary"},
    {"id": "customfield_10100", "name": "Epic Link"},
    {"id": "customfield_10101", "name": "Sprint"},
    {"id": "customfield_10200", "name": "Story Points"},
]


def test_field_discovery_get_only_and_fields_json_written(tmp_path):
    op = _FakeOpener({"ACME": [_issue("ACME-1")]}, fields=_FIELD_LIST)
    summary = collect("https://j", "ACME", tmp_path, token="t",
                      opener=op, sleep=NO_SLEEP)
    assert summary["errors"] == []
    # exactly one discovery request, before the search, via the GET helper:
    # every request the collector ever makes is a body-less GET
    field_urls = [u for u in op.urls if u.endswith("/rest/api/2/field")]
    assert len(field_urls) == 1 and op.urls[0] == field_urls[0]
    assert set(op.methods) == {"GET"} and set(op.bodies) == {None}
    # only the Epic Link / Sprint ids land in the map (Story Points is noise)
    mapping = json.loads((tmp_path / "_fields.json").read_text("utf-8"))
    assert mapping == {"customfield_10100": "Epic Link",
                       "customfield_10101": "Sprint"}
    # ...and the discovered ids are appended to the per-issue fields request
    q = urllib.parse.parse_qs(urllib.parse.urlparse(op.urls[1]).query)
    requested = q["fields"][0].split(",")
    assert {"customfield_10100", "customfield_10101"} <= set(requested)
    assert "customfield_10200" not in requested and "summary" in requested


def test_field_discovery_tolerates_absent_epic_and_sprint(tmp_path):
    op = _FakeOpener({"ACME": [_issue("ACME-1")]},
                     fields=[{"id": "summary", "name": "Summary"}])
    summary = collect("https://j", "ACME", tmp_path, token="t",
                      opener=op, sleep=NO_SLEEP)
    assert summary["issues"] == 1 and summary["errors"] == []
    assert json.loads((tmp_path / "_fields.json").read_text("utf-8")) == {}
    q = urllib.parse.parse_qs(urllib.parse.urlparse(op.urls[1]).query)
    assert "customfield" not in q["fields"][0]


def test_field_discovery_failure_degrades_not_fatal(tmp_path):
    class _FieldErrOpener(_FakeOpener):
        def open(self, req, timeout=None):
            if urllib.parse.urlparse(req.full_url).path.endswith("/rest/api/2/field"):
                raise urllib.error.HTTPError(req.full_url, 404, "nope", {}, None)
            return super().open(req, timeout)

    op = _FieldErrOpener({"ACME": [_issue("ACME-1")]})
    summary = collect("https://j", "ACME", tmp_path, token="t",
                      opener=op, sleep=NO_SLEEP)
    assert summary["issues"] == 1                      # issues still collected
    assert any(e.get("project") is None and "field discovery" in e["error"]
               for e in summary["errors"])
    assert not (tmp_path / "_fields.json").exists()    # nothing misleading written


def test_incremental_skips_unchanged_updated(tmp_path):
    op = _FakeOpener({"ACME": [_issue("ACME-1", "2026-06-01T10:00:00.000+0000")]})
    first = collect("https://j", "ACME", tmp_path, token="t", opener=op, sleep=NO_SLEEP)
    assert first["issues"] == 1 and first["unchanged"] == 0
    second = collect("https://j", "ACME", tmp_path, token="t", opener=op, sleep=NO_SLEEP)
    assert second["issues"] == 0 and second["unchanged"] == 1
    op.issues_by_project["ACME"][0] = _issue("ACME-1", "2026-06-02T09:00:00.000+0000")
    third = collect("https://j", "ACME", tmp_path, token="t", opener=op, sleep=NO_SLEEP)
    assert third["issues"] == 1 and third["unchanged"] == 0


def test_prune_removes_vanished_issues_after_complete_listing(tmp_path):
    op = _FakeOpener({"ACME": [_issue("ACME-1", "u1"), _issue("ACME-2", "u1")]})
    collect("https://j", "ACME", tmp_path, token="t", opener=op, sleep=NO_SLEEP)
    op.issues_by_project["ACME"] = [_issue("ACME-1", "u1")]     # ACME-2 deleted/moved
    summary = collect("https://j", "ACME", tmp_path, token="t", opener=op, sleep=NO_SLEEP)
    assert summary["pruned"] == ["ACME-2"]
    assert not (tmp_path / "ACME" / "ACME-2.issue.json").exists()


class _ErrOpener:
    def open(self, req, timeout=None):
        raise urllib.error.URLError("boom")


def test_project_fetch_error_reported_not_raised(tmp_path):
    summary = collect("https://j", "ACME", tmp_path, token="t",
                      opener=_ErrOpener(), sleep=NO_SLEEP)
    assert summary["issues"] == 0
    assert summary["errors"] and summary["errors"][0]["project"] == "ACME"
    assert summary["incomplete"] == ["ACME"]


def test_incomplete_never_prunes_and_is_marked(tmp_path):
    full = _FakeOpener({"ACME": [_issue("ACME-1", "u"), _issue("ACME-2", "u"),
                                 _issue("ACME-3", "u")]})
    collect("https://j", "ACME", tmp_path, token="t", opener=full, sleep=NO_SLEEP)

    class _FirstListThenErr(_FakeOpener):
        def open(self, req, timeout=None):
            if len(self.urls) >= 1:
                raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, None)
            return super().open(req, timeout)

    aborted = _FirstListThenErr({"ACME": [_issue("ACME-1", "u"), _issue("ACME-2", "u"),
                                          _issue("ACME-3", "u")]})
    summary = collect("https://j", "ACME", tmp_path, token="t", opener=aborted,
                      sleep=NO_SLEEP, per_page=2)
    assert summary["incomplete"] == ["ACME"] and summary["pruned"] == []
    assert (tmp_path / "ACME" / "ACME-3.issue.json").exists()
    assert (tmp_path / "ACME" / ".incomplete").exists()


def test_remote_links_merged_into_dump(tmp_path):
    op = _FakeOpener(
        {"ACME": [_issue("ACME-1", "u")]},
        remotelinks={"ACME-1": [{"object": {"url": "https://wiki.example.internal/x"}}]})
    collect("https://j", "ACME", tmp_path, token="t", opener=op, sleep=NO_SLEEP,
            remote_links=True)
    data = json.loads((tmp_path / "ACME" / "ACME-1.issue.json").read_text("utf-8"))
    assert data["_remotelinks"][0]["object"]["url"].endswith("/x")


def test_multiple_projects(tmp_path):
    op = _FakeOpener({"ACME": [_issue("ACME-1")], "OPS": [_issue("OPS-1"), _issue("OPS-2")]})
    summary = collect("https://j", ["ACME", "OPS"], tmp_path, token="t",
                      opener=op, sleep=NO_SLEEP)
    assert summary["projects"] == {"ACME": 1, "OPS": 2} and summary["issues"] == 3
