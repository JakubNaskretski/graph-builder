"""Confluence collector tests — NO network (the HTTP opener is injected).

Fictional fixtures only; asserts pagination, robustness, and that the token is
read from the environment and never written to disk / the summary.
"""
import json
import urllib.error
import urllib.parse

import pytest

from graphbuilder.confluence.collect import collect, CollectError

NO_SLEEP = lambda *_a, **_k: None


class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b


class _FakeOpener:
    """Serves canned content with real start/limit pagination semantics. Canned
    items are keyed by space; blog posts (``"type": "blogpost"`` items) are served
    only to the blogpost listing, everything else to the page listing."""

    def __init__(self, pages_by_space):
        self.pages_by_space = pages_by_space
        self.urls = []

    def open(self, req, timeout=None):
        self.urls.append(req.full_url)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
        space, start, limit = q["spaceKey"][0], int(q["start"][0]), int(q["limit"][0])
        ctype = q.get("type", ["page"])[0]
        pool = [p for p in self.pages_by_space.get(space, [])
                if (p.get("type") or "page") == ctype]
        chunk = pool[start:start + limit]
        return _Resp({"results": chunk, "start": start, "limit": limit, "size": len(chunk)})


def _page(pid, version=None):
    p = {"id": pid, "title": f"P{pid}", "space": {"key": "ENG"}, "body": {"storage": {"value": "x"}}}
    if version is not None:
        p["version"] = {"number": version}
    return p


def test_collect_writes_pages_and_paginates(tmp_path):
    op = _FakeOpener({"ENG": [_page("1"), _page("2"), _page("3")]})
    summary = collect("https://wiki.example.internal/", "ENG", tmp_path,
                      token="tok", per_page=2, opener=op, sleep=NO_SLEEP)
    assert summary["pages"] == 3 and summary["spaces"]["ENG"] == 3
    assert (tmp_path / "ENG" / "1.page.json").exists()
    assert (tmp_path / "ENG" / "3.page.json").exists()
    page_lists = [u for u in op.urls if "type=page" in u]
    assert len(page_lists) == 2       # start=0 (2 results) then start=2 (1 result -> stop)
    assert any("type=blogpost" in u for u in op.urls)   # blog posts listed by default


def test_token_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CONFLUENCE_TOKEN", "env-tok")
    summary = collect("https://w", "ENG", tmp_path,
                      opener=_FakeOpener({"ENG": [_page("1")]}), sleep=NO_SLEEP)
    assert summary["pages"] == 1


def test_missing_token_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("CONFLUENCE_TOKEN", raising=False)
    with pytest.raises(CollectError):
        collect("https://w", "ENG", tmp_path, opener=_FakeOpener({}), sleep=NO_SLEEP)


def test_token_never_written_to_disk_or_summary(tmp_path):
    op = _FakeOpener({"ENG": [_page("1")]})
    summary = collect("https://w", "ENG", tmp_path, token="SUPER-SECRET", opener=op, sleep=NO_SLEEP)
    blob = (tmp_path / "ENG" / "1.page.json").read_text("utf-8") + json.dumps(summary)
    assert "SUPER-SECRET" not in blob


def test_page_without_id_is_skipped(tmp_path):
    op = _FakeOpener({"ENG": [{"title": "no id"}, _page("2")]})
    summary = collect("https://w", "ENG", tmp_path, token="t", opener=op, sleep=NO_SLEEP)
    assert summary["pages"] == 1
    assert any("no id" in s.get("reason", "") for s in summary["skipped"])


class _ErrOpener:
    def open(self, req, timeout=None):
        raise urllib.error.URLError("boom")


def test_space_fetch_error_reported_not_raised(tmp_path):
    summary = collect("https://w", "ENG", tmp_path, token="t", opener=_ErrOpener(), sleep=NO_SLEEP)
    assert summary["pages"] == 0
    assert summary["errors"] and summary["errors"][0]["space"] == "ENG"


class _429ThenOK:
    def __init__(self, payload):
        self.payload, self.calls = payload, 0

    def open(self, req, timeout=None):
        self.calls += 1
        if self.calls == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {"Retry-After": "0"}, None)
        return _Resp(self.payload)


def test_429_is_retried(tmp_path):
    op = _429ThenOK({"results": [_page("1")], "start": 0, "limit": 50, "size": 1})
    summary = collect("https://w", "ENG", tmp_path, token="t", opener=op, sleep=NO_SLEEP,
                      content_types="page")   # a bare string is normalised to a list
    assert summary["pages"] == 1 and op.calls == 2


class _503ThenOK(_429ThenOK):
    def open(self, req, timeout=None):
        self.calls += 1
        if self.calls == 1:
            raise urllib.error.HTTPError(req.full_url, 503, "Unavailable", {}, None)
        return _Resp(self.payload)


def test_transient_5xx_is_retried(tmp_path):
    op = _503ThenOK({"results": [_page("1")], "start": 0, "limit": 50, "size": 1})
    summary = collect("https://w", "ENG", tmp_path, token="t", opener=op, sleep=NO_SLEEP,
                      content_types="page")
    assert summary["pages"] == 1 and op.calls == 2 and summary["errors"] == []


def test_multiple_spaces(tmp_path):
    op = _FakeOpener({"ENG": [_page("1")], "OPS": [_page("2"), _page("3")]})
    summary = collect("https://w", ["ENG", "OPS"], tmp_path, token="t", opener=op, sleep=NO_SLEEP)
    assert summary["spaces"] == {"ENG": 1, "OPS": 2} and summary["pages"] == 3


def test_concurrent_spaces_match_sequential(tmp_path):
    import threading

    class _SafeOpener(_FakeOpener):
        def __init__(self, pages):
            super().__init__(pages)
            self._lock = threading.Lock()

        def open(self, req, timeout=None):
            with self._lock:
                return super().open(req, timeout)

    pages = {"ENG": [_page("1"), _page("2")], "OPS": [_page("3")], "DOC": [_page("4")]}
    seq = collect("https://w", ["ENG", "OPS", "DOC"], tmp_path / "s",
                  token="t", opener=_SafeOpener(pages), sleep=NO_SLEEP, max_workers=1)
    con = collect("https://w", ["ENG", "OPS", "DOC"], tmp_path / "c",
                  token="t", opener=_SafeOpener(pages), sleep=NO_SLEEP, max_workers=4)
    assert seq["spaces"] == con["spaces"] == {"ENG": 2, "OPS": 1, "DOC": 1}
    assert seq["pages"] == con["pages"] == 4


def test_blogposts_collected_alongside_pages(tmp_path):
    blog = {"id": "9", "type": "blogpost", "title": "News", "space": {"key": "ENG"},
            "body": {"storage": {"value": "b"}}}
    op = _FakeOpener({"ENG": [_page("1"), blog]})
    summary = collect("https://w", "ENG", tmp_path, token="t", opener=op, sleep=NO_SLEEP)
    assert summary["pages"] == 2
    assert json.loads((tmp_path / "ENG" / "9.page.json").read_text("utf-8"))["type"] == "blogpost"


def test_incremental_skips_unchanged_versions(tmp_path):
    op = _FakeOpener({"ENG": [_page("1", version=4), _page("2", version=1)]})
    first = collect("https://w", "ENG", tmp_path, token="t", opener=op, sleep=NO_SLEEP)
    assert first["pages"] == 2 and first["unchanged"] == 0
    mtime = (tmp_path / "ENG" / "1.page.json").stat().st_mtime_ns

    second = collect("https://w", "ENG", tmp_path, token="t", opener=op, sleep=NO_SLEEP)
    assert second["pages"] == 0 and second["unchanged"] == 2
    assert (tmp_path / "ENG" / "1.page.json").stat().st_mtime_ns == mtime  # untouched

    op.pages_by_space["ENG"][0] = _page("1", version=5)        # page 1 edited upstream
    third = collect("https://w", "ENG", tmp_path, token="t", opener=op, sleep=NO_SLEEP)
    assert third["pages"] == 1 and third["unchanged"] == 1


def test_versionless_payload_always_rewrites(tmp_path):
    op = _FakeOpener({"ENG": [_page("1")]})                     # no version info
    collect("https://w", "ENG", tmp_path, token="t", opener=op, sleep=NO_SLEEP)
    second = collect("https://w", "ENG", tmp_path, token="t", opener=op, sleep=NO_SLEEP)
    assert second["pages"] == 1 and second["unchanged"] == 0    # can't prove unchanged -> rewrite


def test_prune_removes_vanished_pages_after_complete_listing(tmp_path):
    op = _FakeOpener({"ENG": [_page("1", version=1), _page("2", version=1)]})
    collect("https://w", "ENG", tmp_path, token="t", opener=op, sleep=NO_SLEEP)
    op.pages_by_space["ENG"] = [_page("1", version=1)]          # page 2 deleted upstream
    summary = collect("https://w", "ENG", tmp_path, token="t", opener=op, sleep=NO_SLEEP)
    assert summary["pruned"] == ["2"]
    assert not (tmp_path / "ENG" / "2.page.json").exists()
    assert (tmp_path / "ENG" / "1.page.json").exists()


def test_prune_opt_out(tmp_path):
    op = _FakeOpener({"ENG": [_page("1", version=1), _page("2", version=1)]})
    collect("https://w", "ENG", tmp_path, token="t", opener=op, sleep=NO_SLEEP)
    op.pages_by_space["ENG"] = [_page("1", version=1)]
    summary = collect("https://w", "ENG", tmp_path, token="t", opener=op, sleep=NO_SLEEP, prune=False)
    assert summary["pruned"] == [] and (tmp_path / "ENG" / "2.page.json").exists()


class _FirstListThenErr(_FakeOpener):
    """Serves the first listing request, then hard-errors — an aborted space."""
    def open(self, req, timeout=None):
        if len(self.urls) >= 1:
            raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, None)
        return super().open(req, timeout)


def test_incomplete_listing_never_prunes_and_is_marked(tmp_path):
    full = _FakeOpener({"ENG": [_page("1", version=1), _page("2", version=1), _page("3", version=1)]})
    collect("https://w", "ENG", tmp_path, token="t", opener=full, sleep=NO_SLEEP)

    # next run lists only the first chunk (page 1+2), then aborts mid-pagination
    aborted = _FirstListThenErr({"ENG": [_page("1", version=1), _page("2", version=1)]})
    summary = collect("https://w", "ENG", tmp_path, token="t", opener=aborted,
                      sleep=NO_SLEEP, per_page=2)
    assert summary["incomplete"] == ["ENG"] and summary["pruned"] == []
    assert (tmp_path / "ENG" / "3.page.json").exists()          # NOT deleted on partial data
    assert (tmp_path / "ENG" / ".incomplete").exists()          # dump marked partial

    # a later complete run clears the marker (and may then prune)
    recovered = _FakeOpener({"ENG": [_page("1", version=1), _page("2", version=1), _page("3", version=1)]})
    collect("https://w", "ENG", tmp_path, token="t", opener=recovered, sleep=NO_SLEEP)
    assert not (tmp_path / "ENG" / ".incomplete").exists()
