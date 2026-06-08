"""Confluence Data Center collector — pull space(s) into a local page dump.

Network I/O for the Confluence source lives ONLY here. It walks the REST
``content`` endpoint for one or more spaces and writes each page's raw API JSON to
``<out_dir>/<SPACEKEY>/<id>.page.json`` — exactly the shape that
:mod:`graphbuilder.confluence.parse` and the extractor expect. Dependency-free
(stdlib ``urllib``); the HTTP ``opener`` is injectable so the collector is testable
with no network.

Auth is a Personal Access Token sent as ``Authorization: Bearer <token>``
(Confluence 7.9+ Data Center/Server). The token is read from the
``CONFLUENCE_TOKEN`` environment variable by default and is NEVER accepted as a CLI
flag, logged, or written into the dump — keeping it out of shell history, the
process list, and any output.

Robustness mirrors the build: a per-space fetch failure stops *that* space and is
reported (never raised); a single un-writable page is skipped and reported. Only
caller-fixable setup problems (missing token / base URL) raise.

Confidentiality: the dump holds real page bodies. Write it only to a gitignored
location (e.g. ``confluence-dump/``); never commit or egress it.
"""
from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# One GET per listing page returns everything the extractor needs: storage body,
# hierarchy, space, version+author, labels.
_EXPAND = "body.storage,ancestors,space,version,history,metadata.labels"
_TOKEN_ENV = "CONFLUENCE_TOKEN"
_PER_PAGE = 50           # REST ``limit`` per request
_MAX_REQUESTS = 10_000   # hard cap on pagination requests per space (loop-safety)


class CollectError(RuntimeError):
    """A caller-fixable setup problem (missing token / base URL). Per-page and
    per-space fetch failures are NOT raised — they are skipped and reported in the
    returned summary."""


def _opener(insecure: bool):
    """Build a urllib opener. ``insecure=True`` disables TLS verification for
    self-signed on-prem certs (a knowing choice — documented, opt-in)."""
    if insecure:
        ctx = ssl._create_unverified_context()
        return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener()


def _safe(name: str) -> str:
    """Filesystem-safe space-dir segment (no traversal / odd chars)."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "_"


def _retry_after(exc) -> float:
    try:
        val = exc.headers.get("Retry-After")
        return float(val) if val else 0.0
    except Exception:
        return 0.0


def _get_json(opener, url, headers, timeout, sleep, retries=3):
    """GET ``url`` and parse JSON, retrying on 429 (honouring ``Retry-After``) and
    transient network errors with exponential backoff. Raises after ``retries``."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    last = None
    for attempt in range(retries + 1):
        try:
            resp = opener.open(req, timeout=timeout)
            raw = resp.read()
            return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 429 and attempt < retries:
                sleep(_retry_after(exc) or (2 ** attempt))
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            if attempt < retries:
                sleep(2 ** attempt)
                continue
            raise
    raise last  # pragma: no cover - loop always returns or raises above


def _collect_space(opener, base, space, out_dir, headers, per_page, timeout, sleep, summary):
    """Page through one space, writing each page dump. Returns count written."""
    start = written = requests_made = 0
    space_dir = out_dir / _safe(space)
    while requests_made < _MAX_REQUESTS:
        requests_made += 1
        q = urllib.parse.urlencode({
            "type": "page", "spaceKey": space,
            "start": start, "limit": per_page, "expand": _EXPAND,
        })
        try:
            payload = _get_json(opener, f"{base}/rest/api/content?{q}", headers, timeout, sleep)
        except Exception as exc:  # can't page further in this space — report, move on
            summary["errors"].append(
                {"space": space, "start": start, "error": f"{type(exc).__name__}: {exc}"})
            break
        results = payload.get("results") or []
        if not results:
            break
        for page in results:
            pid = str((page or {}).get("id") or "")
            if not pid:
                summary["skipped"].append({"space": space, "reason": "page with no id"})
                continue
            try:
                space_dir.mkdir(parents=True, exist_ok=True)
                (space_dir / f"{pid}.page.json").write_text(
                    json.dumps(page, ensure_ascii=False, indent=2), encoding="utf-8")
                written += 1
            except Exception as exc:
                summary["skipped"].append(
                    {"space": space, "id": pid, "error": f"{type(exc).__name__}: {exc}"})
        limit = payload.get("limit") or per_page
        start += len(results)
        if len(results) < limit:  # last page
            break
    return written


def collect(base_url, space_keys, out_dir, *, token=None, per_page=_PER_PAGE,
            insecure=False, opener=None, timeout=30, sleep=time.sleep) -> dict:
    """Collect Confluence page(s) for ``space_keys`` into ``out_dir``.

    ``base_url`` is the instance root (e.g. ``https://wiki.example.internal``);
    ``space_keys`` a key or iterable of keys. ``token`` defaults to
    ``$CONFLUENCE_TOKEN`` (never pass a real token as a positional/CLI value).
    Returns a summary ``{"spaces": {key: count}, "pages": N, "skipped": [...],
    "errors": [...]}`` — and never logs the token or page content.
    """
    token = token if token is not None else os.environ.get(_TOKEN_ENV, "")
    if not token:
        raise CollectError(f"no Confluence token: set ${_TOKEN_ENV} (never pass it as a flag)")
    if not base_url:
        raise CollectError("base_url is required, e.g. https://wiki.example.internal")
    base = str(base_url).rstrip("/")
    if isinstance(space_keys, str):
        space_keys = [space_keys]
    opener = opener or _opener(insecure)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    out_dir = Path(out_dir)

    summary = {"spaces": {}, "pages": 0, "skipped": [], "errors": []}
    for space in space_keys:
        space = str(space).strip()
        if not space:
            continue
        written = _collect_space(
            opener, base, space, out_dir, headers, per_page, timeout, sleep, summary)
        summary["spaces"][space] = written
        summary["pages"] += written
    return summary
