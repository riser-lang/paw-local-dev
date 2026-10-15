"""Agnes Trusted Source Routing — AGNES_TRUSTED_SOURCE_ROUTING_V1 (Phase 4).

This is SOURCE READER ROUTING, not model routing. When Agnes needs project
material it consults sources in priority order:

    1. trusted Local Reader (via the Windows Bridge, when online & verified)
    2. fresh Cloud Source Mirror (server-side read cache — works offline)
    3. WPS Direct (via the Bridge)
    4. Workspace Knowledge / cached context (with freshness disclosure)

Hard rules:
- WPS unavailability must NEVER break the read: mirror/local keep working.
- No automatic canonical write. This module is read-only.
- Provenance + freshness are always returned so the caller (Agnes) can disclose
  "基于 <时间> 的镜像/本地副本，WPS 在线版本未核验" when appropriate.
"""

from __future__ import annotations

from pathlib import Path
import os


import json
import logging
import re
import time
from typing import Any, Callable, Optional

log = logging.getLogger("paw.source_routing")

DEVICE_ID = "office-pc-01"
JOB_TIMEOUT_SECONDS = 90

# Query tokens that hint the user wants project/source material.
PROJECT_HINT_RE = re.compile(
    r"(项目|资料|文件|来源|方案|汇报|清单|名单|汇总|继续推进|编译|读取)",
    re.IGNORECASE,
)


class SourceUnavailable(Exception):
    pass


# ── providers (injectable for tests) ─────────────────────────────────────

class MirrorProvider:
    """Cloud Source Mirror read provider (offline-safe)."""

    def __init__(self, mirror_root: Optional[str] = None) -> None:
        from brain.source_mirror import service as _mirror

        self._mirror = _mirror
        self.mirror_root = mirror_root or _mirror.DEFAULT_MIRROR_ROOT

    def available(self) -> bool:
        try:
            return self._mirror.summary(self.mirror_root).get("total_sources", 0) > 0
        except Exception:  # noqa: BLE001
            return False

    def search_text(self, query: str, max_chars: int = 60_000) -> Optional[dict]:
        """Find the freshest mirror entry whose text matches the query.

        Token-based matching: CJK bigrams extracted from the query are matched
        as substrings, so "查看人才发展相关资料" hits an entry containing
        "人才发展组工作梳理". Text is loaded per-entry (objects) — the manifest
        stores metadata only. Falls back to whole-query containment.
        """
        tokens = _cjk_tokens(query)
        best: Optional[dict] = None
        for entry in self._mirror.list_entries(self.mirror_root):
            location = entry.get("canonical_location")
            if not location:
                continue
            loaded = self._mirror.get_entry(self.mirror_root, location)
            if not loaded:
                continue
            text = loaded.get("text") or ""
            matched = False
            if query and query.casefold() in text.casefold():
                matched = True
            elif tokens:
                lowered = text.casefold()
                matched = any(tok in lowered for tok in tokens)
            if not matched:
                continue
            if best is None or (entry.get("mirrored_at") or "") > (best.get("mirrored_at") or ""):
                best = {**loaded, "text": text[:max_chars]}
        return best

    def get(self, canonical_location: str, max_chars: int = 60_000) -> Optional[dict]:
        entry = self._mirror.get_entry(self.mirror_root, canonical_location)
        if entry:
            entry["text"] = (entry.get("text") or "")[:max_chars]
        return entry

    def freshness(self, canonical_location: str, max_age_hours: float = 24.0) -> dict:
        return self._mirror.entry_freshness(self.mirror_root, canonical_location, max_age_hours=max_age_hours)


class BridgeJobProvider:
    """Windows Bridge read provider (local.* and wps.* via the bridge store).

    Reuses the server's in-process bridge store (same instance the WS loop
    uses); jobs are submitted and polled to completion, exactly like the
    existing WPS sync helper.
    """

    def __init__(self, store: Any = None, device_id: str = DEVICE_ID) -> None:
        self._store = store
        self.device_id = device_id

    def _store_ref(self) -> Any:
        if self._store is not None:
            return self._store
        try:
            from workspace_api import daily_sync as _ds
            return _ds._bridge_store
        except Exception:  # noqa: BLE001
            return None

    def available(self) -> bool:
        store = self._store_ref()
        if store is None:
            return False
        try:
            snapshot = store.snapshot()
            devices = snapshot.get("devices", []) if isinstance(snapshot, dict) else []
            return any(d.get("device_id") == self.device_id and d.get("connected") for d in devices)
        except Exception:  # noqa: BLE001
            return False

    def _run_job(self, operation: str, params: dict) -> dict:
        store = self._store_ref()
        if store is None:
            raise SourceUnavailable("bridge store unavailable")
        try:
            record = store.submit_job(target_device_id=self.device_id, operation=operation, params=params)
        except ValueError as exc:
            raise SourceUnavailable("bridge job submission failed") from exc
        deadline = time.time() + JOB_TIMEOUT_SECONDS
        view = None
        while time.time() < deadline:
            view = store.job_view(record.job_id)
            if view is None:
                raise SourceUnavailable("bridge job disappeared")
            if view.get("state") in ("succeeded", "failed", "cancelled"):
                break
            time.sleep(0.5)
        else:
            raise SourceUnavailable("bridge job timed out")
        if view.get("state") != "succeeded":
            raise SourceUnavailable("bridge job failed")
        return (view.get("result") or {}).get("data") or {}

    def local_search(self, query: str, limit: int = 5) -> Optional[dict]:
        try:
            data = self._run_job("local.search", {"query": query, "limit": limit, "content": False})
        except SourceUnavailable:
            return None
        items = data.get("items") or []
        return items[0] if items else None

    def local_read(self, root: str, relative_path: str, max_chars: int = 60_000) -> Optional[dict]:
        try:
            return self._run_job("local.read", {"root": root, "path": relative_path, "max_chars": max_chars})
        except SourceUnavailable:
            return None

    def wps_search(self, query: str, limit: int = 5) -> Optional[dict]:
        try:
            data = self._run_job("wps.search", {"query": query, "scope": "authorized", "limit": min(limit, 50)})
        except SourceUnavailable:
            return None
        items = data.get("items") or []
        return items[0] if items else None

    def wps_read(self, document_id: str, max_chars: int = 60_000) -> Optional[dict]:
        try:
            return self._run_job("wps.read", {"document_id": document_id, "max_chars": max_chars})
        except SourceUnavailable:
            return None


class WorkspaceCacheProvider:
    """Workspace Knowledge fallback (cached summaries, disclosed as stale)."""

    def __init__(self, db_path: str = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))) -> None:
        self.db_path = db_path

    def search_knowledge(self, query: str, limit: int = 5) -> list[dict]:
        import sqlite3

        hits = []
        try:
            con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            con.execute("PRAGMA query_only=ON")
            rows = con.execute(
                "SELECT title, summary, updated_at FROM knowledge_page WHERE title LIKE ? OR summary LIKE ? LIMIT ?",
                (f"%{query}%", f"%{query}%", limit),
            ).fetchall()
            for title, summary, updated_at in rows:
                hits.append({"title": title, "summary": (summary or "")[:400], "updated_at": updated_at})
            con.close()
        except Exception:  # noqa: BLE001
            pass
        return hits


# ── router ───────────────────────────────────────────────────────────────

def route_source_content(
    query: str,
    *,
    mirror: Optional[MirrorProvider] = None,
    bridge: Optional[BridgeJobProvider] = None,
    cache: Optional[WorkspaceCacheProvider] = None,
    max_chars: int = 60_000,
) -> dict:
    """Resolve project material with full provenance + freshness disclosure.

    Priority: local reader → cloud mirror → WPS direct → workspace cache.
    Returns:
      {available, content, provenance, disclosed}
    """
    query = (query or "").strip()
    if not query:
        return {"available": False, "content": "", "provenance": "none", "disclosed": "未提供查询关键词。"}

    mirror = mirror or MirrorProvider()
    bridge = bridge or BridgeJobProvider()
    cache = cache or WorkspaceCacheProvider()

    # 1. Local Reader (via bridge) — verified local copy, freshest
    hit = bridge.local_search(query, limit=5)
    if hit and hit.get("root") and hit.get("relative_path"):
        data = bridge.local_read(hit["root"], hit["relative_path"], max_chars=max_chars)
        if data and data.get("text"):
            return {
                "available": True,
                "content": data["text"],
                "provenance": "local_reader",
                "source": f"{hit.get('root')}:{hit.get('relative_path')}",
                "content_hash": hit.get("content_hash"),
                "disclosed": "基于本地同步副本（Windows 办公电脑）读取，未在线核验 WPS 原始版本。",
            }

    # 2. Cloud Source Mirror — fresh server-side cache (offline-safe)
    entry = mirror.search_text(query, max_chars=max_chars)
    if entry and entry.get("text"):
        fresh = mirror.freshness(entry["canonical_location"], max_age_hours=24)
        age = fresh.get("age_hours")
        return {
            "available": True,
            "content": entry["text"],
            "provenance": "cloud_mirror",
            "source": entry.get("canonical_location"),
            "content_hash": entry.get("content_hash"),
            "mirrored_at": entry.get("mirrored_at"),
            "disclosed": (
                f"当前基于 {entry.get('mirrored_at', '?')} 的云端来源镜像进行分析"
                f"（{entry.get('canonical_location')}），"
                + ("WPS 原始版本暂时无法在线核验。" if age is None or age > 24 else "镜像与本地同步内容一致。")
            ),
        }

    # 3. WPS Direct (via bridge)
    hit = bridge.wps_search(query, limit=5)
    if hit and hit.get("document_id"):
        data = bridge.wps_read(hit["document_id"], max_chars=max_chars)
        if data and data.get("content"):
            return {
                "available": True,
                "content": data["content"],
                "provenance": "wps_direct",
                "source": hit.get("title") or hit.get("document_id"),
                "disclosed": "已通过 WPS 在线读取原始版本，信息实时。",
            }

    # 4. Workspace Knowledge cache — disclosed as cached
    hits = cache.search_knowledge(query, limit=5)
    if hits:
        content = "\n\n".join(f"【{h['title']}】\n{h['summary']}" for h in hits)
        return {
            "available": True,
            "content": content[:max_chars],
            "provenance": "workspace_cache",
            "source": "; ".join(h["title"] for h in hits),
            "disclosed": "本地/镜像/WPS 均不可用，以下内容来自工作台已有知识缓存，可能不是最新版本。",
        }

    return {"available": False, "content": "", "provenance": "none", "disclosed": "未找到可用的项目资料来源。"}


def hints_project_material(message: str) -> bool:
    """Cheap signal: does this user message ask for project/source material?"""
    return bool(PROJECT_HINT_RE.search(message or ""))


def _cjk_tokens(message: str, max_tokens: int = 10) -> list[str]:
    """Extract CJK match tokens from a message (bounded).

    For each CJK run we emit consecutive 2-char bigrams (effective for
    Chinese substring matching) plus the run head. Bounded to avoid unbounded
    growth on long messages.
    """
    runs = re.findall(r"[\u4e00-\u9fff]{2,}", message or "")
    tokens: list[str] = []
    for run in runs:
        run = run.casefold()
        tokens.append(run[:6])
        for i in range(len(run) - 1):
            tokens.append(run[i:i + 2])
        if len(tokens) >= max_tokens:
            break
    return tokens[:max_tokens]


def _extract_query(message: str) -> str:
    """Compact query for source routing: first CJK run containing a project hint."""
    message = (message or "").strip()
    runs = re.findall(r"[\u4e00-\u9fff]{2,16}", message)
    if not runs:
        return message[:64]
    for run in runs:
        if PROJECT_HINT_RE.search(run):
            return run[:16]
    return runs[0][:16]


def source_context_for_message(message: str, max_chars: int = 30_000) -> str:
    """Build a compact source-context block for the Agnes system prompt.

    Only consults sources when the message plausibly asks for project material,
    keeping the common chat path fast and bridge-free. The routing query is a
    compact keyword extraction of the message, not the full sentence.
    """
    if not hints_project_material(message):
        return ""
    result = route_source_content(_extract_query(message), max_chars=max_chars)
    if not result.get("available"):
        return ""
    content = (result.get("content") or "").strip()
    if not content:
        return ""
    return (
        "\n【项目资料（来源路由）】\n"
        f"来源：{result.get('provenance')}（{result.get('source')}）\n"
        f"说明：{result.get('disclosed')}\n"
        f"内容：\n{content[:max_chars]}"
    )
