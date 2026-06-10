"""Confluence Data Center collector — pull space(s) into a local page dump.

Network I/O for the Confluence source lives ONLY here. It walks the REST
``content`` endpoint for one or more spaces (pages + blog posts) and writes each
unit's raw API JSON to ``<out_dir>/<SPACEKEY>/<id>.page.json`` — exactly the shape
that :mod:`graphbuilder.confluence.parse` and the extractor expect. Collection is
incremental (unchanged versions are not rewritten; vanished ids are pruned after a
complete listing). Dependency-free (stdlib ``urllib``); the HTTP ``opener`` is
injectable so the collector is testable with no network.

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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# One GET per listing page returns everything the extractor needs: storage body,
# hierarchy, space, version+author, labels.
_EXPAND = "body.storage,ancestors,space,version,history,metadata.labels"
_TOKEN_ENV = "CONFLUENCE_TOKEN"
_PER_PAGE = 50           # REST ``limit`` per request
_MAX_REQUESTS = 10_000   # hard cap on pagination requests per space (loop-safety)
_CONTENT_TYPES = ("page", "blogpost")   # blog posts share the page dump shape
_RETRY_CODES = {429, 502, 503, 504}     # throttling + transient gateway errors
# Sentinel marking a space dir whose last collection aborted mid-pagination, so a
# later build/prune knows the dump may be missing pages.
_INCOMPLETE_MARK = ".incomplete"


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
    """GET ``url`` and parse JSON, retrying on 429/502/503/504 (honouring
    ``Retry-After``) and transient network errors with exponential backoff.
    Raises after ``retries``."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    last = None
    for attempt in range(retries + 1):
        try:
            resp = opener.open(req, timeout=timeout)
            raw = resp.read()
            return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in _RETRY_CODES and attempt < retries:
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


def _version_of(payload) -> int:
    """``version.number`` of a page payload / existing dump file content, 0 when
    missing or malformed (0 never counts as unchanged, so it always rewrites)."""
    try:
        return int(((payload or {}).get("version") or {}).get("number") or 0)
    except (TypeError, ValueError, AttributeError):
        return 0


def _existing_version(path: Path) -> int:
    try:
        return _version_of(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return 0


def _collect_space(opener, base, space, out_dir, headers, per_page, timeout, sleep,
                   content_types):
    """Page through one space (each content type in turn), writing each content
    dump. Returns a dict ``{written, unchanged, seen, complete, skipped, errors}``
    — its own state, so spaces can run on separate threads. Pagination stays
    sequential (the next ``start`` needs the prior response).

    Incremental: a page whose dump file already holds the same ``version.number``
    is left untouched (counted ``unchanged``), so a re-collect rewrites only what
    actually changed. ``seen`` holds every id listed this run (the prune input);
    ``complete`` is False when any type's pagination aborted on an error — then
    ``seen`` is partial and MUST NOT drive deletions.
    """
    written = unchanged = 0
    seen: set = set()
    complete = True
    skipped: list = []
    errors: list = []
    space_dir = out_dir / _safe(space)
    for ctype in content_types:
        start = requests_made = 0
        while requests_made < _MAX_REQUESTS:
            requests_made += 1
            q = urllib.parse.urlencode({
                "type": ctype, "spaceKey": space,
                "start": start, "limit": per_page, "expand": _EXPAND,
            })
            try:
                payload = _get_json(opener, f"{base}/rest/api/content?{q}", headers, timeout, sleep)
            except Exception as exc:  # can't page further for this type — report, move on
                errors.append({"space": space, "type": ctype, "start": start,
                               "error": f"{type(exc).__name__}: {exc}"})
                complete = False
                break
            results = payload.get("results") or []
            if not results:
                break
            for page in results:
                pid = str((page or {}).get("id") or "")
                if not pid:
                    skipped.append({"space": space, "reason": f"{ctype} with no id"})
                    continue
                seen.add(pid)
                target = space_dir / f"{pid}.page.json"
                new_version = _version_of(page)
                try:
                    if new_version > 0 and target.exists() \
                            and _existing_version(target) == new_version:
                        unchanged += 1
                        continue
                    space_dir.mkdir(parents=True, exist_ok=True)
                    target.write_text(
                        json.dumps(page, ensure_ascii=False, indent=2), encoding="utf-8")
                    written += 1
                except Exception as exc:
                    skipped.append({"space": space, "id": pid, "error": f"{type(exc).__name__}: {exc}"})
            limit = payload.get("limit") or per_page
            start += len(results)
            if len(results) < limit:  # last page of this type
                break
    return {"written": written, "unchanged": unchanged, "seen": seen,
            "complete": complete, "skipped": skipped, "errors": errors}


def _prune_space(space_dir: Path, seen: set) -> list:
    """Delete dump files for ids a COMPLETE listing no longer returned —
    deleted/moved pages would otherwise stay in the graph forever. Returns the
    pruned ids. Only ever touches ``*.page.json`` directly inside ``space_dir``."""
    pruned = []
    if not space_dir.is_dir():
        return pruned
    for f in sorted(space_dir.glob("*.page.json")):
        pid = f.name[: -len(".page.json")]
        if pid not in seen:
            try:
                f.unlink()
                pruned.append(pid)
            except OSError:
                pass
    return pruned


def collect(base_url, space_keys, out_dir, *, token=None, per_page=_PER_PAGE,
            insecure=False, opener=None, timeout=30, sleep=time.sleep, max_workers=None,
            content_types=_CONTENT_TYPES, prune=True) -> dict:
    """Collect Confluence content for ``space_keys`` into ``out_dir``.

    ``base_url`` is the instance root (e.g. ``https://wiki.example.internal``);
    ``space_keys`` a key or iterable of keys. ``token`` defaults to
    ``$CONFLUENCE_TOKEN`` (never pass a real token as a positional/CLI value).
    ``max_workers`` runs multiple spaces concurrently (default ``min(8, n_spaces)``);
    pagination within a space stays sequential. Output files are keyed by page id, so
    concurrency never changes the result.

    ``content_types`` selects what is collected (pages + blog posts by default —
    blog posts share the dump shape and graph as ``page`` nodes). Collection is
    **incremental**: an id whose dump already holds the same ``version.number`` is
    left untouched, and — with ``prune`` (default) — ids a COMPLETE listing no
    longer returns have their dump files deleted, so deleted/moved pages drop out
    of the next build. A space whose listing aborted mid-pagination is never
    pruned and is marked with a ``.incomplete`` sentinel file in its dump dir
    (removed on the next complete run) so downstream knows pages may be missing.

    Returns a summary ``{"spaces": {key: written}, "pages": N, "unchanged": N,
    "pruned": [ids...], "incomplete": [keys...], "skipped": [...], "errors": [...]}``
    — and never logs the token or page content.
    """
    token = token if token is not None else os.environ.get(_TOKEN_ENV, "")
    if not token:
        raise CollectError(f"no Confluence token: set ${_TOKEN_ENV} (never pass it as a flag)")
    if not base_url:
        raise CollectError("base_url is required, e.g. https://wiki.example.internal")
    base = str(base_url).rstrip("/")
    if isinstance(space_keys, str):
        space_keys = [space_keys]
    spaces = [s for s in (str(s).strip() for s in space_keys) if s]
    if isinstance(content_types, str):
        content_types = [content_types]
    opener = opener or _opener(insecure)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    out_dir = Path(out_dir)

    def _run(space):
        return _collect_space(opener, base, space, out_dir, headers, per_page,
                              timeout, sleep, content_types)

    workers = max_workers if max_workers else min(8, max(1, len(spaces)))
    if workers > 1 and len(spaces) > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {s: ex.submit(_run, s) for s in spaces}
            results = [(s, futures[s].result()) for s in spaces]   # gather in space order
    else:
        results = [(s, _run(s)) for s in spaces]

    summary = {"spaces": {}, "pages": 0, "unchanged": 0, "pruned": [],
               "incomplete": [], "skipped": [], "errors": []}
    for space, r in results:
        summary["spaces"][space] = r["written"]
        summary["pages"] += r["written"]
        summary["unchanged"] += r["unchanged"]
        summary["skipped"].extend(r["skipped"])
        summary["errors"].extend(r["errors"])
        space_dir = out_dir / _safe(space)
        mark = space_dir / _INCOMPLETE_MARK
        if r["complete"]:
            if prune:
                summary["pruned"].extend(_prune_space(space_dir, r["seen"]))
            mark.unlink(missing_ok=True)
        else:
            summary["incomplete"].append(space)
            if space_dir.is_dir():
                try:
                    mark.write_text("last collection aborted mid-listing; dump may be missing pages\n",
                                    encoding="utf-8")
                except OSError:
                    pass
    return summary
