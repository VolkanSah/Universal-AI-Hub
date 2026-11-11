# =============================================================================
# app/skills_db.py
# Read-only access to the skill databases built by local_tools/vector_hub/secure_vectorizer.py
# Universal MCP Hub (Sandboxed) - based on PyFundaments Architecture
# Copyright 2026 - Volkan Kücükbudak
# Apache License V. 2 + ESOL 1.1
# =============================================================================
# LAYOUT (app/db/skills/):
#   core/core.db        → CORE: the main skill with all restrictions. Never exposed through tools.
#                         Optionally injected as system-prompt prefix (SKILLS_INJECT_CORE / use_core).
#   core/<other>.db     → AGENT: reference for agents (e.g. core_agent.db). Searchable, not injected.
#   user/*.db           → USER skills (autoload). Searchable; best chunks can be injected per
#                         request (SKILLS_INJECT_RELEVANT / use_skills).
#
# WHAT IS USED: FTS5 / BM25 keyword search (same query + weights as the vectorizer).
#   The optional semantic vectors (vec BLOB) are ignored — no embedding dependency in the hub.
#
# SAFETY:
#   - DBs are opened READ-ONLY (mode=ro); nothing here writes, imports or executes skill content.
#   - Skill text is plain data that ends up in a system prompt — only ship DBs you built yourself.
#   - A broken / foreign DB (wrong schema) is skipped with a warning — never a crash.
#   - SHIP DBs CHECKPOINTED: PRAGMA wal_checkpoint(TRUNCATE); PRAGMA journal_mode=DELETE;
#     and do not commit the *.db-wal / *.db-shm files.
# =============================================================================
import asyncio
import logging
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

logger = logging.getLogger("skills_db")

_ROOT   = Path(__file__).resolve().parent / "db" / "skills"
_TOKEN  = re.compile(r"\w+", re.UNICODE)
_NEEDED = {"chunks", "tags", "meta", "fts"}

_dbs: Dict[str, List[Path]] = {"core": [], "agent": [], "user": []}
_open_as: Dict[Path, tuple] = {}     # db path → (target, use_uri)
_core_text = ""
_active = False


# ---------------------------------------------------------------------------
# Open / validate
# ---------------------------------------------------------------------------

def _connect(path: Path) -> sqlite3.Connection:
    target, uri = _open_as[path]
    return sqlite3.connect(target, uri=uri)


def _validate(path: Path) -> bool:
    """Open read-only, check schema. WAL on a read-only dir → fall back to a private copy."""
    attempts = [(f"{path.resolve().as_uri()}?mode=ro", True), None]
    for attempt in attempts:
        if attempt is None:   # fallback: private temp copy (db + wal/shm), opened normally
            tmp = Path(tempfile.mkdtemp(prefix="skills_"))
            for suffix in ("", "-wal", "-shm"):
                src = Path(str(path) + suffix)
                if src.exists():
                    shutil.copy2(src, tmp / src.name)
            attempt = (str(tmp / path.name), False)
        try:
            c = sqlite3.connect(attempt[0], uri=attempt[1])
            names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
            if not _NEEDED <= names:
                c.close()
                logger.warning(f"Skills DB '{path.name}' skipped — not a vectorizer DB (missing {sorted(_NEEDED - names)}).")
                return False
            c.execute("SELECT 1 FROM chunks LIMIT 1").fetchall()
            c.execute("SELECT rowid FROM fts WHERE fts MATCH 'x' LIMIT 1").fetchall()   # FTS5 available?
            c.close()
            _open_as[path] = attempt
            return True
        except sqlite3.Error as e:
            last = type(e).__name__
    logger.warning(f"Skills DB '{path.name}' skipped — cannot open read-only ({last}).")
    return False


def initialize() -> None:
    """Scan app/db/skills/. Inactive or empty → stays inactive. Never raises."""
    global _active, _core_text
    cfg = config.get_skills()
    if str(cfg.get("SKILLS_ACTIVE", "false")).lower() != "true":
        logger.info("Skills DB not active in .pyfun [SKILLS] — skipped")
        return
    for k in _dbs:
        _dbs[k].clear()
    for path in sorted((_ROOT / "core").glob("*.db")) if (_ROOT / "core").is_dir() else []:
        if _validate(path):
            _dbs["core" if path.name == "core.db" else "agent"].append(path)
    for path in sorted((_ROOT / "user").glob("*.db")) if (_ROOT / "user").is_dir() else []:
        if _validate(path):
            _dbs["user"].append(path)

    # core text: all chunks of core.db, in order, capped
    try:
        cap = int(cfg.get("SKILLS_CORE_MAX_CHARS", "8000"))
    except ValueError:
        cap = 8000
    parts = []
    for p in _dbs["core"]:
        c = _connect(p)
        parts += [f"## {h}\n{t}" if h else t for h, t in c.execute("SELECT heading, text FROM chunks ORDER BY id")]
        c.close()
    _core_text = "\n\n".join(parts)[:cap]

    _active = any(_dbs.values())
    logger.info(f"Skills DB: core={len(_dbs['core'])} agent={len(_dbs['agent'])} user={len(_dbs['user'])} "
                f"(core text: {len(_core_text)} chars)")


def is_ready() -> bool:
    return _active


def core_text() -> str:
    """The always-on core skill (restrictions). For system-prompt injection only — never via tools."""
    return _core_text


# ---------------------------------------------------------------------------
# Search / catalog (sync, run in a thread by the async wrappers)
# ---------------------------------------------------------------------------

def _fts_query(q: str) -> str:
    toks = _TOKEN.findall((q or "").lower())
    return " OR ".join(f'"{t}"*' for t in toks)


def _scope_paths(scope: str) -> List[Path]:
    scope = (scope or "user").lower()
    if scope == "all":                      # core is deliberately NOT included
        return _dbs["agent"] + _dbs["user"]
    if scope not in ("user", "agent"):
        raise ValueError('scope must be "user", "agent" or "all"')
    return _dbs[scope]


def _search_sync(query: str, top_k: int, tag: str, scope: str) -> List[Dict[str, Any]]:
    tag = (tag or "").strip().lower() or None
    fq = _fts_query(query)
    if not fq and not tag:
        return []
    out: List[Dict[str, Any]] = []
    for path in _scope_paths(scope):
        c = _connect(path)
        try:
            if fq:
                sql, params = "SELECT rowid FROM fts WHERE fts MATCH ?", [fq]
                if tag:
                    sql += " AND rowid IN (SELECT c.id FROM chunks c JOIN tags t ON t.path=c.path WHERE t.tag=?)"
                    params.append(tag)
                sql += " ORDER BY bm25(fts, 5.0, 1.0) LIMIT ?"
            else:
                sql = ("SELECT id FROM chunks WHERE path IN (SELECT path FROM tags WHERE tag=?) "
                       "ORDER BY id LIMIT ?")
                params = [tag]
            params.append(top_k)
            for rank, (rid,) in enumerate(c.execute(sql, params).fetchall()):
                row = c.execute(
                    "SELECT c.path, c.heading, c.text, m.name FROM chunks c "
                    "LEFT JOIN meta m ON m.path = c.path WHERE c.id = ?", (rid,)).fetchone()
                if row:
                    out.append({"skill": row[0], "name": row[3] or row[0], "heading": row[1],
                                "text": row[2], "db": path.name, "rank": rank})
        finally:
            c.close()
    out.sort(key=lambda r: r["rank"])       # best of each DB first (BM25 rank per DB)
    return out[:top_k]


def _catalog_sync(scope: str, tag: str) -> List[Dict[str, Any]]:
    tag = (tag or "").strip().lower() or None
    out: List[Dict[str, Any]] = []
    for path in _scope_paths(scope):
        c = _connect(path)
        try:
            for p, name, desc in c.execute("SELECT path, name, description FROM meta ORDER BY path"):
                tags = [r[0] for r in c.execute("SELECT tag FROM tags WHERE path=? ORDER BY tag", (p,))]
                if tag and tag not in tags:
                    continue
                n = c.execute("SELECT COUNT(*) FROM chunks WHERE path=?", (p,)).fetchone()[0]
                out.append({"skill": p, "name": name, "description": desc, "tags": tags, "chunks": n, "db": path.name})
        finally:
            c.close()
    return out


async def search(query: str = "", top_k: int = 3, tag: str = "", scope: str = "user") -> List[Dict[str, Any]]:
    """BM25 search over skill chunks. scope: user (default) | agent | all. core is never searchable."""
    if not _active:
        raise RuntimeError("Skills DB not active — set SKILLS_ACTIVE = \"true\" in .pyfun [SKILLS]")
    top_k = max(1, min(int(top_k or 3), 10))
    return await asyncio.to_thread(_search_sync, query, top_k, tag, scope)


async def catalog(scope: str = "all", tag: str = "") -> List[Dict[str, Any]]:
    """List skills (name, description, tags, chunk count). core is never listed."""
    if not _active:
        raise RuntimeError("Skills DB not active — set SKILLS_ACTIVE = \"true\" in .pyfun [SKILLS]")
    return await asyncio.to_thread(_catalog_sync, scope, tag)


async def context_for(prompt: str, top_k: int = 3, max_chars: int = 3000) -> str:
    """Best matching USER skill chunks for a prompt, formatted for a system prompt ('' if none)."""
    if not _active or not _dbs["user"] or not (prompt or "").strip():
        return ""
    try:
        hits = await asyncio.to_thread(_search_sync, prompt, max(1, min(int(top_k), 10)), "", "user")
    except Exception as e:
        logger.warning(f"Skills lookup failed ({type(e).__name__}) — continuing without skills.")
        return ""
    parts, used = [], 0
    for h in hits:
        block = f"### {h['name']} — {h['heading']}\n{h['text']}" if h["heading"] else f"### {h['name']}\n{h['text']}"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return ("Relevant skills for this request:\n\n" + "\n\n".join(parts)) if parts else ""
