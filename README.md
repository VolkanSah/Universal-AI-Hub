---
title: ⬡ Universal AI Hub 
emoji: 🛡️
colorFrom: indigo
colorTo: red
sdk: docker
pinned: false
license: apache-2.0
short_description: 'Secure Multi-LLM Gateway — (Streamable HTTP / SSE)'
---

# ⬡ Universal AI Hub - (+LLM API Gateway)

- or advanced Universal MCP Hub (Sandboxed)
- or secure AI wrapper with dual interface: REST + (FAST)MCP and many more
- or own AI HUB Server for local, Huggingface or similar

aka: a clean, secure starting point for your own projects.  
Pick the description that fits your use case. They're all correct.

> A production-grade **the-thing** that actually thinks about security.  
> Built on [PyFundaments](PyFundaments.md) — running on **simpleCity**.

```
No key → no tool → no crash → no exposed secrets
```

> [!TIP]
> Most MCP servers are prompts dressed up as servers. This is not an simple MCP server, it has a real architecture.

---

> [!IMPORTANT]
> This project is under active development — always use the latest release from [Codey Lab](https://github.com/Codey-LAB/Multi-LLM-API-Gateway) *(more stable builds land here first)*.  
> This repo ([DEV](https://github.com/VolkanSah/Multi-LLM-API-Gateway)) is where the chaos happens. 🔬 A ⭐ on the repos will be cool 😙

---

## Why this exists

The AI ecosystem is full of servers with hardcoded keys, `os.environ` scattered everywhere, zero sandboxing. One misconfigured fork and your API keys are gone or just 100 % AI crafted buggy code!

This is exactly the kind of negligence (and worse — outright fraud) that [Wall of Shames](https://github.com/Wall-of-Shames) documents: fake "AI tools" exploiting non-technical users over social network (Meta/TikTok) — API wrappers dressed up as custom models, Telegram payment funnels, bought stars. If you build on open source, you should know this exists.

**This hub is the antidote:**

- **Structural sandboxing** — `app/*` can never touch `fundaments/` or `.env`. Not by convention. By design.
- **Guardian pattern** — `main.py` is the only process that reads secrets. It injects validated services as a dict. `app/*` never sees the raw environment.
- **Graceful degradation** — No key? Tool doesn't register. Server still starts. No crash, no error, no empty `None` floating around.
- **Single source of truth** — All tool/provider/model config lives in `app/.pyfun`. Adding a provider = edit one file. No code changes.

---

## Two Interfaces — One Server

This hub exposes **two completely independent interfaces** on the same hypercorn instance:

```
POST /api          → REST interface — for custom clients, desktop apps, CMS plugins
GET+POST /mcp      → MCP interface — for Claude Desktop, Cursor, Windsurf, any MCP client
GET /              → Health check — uptime, status
```

They share the same tool registry, provider config, and fallback chain. Adding a tool once makes it available on both interfaces automatically.

### REST API (`/api`)

Simple JSON POST — no protocol overhead, works with any HTTP client:

```json
POST /api
{"tool": "llm_complete", "params": {"prompt": "Hello", "provider": "anthropic"}}
```

Used by: Desktop Client (`DESKTOP_CLIENT/hub.py`), WordPress plugin, any custom integration.

### MCP Interface (`/mcp`)

Full MCP protocol — tool discovery, structured calls, streaming responses.

**Primary transport: Streamable HTTP** (MCP spec 2025-11-25)  
**Fallback transport: SSE** (legacy, configurable via `.pyfun`)

Configured via `HUB_TRANSPORT` in `app/.pyfun [HUB]` or in .env (.env files are given priority and override .pyfun files)

```ini
HUB_TRANSPORT = "streamable-http"   # default — MCP spec 2025-11-25
# HUB_TRANSPORT = "sse"             # legacy fallback for older clients
```

Used by: Claude Desktop, Cursor, Windsurf, any MCP-compatible client.

---

## Architecture of the app


```
main.py (Guardian)
│
│  reads .env / HF Secrets
│  initializes fundaments/* conditionally
│  injects validated services as dict
│
└──► app/app.py (Orchestrator, sandboxed)
     │
     │  unpacks fundaments ONCE, at startup, never stores globally
     │  starts hypercorn (async ASGI)
     │  routes: GET / | POST /api | /mcp (transport-dependent)
     │
     ├── app/mcp.py         ← FastMCP + transport handler (Streamable HTTP / SSE)
     ├── app/tools.py       ← Tool registry (key-gated)
     ├── app/providers.py   ← LLM + Search execution + fallback chain
     ├── app/models.py      ← Model limits, costs, capabilities
     ├── app/config.py      ← .pyfun parser (single source of truth)
     └── app/db_sync.py     ← Internal SQLite IPC (app/* state only)
                              ≠ fundaments/postgresql.py (Guardian-only)
```

Whole project structure [PROJECT_STRUCTURE](PROJECT_STRUCTURE.md)

**The sandbox is structural:**

```python
# app/app.py — fundaments unpacked ONCE, NEVER stored globally
async def start_application(fundaments: Dict[str, Any]) -> None:
    config_service         = fundaments["config"]
    db_service             = fundaments["db"]          # None if not configured
    encryption_service     = fundaments["encryption"]  # None if keys missing
    access_control_service = fundaments["access_control"]
    ...
    # From here: app/* reads its own config from app/.pyfun only.
    # fundaments are never passed into other app/* modules.
```

`app/app.py` never calls `os.environ`. Never imports from `fundaments/`. Never reads `.env`.  
This isn't documentation. It's enforced by the import structure.

### Why Quart + hypercorn?

**Quart** is async Flask — fully `async/await` native. FastMCP's handlers are async; mixing sync Flask would require thread hacks. With Quart, `/mcp` hands off directly to FastMCP — no bridging, no blocking.

**hypercorn** is an ASGI server (vs. waitress/gunicorn which are WSGI). WSGI servers handle one request per thread — wrong for long-lived MCP connections. hypercorn handles both Streamable HTTP and SSE natively, and runs without extra config on HuggingFace Spaces. HTTP/2 support (`config.h2 = True`) is built-in — relevant for Streamable HTTP performance at scale.

The `/mcp` route in `app.py` remains the natural interception point regardless of transport — auth checks, rate limiting, and logging can all be added there before the request reaches FastMCP.

---

## Two Databases — One Architecture


```
┌─────────────────────────────────────────────────────────────┐
│  Guardian Layer (fundaments/*)                              │
│                                                             │
│  postgresql.py   → Cloud DB (e.g. Neon, Supabase)          │
│                    asyncpg pool, SSL enforced               │
│                                                             │
│  user_handler.py → SQLite (users + sessions tables)        │
│                    PBKDF2-SHA256 password hashing           │
│                    Session validation incl. IP + UserAgent  │
│                    Account lockout after 5 failed attempts  │
│                                                             │
└──────────────────────┬──────────────────────────────────────┘
                       │ inject as fundaments dict
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  App Layer (app/*)                                          │
│                                                             │
│  db_sync.py  → SQLite (hub_state + tool_cache tables)      │
│                aiosqlite (async, non-blocking)              │
│                NEVER touches users/sessions tables          │
│                Relocated to /tmp/ on HF Spaces auto        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```



---
**Why two databases?**

`user_handler.py` (Guardian) owns `users` and `sessions` — authentication state that must be isolated from the app layer. `db_sync.py` (app/*) owns `hub_state` and `tool_cache` — fast, async IPC between tools that doesn't need to leave the process, let alone hit a cloud endpoint.

A tool caching a previous LLM response or storing intermediate state between pipeline steps should never wait on a round-trip to Neon. 
Local SQLite is microseconds. Cloud PostgreSQL is 50-200ms per query. For tool-to-tool communication, that difference matters.

**Table ownership — hard rule:**

| Table | Owner | Access |
| :--- | :--- | :--- |
| `users` | `fundaments/user_handler.py` | Guardian only |
| `sessions` | `fundaments/user_handler.py` | Guardian only |
| `hub_state` | `app/db_sync.py` | app/* only |
| `tool_cache` | `app/db_sync.py` | app/* only |
| `hub_results` | PostgreSQL / Guardian | via `persist_result` tool |

`db_sync.py` uses the same SQLite path (`SQLITE_PATH`) as `user_handler.py` — same file, different tables, zero overlap. The `db_query` 

**Cloud DB (postgresql.py):**

Handles the heavy cases — persistent storage, workflow tool results that need to survive restarts, anything that benefits from a real relational DB. Neon-specific quirks are handled automatically: `statement_timeout` is stripped from the DSN (Neon doesn't support it), SSL is enforced at `require` minimum, keepalives are set, and terminated connections trigger an automatic pool restart.

If no `DATABASE_URL` is set, the entire cloud DB layer is skipped cleanly. The app runs without it.





---

## Tools

Tools register at startup — only if the required API key exists. No key, no tool. Server always starts.

| ENV Secret | Tool | Notes |
| :--- | :--- | :--- |
| `ANTHROPIC_API_KEY` | `llm_complete` | Claude Haiku / Sonnet / Opus |
| `GEMINI_API_KEY` | `llm_complete` | Gemini 2.0 / 2.5 / 3.x Flash & Pro |
| `OPENROUTER_API_KEY` | `llm_complete` | 100+ models via OpenRouter |
| `HF_TOKEN` | `llm_complete` | HuggingFace Inference API |
| `BRAVE_API_KEY` | `web_search` | Independent web index |
| `TAVILY_API_KEY` | `web_search` | AI-optimized search with synthesized answers |
| `DATABASE_URL` | `cloud DB` | e.g. Neon, Supabase |
| `DATABASE_URL` | `db_query`, `persist_result` | SQLite read + PostgreSQL write |
| *(always)* | `list_active_tools` | Shows key names only — never values |
| *(always)* | `health_check` | Status + uptime + active transport |
| *(always)* | `get_model_info` | Limits, costs, capabilities per model |

For all key names see [`app/.pyfun`](app/.pyfun).

**Tools are configured in `.pyfun` — including system prompts:**

```ini
[TOOL.code_review]
active           = "true"
description      = "Review code for bugs, security issues and improvements"
provider_type    = "llm"
default_provider = "anthropic"
timeout_sec      = "60"
system_prompt    = "You are an expert code reviewer. Analyze the given code for bugs, security issues, and improvements. Be specific and concise."
[TOOL.code_review_END]
```

Current built-in tools: `llm_complete`, `code_review`, `summarize`, `translate`, `web_search`, `db_query` , `persist_result`
Future hooks (commented, ready): `image_gen`, `code_exec`, `shellmaster`, Discord, GitHub webhooks

---

## LLM Fallback Chain

All LLM providers share one `llm_complete` tool. If a provider fails, the hub walks the fallback chain from `.pyfun`:

```
e.g. anthropic → gemini → openrouter → huggingface
```

```ini
[LLM_PROVIDER.anthropic]
fallback_to = "gemini"
[LLM_PROVIDER.anthropic_END]

[LLM_PROVIDER.gemini]
fallback_to = "openrouter"
[LLM_PROVIDER.gemini_END]
```

Same pattern applies to search providers (`brave → tavily`).

---

## Quick Start

### HuggingFace Spaces (recommended)

1. Fork / duplicate this Space
2. Go to **Settings → Variables and secrets**
3. Add the API keys you have (any subset works)
4. Space starts automatically — only tools with valid keys register

[→ Live Demo Space](https://huggingface.co/spaces/codey-lab/Multi-LLM-API-Gateway) (no LLM keys set)

### Local / Docker

```bash
git clone https://github.com/VolkanSah/Multi-LLM-API-Gateway
cd Multi-LLM-API-Gateway
cp example-mcp___.env .env
# fill in your keys
pip install -r requirements.txt
python main.py
```

Minimum required ENV vars (everything else is optional):

```env
PYFUNDAMENTS_DEBUG=""
LOG_LEVEL="INFO"
LOG_TO_TMP=""
ENABLE_PUBLIC_LOGS="true"
HF_TOKEN=""
HUB_SPACE_URL=""
```

Transport is configured in `app/.pyfun [HUB]` — not via ENV.

---

## Connect an MCP Client

### Streamable HTTP (default — MCP spec 2025-11-25)

```json
{
  "mcpServers": {
    "universal-mcp-hub": {
      "url": "https://YOUR_USERNAME-universal-mcp-hub.hf.space/mcp"
    }
  }
}
```

### Streamable HTTP — Private Space (with HF token)

```json
{
  "mcpServers": {
    "universal-mcp-hub": {
      "url": "https://YOUR_USERNAME-universal-mcp-hub.hf.space/mcp",
      "headers": {
        "Authorization": "Bearer hf_..."
      }
    }
  }
}
```

### SSE legacy fallback (set `HUB_TRANSPORT = "sse"` in `.pyfun`)

```json
{
  "mcpServers": {
    "universal-mcp-hub": {
      "url": "https://YOUR_USERNAME-universal-mcp-hub.hf.space/mcp"
    }
  }
}
```

> Same URL (`/mcp`) for both transports — the protocol is negotiated automatically.  
> SSE fallback is for older clients that don't support Streamable HTTP yet.

---

## Desktop Client
###### (experimental — ~80% AI generated)

A full PySide6 desktop client is included in `DESKTOP_CLIENT/hub.py`.  
Communicates via the REST `/api` endpoint — no MCP protocol overhead.  
Ideal for private or non-public Spaces.

```bash
pip install PySide6 httpx
# optional file handling:
pip install Pillow PyPDF2 pandas openpyxl
python DESKTOP_CLIENT/hub.py
```

**Features:**
- Multi-chat with persistent history
- Tool / Provider / Model selector loaded live from your Hub
- File attachments: images, PDF, CSV, Excel, ZIP, source code
- Connect tab with health check + auto-load
- Settings: HF Token + Hub URL saved locally, never sent anywhere except your own Hub
- Full request/response log with timestamps
- Runs on Windows, Linux, macOS

[→ Desktop Client docs](DESKTOP_CLIENT/README.md)

---

## CMS & Custom Clients

| Client | Interface used | Notes |
| :--- | :--- | :--- |
| [Desktop Client](DESKTOP_CLIENT/hub.py) | REST `/api` | PySide6, local |
| [WP AI Hub](https://github.com/VolkanSah/WP-AI-HUB/) | REST `/api` | WordPress plugin |
| TYPO3 (soon) | REST `/api` | — |
| Claude Desktop | MCP `/mcp` | Streamable HTTP |
| Cursor / Windsurf | MCP `/mcp` | Streamable HTTP |
| [WEB UI](https://github.com/VolkanSah/ai-hub) | REST `/api` | Next.js/Vercel

---

## Configuration (.pyfun)

> [!CAUTION]
> **To ensure security, store sensitive information in .env secrets. These values override any corresponding settings in .pyfun. Under no circumstances should API keys be committed to public repositories.**


`app/.pyfun` is the single source of truth for all app behavior. Three tiers:

```
LAZY:       [HUB] + one [LLM_PROVIDER.*]                    → works
NORMAL:     + [SEARCH_PROVIDER.*] + [MODELS.*]              → works better
PRODUCTIVE: + [TOOLS] + [HUB_LIMITS] + [DB_SYNC]           → full power
```

Key settings in `[HUB]`:

```ini
[HUB]
HUB_TRANSPORT   = "streamable-http"   # streamable-http | sse
HUB_STATELESS   = "true"              # true = HF Spaces safe, no session state
HUB_PORT        = "7860"
[HUB_END]
```

Adding a new LLM provider — two steps:

```ini
# 1. app/.pyfun
[LLM_PROVIDER.mistral]
active        = "true"
base_url      = "https://api.mistral.ai/v1"
env_key       = "MISTRAL_API_KEY"
default_model = "mistral-large-latest"
models        = "mistral-large-latest, mistral-small-latest"
fallback_to   = ""
[LLM_PROVIDER.mistral_END]
```

```python
# 2. app/providers.py — uncomment the dummy
_PROVIDER_CLASSES = {
    ...
    "mistral": MistralProvider,   # ← uncomment to activate
}
```

---

## Dependencies

```
# PyFundaments Core (always required)
asyncpg          — async PostgreSQL pool (Guardian/cloud DB)
python-dotenv    — .env loading
passlib          — PBKDF2 password hashing in user_handler.py
cryptography     — encryption layer in fundaments/

# MCP Hub
mcp              — MCP protocol + FastMCP (Streamable HTTP + SSE)
httpx            — async HTTP for all provider API calls
quart            — async Flask (ASGI) — needed for MCP + hypercorn
hypercorn        — ASGI server — Streamable HTTP + SSE, HF Spaces native
requests         — sync HTTP for tool workers

# Optional (uncomment in requirements.txt as needed)
# aiofiles         — async file ops (ML pipelines, file uploads)
# discord.py       — Discord bot integration (planned)
# PyNaCl           — Discord signature verification
# psycopg2-binary  — alternative PostgreSQL driver
```

> **Note:** The package is `mcp` (not `fastmcp`) — `FastMCP` is imported from `mcp.server.fastmcp`.  
> Streamable HTTP support requires `mcp >= 1.6.0`.

---

## Security Design

- API keys live in Secrets / `.env` — never in `.pyfun`, never in code
- `list_active_tools` returns key **names** only — never values
- `db_query` is SELECT-only, enforced at application level (not just docs)
- `app/*` has zero import access to `fundaments/` internals
- Direct execution of `app/app.py` blocked by design — warning + null-fundaments fallback
- `fundaments/` initialized conditionally — missing services degrade gracefully, never crash
- Streamable HTTP uses standard Bearer headers — no token-in-URL (unlike SSE)

> PyFundaments is not perfect. But it's more secure than most of what runs in production today.

[→ Full Security Policy](SECURITY.md)

---

## Foundation

Built on [PyFundaments](PyFundaments.md) — a security-first Python boilerplate:

- `config_handler.py` — env loading with validation
- `postgresql.py` — async DB pool (Guardian-only)
- `encryption.py` — key-based encryption layer
- `access_control.py` — role/permission management
- `user_handler.py` — user lifecycle management
- `security.py` — unified security manager composing the above

None accessible from `app/*`. Injected as a validated dict by `main.py`.

[→ PyFundaments Function Overview](PyFundaments%20–%20Function%20Overview.md)  
[→ Module Docs](docs/app/)  
[→ Source Repo](https://github.com/VolkanSah/Multi-LLM-API-Gateway)

---

## Related Projects

- [Customs LLMs for free — Build Your Own LLM Service](https://github.com/VolkanSah/SmolLM2-customs/)
     - [Parquet-Sync](https://github.com/VolkanSah/Parquet-Sync)
- [WP AI Hub (WordPress Client)](https://github.com/VolkanSah/WP-AI-HUB/)
- [ShellMaster (2023 precursor)](https://github.com/VolkanSah/ChatGPT-ShellMaster) - (Archived)

---

## History

[ShellMaster](https://github.com/VolkanSah/ChatGPT-ShellMaster) (2023, MIT) was the precursor — browser-accessible shell for ChatGPT with session memory, built before MCP was a concept. Universal MCP Hub is its natural evolution: same idea, proper architecture, dual interface.

---

## License

Dual-licensed:

- [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0)
- [Ethical Security Operations License v2.0 (ESOL)](ESOL) — mandatory, non-severable

By using this software you agree to all ethical constraints defined in ESOL v1.1.

---

*Architecture, security decisions, and PyFundaments by Volkan Kücükbudak.*  
*AI HUB Ökosystem Built with help of Claude (Anthropic) as a typing assistant for docs (and the occasional bug).*

> crafted with passion — just wanted to understand how it works, don't actually need it, have a CLI 😄
