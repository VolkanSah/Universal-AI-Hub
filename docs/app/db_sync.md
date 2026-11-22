# app/db_sync.py

**Internal SQLite IPC — app/\* state & communication**

This module manages the hub's internal state via a local SQLite database. It serves as a fast, ephemeral IPC store for app-layer data and tool responses — completely separate from the Guardian-layer cloud database (`fundaments/postgresql.py`).

> **After restart:** SQLite data is gone. This is by design — `db_sync` is short-term memory, not persistent storage. For persistence, use `persist_result` → PostgreSQL.

---

## Table Ownership

| Table | Owner | Access |
| :--- | :--- | :--- |
| `hub_state` | `app/db_sync.py` | app/\* only |
| `tool_cache` | `app/db_sync.py` | app/\* only |
| `users` | `fundaments/user_handler.py` | Guardian only — blocked |
| `sessions` | `fundaments/user_handler.py` | Guardian only — blocked |
| `hub_results` | PostgreSQL / Guardian | via `persist_result` tool |

---

## PostgreSQL Setup

Before using the `persist_result` tool, create the target table once in your cloud DB (Neon, Supabase, etc.):

```sql
CREATE TABLE IF NOT EXISTS hub_results (
    id         SERIAL PRIMARY KEY,
    payload    JSONB NOT NULL,
    created_at TEXT
);
```

---

## Functions

### `initialize()`
Sets the database path and creates required tables.  
On HuggingFace Spaces (`SPACE_ID` is set): automatically relocates SQLite to `/tmp/` since the default filesystem is read-only.  
Config comes from `app/.pyfun [DB_SYNC]` → `SQLITE_PATH`.

### Key/Value Store — `hub_state` table

```python
await db_sync.write(key, value)   # JSON-serialized, any type
await db_sync.read(key, default)  # returns default if not found
await db_sync.delete(key)
```

Used by tools to share intermediate results across tool calls — the "short-term memory" of the hub.

Example:
```python
await db_sync.write("web_search.last_result", {"query": "...", "results": [...]})
data = await db_sync.read("web_search.last_result")
```

### Tool Cache — `tool_cache` table

```python
await db_sync.cache_write(tool_name, prompt, response, provider, model)
cached = await db_sync.cache_read(tool_name, prompt)  # None if not found
```

Stores tool responses to reduce redundant API calls and costs.  
Automatically enforces `MAX_CACHE_ENTRIES` from `.pyfun [DB_SYNC]` — oldest entries are deleted when limit is exceeded.

### Read-Only Query — `query()`

```python
rows = await db_sync.query("SELECT * FROM hub_state")
# returns List[Dict]
```

Strictly limited to `SELECT` statements. All write operations raise `ValueError`.  
Used by the `db_query` MCP tool and REST `/api` endpoint.

### PostgreSQL Bridge — `persist()` / `set_psql_writer()`

```python
await db_sync.persist(table, data)  # writes dict as JSONB to PostgreSQL
```

`set_psql_writer()` is called once by `app/app.py` during startup if `DATABASE_URL` is configured.  
`app/*` never imports `postgresql.py` directly — this callable is the only bridge.  
Graceful degradation: raises `RuntimeError` if no DB configured.

---

## Security

- **SELECT only** — `query()` rejects any non-SELECT statement with `ValueError`
- **Table isolation** — `hub_state` and `tool_cache` are the only tables this module creates or touches
- **Guardian tables unreachable** — `users` and `sessions` are in the same SQLite file but owned by `fundaments/user_handler.py`. Attempting to SELECT them returns `no such table` from the app layer
- **No ENV access** — all config via `app/config.py` → `app/.pyfun`

---

## Test Queries

Run these via the Desktop Client or REST API (`POST /api` with `"tool": "db_query"`).

**hub_state:**
```sql
SELECT * FROM hub_state
SELECT key, value FROM hub_state WHERE key = 'test'
SELECT count(*) FROM hub_state
```

**tool_cache:**
```sql
SELECT * FROM tool_cache
SELECT tool_name, prompt, provider FROM tool_cache
SELECT count(*) FROM tool_cache
SELECT tool_name, count(*) as calls FROM tool_cache GROUP BY tool_name
SELECT * FROM tool_cache ORDER BY created_at DESC LIMIT 5
```

**Security tests — must all REJECT:**
```sql
SELECT * FROM users                              -- no such table (sandbox works)
DROP TABLE hub_state                             -- rejected: not SELECT
INSERT INTO hub_state VALUES ('hack','test','x') -- rejected: not SELECT
from hub_state                                   -- rejected: not SELECT
```

Expected responses:
- `SELECT * FROM users` → `[]` or `no such table: users` ✅
- All non-SELECT → `Only SELECT queries are permitted in db_query tool.` ✅
