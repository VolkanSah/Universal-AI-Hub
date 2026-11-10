# Security Policy

## Reporting a Vulnerability

If you find a security issue, please do **not** open a public GitHub issue.

Report privately via: [GitHub Security Advisories](https://github.com/VolkanSah/Universal-MCP-Hub/security/advisories/new)

Include: what you found, how to reproduce it, and what impact you think it has. You'll get a response within 72 hours. If the issue is confirmed, a fix will be prioritized before any public disclosure.

---

## Security Architecture

### The Guardian Pattern

`main.py` is the only process that ever touches secrets. It reads `.env` / HF Space Secrets, initializes `fundaments/*` conditionally, and passes the results into `app/*` as a validated dict. After that handoff, `app/*` has no path back to the raw environment.

```
.env / HF Secrets
      │
      ▼
main.py (Guardian) — only entry point
      │
      │  initializes fundaments/* conditionally
      │  injects as: fundaments = {"config": ..., "db": ..., "encryption": ..., ...}
      │
      ▼
app/app.py — unpacks fundaments ONCE at startup, never stores globally
      │
      └── app/* modules — read only from app/.pyfun, never from os.environ
```

This is enforced structurally. `app/*` has no import of `fundaments/`, no `os.environ` calls, no `.env` access. It's not a coding convention — it's an import boundary.

---

### Secret Handling

- API keys live exclusively in HF Space Secrets or `.env` — never in `app/.pyfun`, never in source code
- `app/.pyfun` references only ENV variable **names** (e.g. `env_key = "ANTHROPIC_API_KEY"`) — never values
- `list_active_tools` returns key names only — the actual key values are never exposed through any MCP tool
- `fundaments/` modules are initialized only if their required ENV vars are present — missing config = service skipped, not crashed

---

### Encryption (`fundaments/encryption.py`)

When `MASTER_ENCRYPTION_KEY` and `PERSISTENT_ENCRYPTION_SALT` are set:

- Key derivation: **PBKDF2-HMAC-SHA256**, 480,000 iterations, 256-bit output
- Encryption: **AES-256-GCM** — authenticated encryption, provides both confidentiality and integrity
- Each encryption operation uses a fresh random 96-bit nonce
- Authentication tag (128-bit) is stored alongside ciphertext — decryption fails loudly if data was tampered
- File encryption uses streaming with 8 KB chunks — nonce prepended, tag appended to file
- If encryption keys are not set, the service is skipped cleanly — the app runs without it

---

### Authentication (`fundaments/user_handler.py`)

- Passwords hashed with **PBKDF2-SHA256** via `passlib`
- Sessions stored in SQLite with IP address + User-Agent binding — `validate_session()` checks both
- Session IDs are regenerated on login (session fixation prevention)
- Failed login attempts tracked per user — account locks after 5 consecutive failures
- Session table uses `ON DELETE CASCADE` — deleting a user removes all their sessions automatically

---

### Access Control (`fundaments/access_control.py`)

- Role-based permission system backed by PostgreSQL (cloud DB, Guardian-only)
- Schema: `users → user_role_assignments → user_roles → role_permissions → user_permissions`
- All queries use parameterized statements via `asyncpg` — no string interpolation
- `has_permission()` checks the full role→permission chain per request
- `AccessControl` only initializes if a DB connection is available — degrades gracefully otherwise

---

### Database Security (`fundaments/postgresql.py`)

**Cloud DB (asyncpg pool):**
- SSL enforced at minimum `sslmode=require` — upgraded automatically if not set
- Neon.tech quirks handled: `statement_timeout` stripped from DSN (unsupported, causes silent failures), keepalives set
- Credentials masked in all log output — full DSN only in `DEBUG` level logs
- SSL runtime check via `pg_stat_ssl` on every new connection (cloud providers: warning only if unavailable)
- Automatic pool restart on Neon connection termination (sqlstate `08006`)
- Pool: min 1 / max 10 connections, connect timeout 5s, command timeout 30s

**Internal SQLite (`app/db_sync.py` + `fundaments/user_handler.py`):**
- Same SQLite file, separate tables — zero overlap enforced by code
- `users` + `sessions` → owned by `user_handler.py` (Guardian layer)
- `hub_state` + `tool_cache` → owned by `db_sync.py` (app/* layer)
- `db_query` MCP tool exposes SELECT-only access to `hub_state` and `tool_cache` — cannot reach `users` or `sessions`
- SELECT enforcement: non-SELECT queries raise `ValueError` before execution
- On HuggingFace Spaces: SQLite auto-relocated to `/tmp/` (HF filesystem is read-only outside `/tmp/`)

---

### MCP Transport Security

- All MCP traffic routes through Quart `/mcp` endpoint before reaching FastMCP
- This is the natural interception point for auth checks, rate limiting, and payload logging — none are optional add-ons
- `/crypto` endpoint exists but returns `501` until the app-layer encryption wrapper is implemented — not bypassed, not silently ignored
- `health_check` and `list_active_tools` are always available — they expose no secrets, only status and key names

---

### Sandboxing Rules

These rules are enforced by code structure, not just documentation:

| Rule | Enforced by |
| :--- | :--- |
| `app/*` never reads `os.environ` | No `os.environ` calls in any `app/` file |
| `app/*` never imports `fundaments/` | No cross-imports between layers |
| `fundaments/` services injected, not constructed in `app/*` | `start_application(fundaments)` signature |
| Direct execution of `app/app.py` blocked | `__main__` guard with null-fundaments warning |
| `db_query` tool is SELECT-only | `ValueError` raised before any non-SELECT execution |
| API keys never in `.pyfun` | `.pyfun` parser only reads key names, never resolves them |

---

### What This Project Does Not Claim

- It is not a penetration-tested security product
- The encrypted `/crypto` endpoint is not yet implemented
- Search provider implementations (Brave, Tavily) are pending — the tool registers as placeholder
- This is a personal/community project — use in production at your own assessment

> PyFundaments is not perfect. But it's more secure than most of what runs in production today.

---

## Supported Versions

| Version | Supported |
| :--- | :--- |
| latest (`main`) | ✅ |
| older forks | ❌ — fork at your own risk |

---

## License Note

This project is dual-licensed under Apache 2.0 and the [Ethical Security Operations License v1.1 (ESOL)](ESOL). Using this software for surveillance, unauthorized access, or harm to others automatically terminates your license.
