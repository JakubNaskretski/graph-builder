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
    per-issue remote links when the URL asks for them."""

    def __init__(self, issues_by_project, remotelinks=None):
        self.issues_by_project = issues_by_project
        self.remotelinks = remotelinks or {}
        self.urls = []

    def open(self, req, timeout=None):
        self.urls.append(req.full_url)
        url = urllib.parse.urlparse(req.full_url)
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
    assert len(op.urls) == 2          # startAt=0 (2 of 3) then startAt=2 (total reached)


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
    op = _FakeOpener({"ACME": [_issue("ACME-1")]})
    summary = collect("https://j", "ACME", tmp_path, token="SUPER-SECRET",
                      opener=op, sleep=NO_SLEEP)
    blob = (tmp_path / "ACME" / "ACME-1.issue.json").read_text("utf-8") + json.dumps(summary)
    assert "SUPER-SECRET" not in blob


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
