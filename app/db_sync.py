
# =============================================================================
# app/db_sync.py
# Internal SQLite IPC — app/* state & communication
# Universal MCP Hub (Sandboxed) - based on PyFundaments Architecture
# Copyright 2026 - Volkan Kücükbudak
# Apache License V. 2 + ESOL 1.1
# Repo: https://github.com/VolkanSah/Universal-MCP-Hub-sandboxed
# =============================================================================
# ARCHITECTURE NOTE:
#   This file lives exclusively in app/ and is ONLY started by app/app.py.
#   NO direct access to fundaments/*, .env, or Guardian (main.py).
#   DB path comes from app/.pyfun [DB_SYNC] → SQLITE_PATH via app/config.py.
#
# CRITICAL RULES:
#   - This is NOT postgresql.py — cloud DB is Guardian-only!
#   - db_sync ONLY manages its own tables (hub_state, tool_cache)
#   - NEVER touch Guardian tables (users, sessions) — those belong to user_handler.py
#   - SQLite path is shared with user_handler.py via SQLITE_PATH
#   - app/* modules call db_sync.write() / db_sync.read() — never aiosqlite directly
#
# TABLE OWNERSHIP:
#   users, sessions  → Guardian (fundaments/user_handler.py) — DO NOT TOUCH!
#   hub_state        → db_sync (app/* internal state)
#   tool_cache       → db_sync (app/* tool response cache)
#   persist_queue    → db_sync (restart-safe queue → PostgreSQL long-term memory)
#   usage_log        → db_sync (tokens + estimated cost per LLM call)
# =============================================================================

import asyncio
import re
import time
import aiosqlite
from pathlib import Path
import logging
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from . import config

logger = logging.getLogger("db_sync")

# =============================================================================
# Internal State
# =============================================================================
_db_path: Optional[str] = None
_initialized: bool = False


# =============================================================================
# Initialization — called by app/app.py (parameterless, sandboxed)
# =============================================================================

async def initialize() -> None:
    global _db_path, _initialized

    if _initialized:
        return

    db_cfg   = config.get_db_sync()
    raw_path = db_cfg.get("SQLITE_PATH", "app/.hub_state.db")
    
    # HF Spaces: SPACE_ID is set → filesystem is read-only except /tmp/
    import os
    if os.getenv("SPACE_ID"):
        filename = os.path.basename(raw_path)
        _db_path = f"/tmp/{filename}"
        logger.info(f"HF Space detected — SQLite relocated to {_db_path}")
    else:
        _db_path = raw_path

    await _init_tables()

    _initialized = True
    logger.info(f"db_sync initialized — path: {_db_path}")


# =============================================================================
# SECTION 1 — Table Setup (app/* tables only!)
# =============================================================================

async def _init_tables() -> None:
    """
    Creates app/* internal tables if they don't exist.
    NEVER modifies Guardian tables (users, sessions).
    """
    async with aiosqlite.connect(_db_path) as db:

        # hub_state — generic key/value store for app/* modules
        await db.execute("""
            CREATE TABLE IF NOT EXISTS hub_state (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TEXT
            )
        """)

        # tool_cache — cached tool responses to reduce API calls
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tool_cache (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_name  TEXT NOT NULL,
                prompt     TEXT NOT NULL,
                response   TEXT NOT NULL,
                provider   TEXT,
                model      TEXT,
                created_at TEXT
            )
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_tool_cache_tool
            ON tool_cache(tool_name)
        """)

        # persist_queue — Dauer-Gedächtnis-Queue: überlebt Restarts,
        # bis der Eintrag erfolgreich nach PostgreSQL geflusht wurde.
        # score-Spalte = reservierter Slot für den Bullshit-Filter (Nutzer-eigenes
        # Scoring, später) — wird von db_sync NICHT interpretiert.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS persist_queue (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                target_table TEXT NOT NULL,
                payload      TEXT NOT NULL,
                source       TEXT,
                model        TEXT,
                score        REAL,
                queued_at    TEXT NOT NULL,
                attempts     INTEGER NOT NULL DEFAULT 0,
                last_error   TEXT
            )
        """)

        # usage_log — one row per successful LLM call (tokens + estimated cost).
        # cost_usd is NULL when no price is known. Read-only for everything else.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS usage_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         TEXT NOT NULL,
                tool       TEXT,
                provider   TEXT,
                model      TEXT,
                tokens_in  INTEGER,
                tokens_out INTEGER,
                cost_usd   REAL,
                latency_ms INTEGER
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_usage_log_ts ON usage_log(ts)
        """)

        await db.commit()

    logger.info("db_sync tables ready.")


# =============================================================================
# SECTION 2 — Key/Value Store (hub_state table)
# =============================================================================

async def write(key: str, value: Any) -> None:
    """
    Write a value to hub_state key/value store.
    Value is JSON-serialized — supports dicts, lists, strings, numbers.

    Args:
        key:   Unique key string (e.g. 'scheduler.last_run').
        value: Any JSON-serializable value.
    """
    _check_init()
    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(_db_path) as db:
        await db.execute("""
            INSERT OR REPLACE INTO hub_state (key, value, updated_at)
            VALUES (?, ?, ?)
        """, (key, json.dumps(value), now))
        await db.commit()


async def read(key: str, default: Any = None) -> Any:
    """
    Read a value from hub_state key/value store.
    Returns default if key does not exist.

    Args:
        key:     Key string to look up.
        default: Default value if key not found. Default: None.

    Returns:
        Deserialized value, or default if not found.
    """
    _check_init()

    async with aiosqlite.connect(_db_path) as db:
        cursor = await db.execute(
            "SELECT value FROM hub_state WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()

    if row is None:
        return default

    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return row[0]


async def delete(key: str) -> None:
    """
    Delete a key from hub_state.

    Args:
        key: Key string to delete.
    """
    _check_init()

    async with aiosqlite.connect(_db_path) as db:
        await db.execute("DELETE FROM hub_state WHERE key = ?", (key,))
        await db.commit()


# =============================================================================
# SECTION 3 — Tool Cache (tool_cache table)
# =============================================================================

async def cache_write(
    tool_name: str,
    prompt: str,
    response: str,
    provider: str = None,
    model: str = None,
) -> None:
    """
    Cache a tool response to reduce redundant API calls.

    Args:
        tool_name: Tool name (e.g. 'llm_complete', 'web_search').
        prompt:    The input prompt/query that was used.
        response:  The response to cache.
        provider:  Provider name used (optional).
        model:     Model name used (optional).
    """
    _check_init()

    db_cfg      = config.get_db_sync()
    max_entries = int(db_cfg.get("MAX_CACHE_ENTRIES", "1000"))
    now         = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(_db_path) as db:
        await db.execute("""
            INSERT INTO tool_cache (tool_name, prompt, response, provider, model, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (tool_name, prompt, response, provider, model, now))

        # Enforce MAX_CACHE_ENTRIES — delete oldest if exceeded
        await db.execute("""
            DELETE FROM tool_cache WHERE id NOT IN (
                SELECT id FROM tool_cache ORDER BY created_at DESC LIMIT ?
            )
        """, (max_entries,))

        await db.commit()


async def cache_read(tool_name: str, prompt: str, max_age_sec: int = 0) -> Optional[str]:
    """
    Read a cached tool response.
    Returns None if no cache entry exists (or it is older than max_age_sec).

    Args:
        tool_name:   Tool name to look up.
        prompt:      The exact prompt/query to match.
        max_age_sec: Ignore entries older than this (0 = no age limit).

    Returns:
        Cached response string, or None if not found.
    """
    _check_init()

    cutoff = ""
    if max_age_sec and max_age_sec > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max_age_sec)).isoformat()

    async with aiosqlite.connect(_db_path) as db:
        cursor = await db.execute("""
            SELECT response FROM tool_cache
            WHERE tool_name = ? AND prompt = ? AND created_at >= ?
            ORDER BY created_at DESC LIMIT 1
        """, (tool_name, prompt, cutoff))
        row = await cursor.fetchone()

    return row[0] if row else None


# =============================================================================
# SECTION 4 — Read-Only Query (for mcp.py db_query tool)
# =============================================================================

# Tables db_query must never touch (Guardian-owned) + SQLite internals.
# Identifiers must appear literally in SQL, so a word-boundary check is reliable.
_QUERY_BLOCKED = re.compile(
    r"\b(users|sessions|sqlite_master|sqlite_schema|sqlite_temp_master|pragma|attach|detach)\b",
    re.IGNORECASE,
)
_QUERY_MAX_ROWS = 1000


async def query(sql: str) -> List[Dict]:
    """
    Execute a read-only SELECT query on the internal hub state database.
    Called by mcp.py db_query tool when db_sync.py is active.

    Hardening (the docstring used to promise more than the code did):
      - exactly ONE statement, must start with SELECT
      - Guardian tables (users, sessions) + SQLite internals are rejected
      - connection is opened read-only at engine level (mode=ro) — writes are impossible
      - result is capped at 1000 rows

    Args:
        sql: SQL SELECT statement to execute.

    Returns:
        List of result rows as dicts.

    Raises:
        ValueError: If the query is not an allowed SELECT statement.
    """
    _check_init()

    stmt = sql.strip()
    if stmt.endswith(";"):
        stmt = stmt[:-1].rstrip()
    if not stmt.upper().startswith("SELECT"):
        raise ValueError("Only SELECT queries are permitted in db_query tool.")
    if ";" in stmt:
        raise ValueError("Only a single statement is permitted in db_query tool.")
    if _QUERY_BLOCKED.search(stmt):
        raise ValueError("Query touches a protected table or SQLite internals.")

    ro_uri = Path(_db_path).resolve().as_uri() + "?mode=ro"
    async with aiosqlite.connect(ro_uri, uri=True) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(stmt)
        rows   = await cursor.fetchmany(_QUERY_MAX_ROWS)
        return [dict(r) for r in rows]


# =============================================================================
# SECTION 4b — Usage / Cost Log (usage_log table)
# =============================================================================

async def log_usage(
    tool: str,
    provider: str,
    model: str,
    tokens_in: Optional[int],
    tokens_out: Optional[int],
    cost_usd: Optional[float],
    latency_ms: Optional[int] = None,
) -> None:
    """Append one usage row. Called by providers.py after every successful LLM call."""
    _check_init()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(_db_path) as db:
        await db.execute("""
            INSERT INTO usage_log (ts, tool, provider, model, tokens_in, tokens_out, cost_usd, latency_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (now, tool, provider, model, tokens_in, tokens_out, cost_usd, latency_ms))
        await db.commit()


_USAGE_GROUPS = {"model": "model", "provider": "provider", "tool": "tool", "day": "substr(ts, 1, 10)"}


async def usage_stats(group_by: str = "model", days: Optional[int] = None) -> Dict[str, Any]:
    """
    Aggregate usage_log.

    Args:
        group_by: "model" | "provider" | "tool" | "day"
        days:     Only count the last N days (None = all time).

    Returns:
        {"group_by", "days", "rows": [...], "total": {...}}
        cost_usd is the sum of KNOWN costs; calls_unpriced counts calls without a price.
    """
    _check_init()
    col = _USAGE_GROUPS.get(str(group_by).lower())
    if not col:
        raise ValueError(f"group_by must be one of {sorted(_USAGE_GROUPS)}")

    where, args = "", []
    if days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()
        where, args = "WHERE ts >= ?", [cutoff]

    select = """
        COUNT(*)                                         AS calls,
        COALESCE(SUM(tokens_in), 0)                      AS tokens_in,
        COALESCE(SUM(tokens_out), 0)                     AS tokens_out,
        ROUND(COALESCE(SUM(cost_usd), 0), 6)             AS cost_usd,
        SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) AS calls_unpriced
    """
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        cur  = await db.execute(
            f"SELECT {col} AS grp, {select} FROM usage_log {where} GROUP BY grp ORDER BY cost_usd DESC, calls DESC",
            args)
        rows = [dict(r) for r in await cur.fetchall()]
        cur  = await db.execute(f"SELECT {select} FROM usage_log {where}", args)
        total = dict(await cur.fetchone())

    return {"group_by": group_by, "days": days, "rows": rows, "total": total}


# =============================================================================
# SECTION 5 — Helpers
# =============================================================================

def _check_init() -> None:
    """Raise RuntimeError if db_sync was not initialized."""
    if not _initialized or not _db_path:
        raise RuntimeError("db_sync not initialized — call initialize() first.")


def is_ready() -> bool:
    """Returns True if db_sync is initialized and ready."""
    return _initialized and _db_path is not None


# =============================================================================
# SECTION 6 — Memory Orchestration: SQLite (Kurzzeit) → PostgreSQL (Dauer)
# =============================================================================
# ARCHITECTURE (Gedächtnis-Design):
#   SQLite (hub_state / tool_cache / persist_queue) = Kurzzeit-Gedächtnis.
#   Alle app/* Tools lesen/schreiben hier — immer verfügbar, kein Key nötig.
#   PostgreSQL = Dauer-Gedächtnis — WICHTIGE Dinge wandern dorthin.
#
#   persist_result Tool → enqueue_persist()   (sofort, immer, kein Crash)
#                          ↓
#                    persist_queue (SQLite, überlebt Restarts)
#                          ↓
#            flush_persist_queue() → PostgreSQL (Guardian-Writer)
#            (Background-Retry bis die DB da ist — siehe start_persist_flusher)
#
#   Delivery-Semantik: at-least-once (PG-Insert + Queue-Delete sind nicht
#   atomar über zwei Systeme). Für ein Gedächtnis ist ein seltener Duplikat-
#   Eintrag unkritisch — Datenverlust wäre es.
#
#   HF-Spaces-Hinweis: SPACE_ID verschiebt SQLite nach /tmp — Queue überbrückt
#   dort DB-Ausfälle innerhalb der Container-Lebenszeit; dauerhaft ist nur PG.
# =============================================================================
_psql_writer = None
_flush_task: Optional[asyncio.Task] = None
_pg_tables_ensured: set = set()

# Defense: Tabellennamen kommen aus .pyfun, aber wir validieren trotzdem.
_SAFE_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Ziel-Schema in PostgreSQL — source/model/score als eigene Spalten,
# damit dein Bullshit-Filter später direkt auf score filtern kann.
_PG_DDL = """
    CREATE TABLE IF NOT EXISTS {table} (
        id          BIGSERIAL PRIMARY KEY,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        source      TEXT,
        model       TEXT,
        score       REAL,
        payload     JSONB NOT NULL
    )
"""


def set_psql_writer(writer_fn) -> None:
    """
    Receives the asyncpg execute callable from Guardian via app.py.
    Called once in start_application() if db_service is available.
    app/* never imports postgresql.py directly — this is the only bridge.
    """
    global _psql_writer
    _psql_writer = writer_fn
    _pg_tables_ensured.clear()  # neue Connection → CREATE TABLE erneut sicherstellen
    logger.info("PostgreSQL writer registered.")


async def enqueue_persist(
    table: str,
    payload: Any,
    source: str = "",
    model: str = "",
    score: Optional[float] = None,
) -> int:
    """
    Queue an entry for the PostgreSQL long-term memory.
    ALWAYS works (no writer needed) — this is the 'no crash' guarantee:
    persist_result ohne DATABASE_URL = Eintrag wartet restart-safe in der Queue.

    Args:
        table:   Target table in PostgreSQL (from .pyfun target_table).
        payload: JSON-serializable data to persist.
        source:  Which tool/component produced this entry.
        model:   Which model produced this entry.
        score:   Optional quality score — reserved slot for the bullshit filter.

    Returns:
        Queue depth after insert (int).
    """
    _check_init()
    max_q = int(config.get_db_sync().get("PERSIST_QUEUE_MAX", "500"))
    now  = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            """INSERT INTO persist_queue
               (target_table, payload, source, model, score, queued_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (table, json.dumps(payload, ensure_ascii=False, default=str),
             source, model, score, now),
        )
        # Queue begrenzen — älteste zuerst raus (gleiche Regel wie tool_cache)
        await db.execute(
            "DELETE FROM persist_queue WHERE id NOT IN "
            "(SELECT id FROM persist_queue ORDER BY id DESC LIMIT ?)",
            (max_q,),
        )
        await db.commit()
        cursor = await db.execute("SELECT COUNT(*) FROM persist_queue")
        return int((await cursor.fetchone())[0])


async def _flush_locked(limit: int = 50) -> Dict[str, Any]:
    """
    Try to move queued entries to PostgreSQL. NEVER raises.
    (Single-flight: always called through flush_persist_queue(), which holds the lock.)

    No writer  → no-op with reason (entries keep waiting — no crash).
    Writer set → CREATE TABLE IF NOT EXISTS + INSERT per entry + queue delete.
    Failure    → attempts+1 + sanitized last_error, entry stays queued.

    Args:
        limit: Max entries per flush run (bounded work per interval).

    Returns:
        Dict: {flushed, errors, remaining, reason}
    """
    _check_init()

    if not _psql_writer:
        return {
            "flushed": 0, "errors": 0,
            "remaining": await _queue_count(),
            "reason": "no_postgres_writer",
        }

    # Neon asleep / unreachable a moment ago → don't even try (entries stay queued, nothing blocks)
    if time.monotonic() < _pg_down_until:
        return {
            "flushed": 0, "errors": 0,
            "remaining": await _queue_count(),
            "reason": "pg_backoff",
        }

    flushed = errors = 0
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, target_table, payload, source, model, score "
            "FROM persist_queue ORDER BY id ASC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()

        for row in rows:
            table = row["target_table"]
            try:
                if not _SAFE_TABLE_NAME.match(table or ""):
                    raise ValueError("invalid table name")
                if table not in _pg_tables_ensured:
                    await _pg_exec(_PG_DDL.format(table=table))
                    _pg_tables_ensured.add(table)
                await _pg_exec(
                    f"INSERT INTO {table} (source, model, score, payload) "
                    "VALUES ($1, $2, $3, $4::jsonb)",
                    row["source"], row["model"], row["score"], row["payload"],
                )
                await db.execute("DELETE FROM persist_queue WHERE id = ?", (row["id"],))
                await db.commit()
                flushed += 1
            except Exception as e:
                errors += 1
                # Sanitized: nur Exception-TYP — niemals DSN/Payload in Logs oder DB
                await db.execute(
                    "UPDATE persist_queue SET attempts = attempts + 1, last_error = ? "
                    "WHERE id = ?",
                    (type(e).__name__, row["id"]),
                )
                await db.commit()
                if _is_conn_failure(e):
                    break   # DB unreachable: stop here — don't burn one timeout per remaining row

    remaining = await _queue_count()
    if flushed:
        logger.info(f"Persist flush: {flushed} → PostgreSQL, {remaining} queued.")
    if errors:
        logger.warning(f"Persist flush: {errors} failed — stay queued (retry next interval).")
    return {"flushed": flushed, "errors": errors, "remaining": remaining}


# =============================================================================
# SECTION 6b — Neon / PostgreSQL resilience (scale-to-zero friendly)
# Neon suspends idle compute; the first connection afterwards can be slow or time out.
# Principle: the hub NEVER waits for PostgreSQL on a request path and never crashes.
#   - every PG write has a hard time limit (PG_WRITE_TIMEOUT_SEC)
#   - after a connection failure PG is skipped for PG_BACKOFF_SEC (entries stay queued)
#   - request path waits at most PG_REQUEST_WAIT_SEC; the flush continues in background
#   - pg_status() = cheap "is the DB awake?" ping for health checks (it also wakes Neon)
#   - optional keepalive (PG_KEEPALIVE_SEC, default 0 = off): keeps Neon awake, costs compute time
# All knobs in .pyfun [DB_SYNC].
# =============================================================================

_pg_down_until: float = 0.0
_pg_last: Dict[str, Any] = {"at": -1e9}
_flush_lock: Optional[asyncio.Lock] = None
_bounded_task: Optional[asyncio.Task] = None
_keepalive_task: Optional[asyncio.Task] = None


def _pg_cfg(key: str, default: float) -> float:
    try:
        return float(config.get_db_sync().get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _is_conn_failure(e: BaseException) -> bool:
    """Timeout / socket / asyncpg connection-class errors (matched by name — no asyncpg import here)."""
    name = type(e).__name__
    return isinstance(e, (asyncio.TimeoutError, OSError)) or "Connection" in name or "Interface" in name


def _pg_mark(ok: bool) -> None:
    global _pg_down_until
    _pg_down_until = 0.0 if ok else time.monotonic() + _pg_cfg("PG_BACKOFF_SEC", 30)


async def _pg_exec(query: str, *args):
    """One PG write with a hard time limit. Connection-class failures arm the backoff."""
    try:
        result = await asyncio.wait_for(_psql_writer(query, *args), timeout=_pg_cfg("PG_WRITE_TIMEOUT_SEC", 10))
        _pg_mark(True)
        return result
    except Exception as e:
        if _is_conn_failure(e):
            _pg_mark(False)
        raise


async def flush_persist_queue(limit: int = 50) -> Dict[str, Any]:
    """Single-flight flush (lock): timer + request path can never insert the same row twice."""
    global _flush_lock
    _check_init()
    if _flush_lock is None:
        _flush_lock = asyncio.Lock()
    async with _flush_lock:
        return await _flush_locked(limit)


async def flush_persist_queue_bounded(limit: int = 50, wait_sec: Optional[float] = None) -> Dict[str, Any]:
    """
    Request-path flush: waits at most PG_REQUEST_WAIT_SEC. If PostgreSQL is slow/asleep the
    flush keeps running in the background (shielded) and the caller gets reason "pg_slow".
    """
    global _bounded_task
    wait = _pg_cfg("PG_REQUEST_WAIT_SEC", 3) if wait_sec is None else wait_sec
    if _bounded_task is None or _bounded_task.done():
        _bounded_task = asyncio.create_task(flush_persist_queue(limit))
    try:
        return await asyncio.wait_for(asyncio.shield(_bounded_task), timeout=wait)
    except asyncio.TimeoutError:
        return {"flushed": 0, "errors": 0, "remaining": await _queue_count(), "reason": "pg_slow"}


async def pg_status(force: bool = False) -> Dict[str, Any]:
    """
    Is PostgreSQL awake? Cheap ping (SELECT 1) with a short limit, cached PG_STATUS_TTL_SEC.
    state: "up" | "no_response" (asleep/waking/unreachable) | "error"; bridge: active|inactive.
    The ping itself wakes a suspended Neon compute.
    """
    if not _psql_writer:
        return {"bridge": "inactive", "state": "n/a"}
    now = time.monotonic()
    if not force and now - _pg_last["at"] < _pg_cfg("PG_STATUS_TTL_SEC", 20):
        return {k: v for k, v in _pg_last.items() if k != "at"}

    t0 = time.monotonic()
    state, err = "up", None
    try:
        await asyncio.wait_for(_psql_writer("SELECT 1"), timeout=_pg_cfg("PG_PING_TIMEOUT_SEC", 5))
        _pg_mark(True)
    except asyncio.TimeoutError:
        state = "no_response"
        _pg_mark(False)
    except Exception as e:
        state, err = "error", type(e).__name__
        if _is_conn_failure(e):
            _pg_mark(False)
    _pg_last.clear()
    _pg_last.update({
        "at": time.monotonic(), "bridge": "active", "state": state,
        "latency_ms": int((time.monotonic() - t0) * 1000), "error": err,
        "backoff_active": time.monotonic() < _pg_down_until,
    })
    return {k: v for k, v in _pg_last.items() if k != "at"}


async def _keepalive_loop(interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await pg_status(force=True)
        except Exception as e:
            logger.debug(f"Keepalive ping skipped: {type(e).__name__}")


async def _queue_count() -> int:
    async with aiosqlite.connect(_db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM persist_queue")
        return int((await cursor.fetchone())[0])


async def persist_queue_stats() -> Dict[str, Any]:
    """
    Zustand des Gedächtnisses — Observability für die Orchestrierung.
    Wird von persist_status (MCP + /api) gelesen.
    """
    _check_init()
    async with aiosqlite.connect(_db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*), MIN(queued_at), "
            "SUM(CASE WHEN attempts > 0 THEN 1 ELSE 0 END) FROM persist_queue"
        )
        row = await cursor.fetchone()
    count, oldest, failed = row[0] or 0, row[1], row[2] or 0
    return {
        "queue_depth": count,
        "oldest_queued_at": oldest,
        "entries_with_failed_attempts": failed,
        "postgres_bridge": "active" if _psql_writer else "inactive (DATABASE_URL not set)",
        "note": "queue is restart-safe — flushes to PostgreSQL as soon as the bridge is active",
    }


def start_persist_flusher() -> None:
    """
    Background retry loop — started by app.py AFTER db_sync.initialize().
    Works with AND without writer: ohne PG bleibt alles einfach liegen.
    """
    global _flush_task
    if _flush_task is not None:
        return
    interval = float(config.get_db_sync().get("PERSIST_FLUSH_INTERVAL_SEC", "60"))
    _flush_task = asyncio.create_task(_persist_flush_loop(interval))
    logger.info(f"Persist flusher started (interval: {interval}s).")

    global _keepalive_task
    ka = _pg_cfg("PG_KEEPALIVE_SEC", 0)
    if ka > 0 and _keepalive_task is None:
        _keepalive_task = asyncio.create_task(_keepalive_loop(ka))
        logger.info(f"PostgreSQL keepalive ping every {int(ka)}s (keeps Neon awake — uses compute time).")


async def _persist_flush_loop(interval: float) -> None:
    # Erster Versuch kurz nach dem Boot — fängt Einträge der letzten Session ab
    # (genau dann, wenn PG wieder da ist, während er vorher down war).
    await asyncio.sleep(5)
    while True:
        try:
            result = await flush_persist_queue()
            if result.get("flushed") or result.get("errors"):
                logger.info(f"Persist flusher: {result}")
        except Exception as e:
            logger.warning(f"Persist flusher error (non-fatal): {type(e).__name__}")
        await asyncio.sleep(interval)


async def persist(table: str, data: dict) -> None:
    """
    Legacy direct-write — jetzt queue-safe: enqueue + sofortiger Flush-Versuch.
    Beißt nicht mehr ohne DATABASE_URL (früher: RuntimeError). Backward-kompatibel
    für externe Aufrufer; tools.py nutzt jetzt enqueue_persist() direkt.
    """
    await enqueue_persist(table, payload=data)
    await flush_persist_queue(limit=1)

# =============================================================================
# Direct execution guard
# =============================================================================

if __name__ == "__main__":
    print("WARNING: Run via main.py → app.py, not directly.")
