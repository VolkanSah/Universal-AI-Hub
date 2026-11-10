---
title: Universal AI Hub
emoji: 🛡️
colorFrom: indigo
colorTo: red
sdk: docker
pinned: true
license: apache-2.0
short_description: Secure Multi-HUB i craft since month
---

# Universal AI Hub 🛡️

> stabe and secure but not 101 % still testing!

- or **Multi-LLM API Gateway**
- or secure AI wrapper with dual interface: REST + MCP

aka: a clean, secure starting point for your own projects.
Pick the description that fits your use case. They're all correct.

> A **the-thing** that actually thinks about security.
> Built on [PyFundaments](PyFundaments.md) — running on **simpleCity**.

```
No key → no tool → no crash → no exposed secrets
```

> [!WARNING]
> Most MCP servers are prompts dressed up as servers. This one has a real architecture.

> [!IMPORTANT]
> This project is under active development — always use the latest release from [Codey Lab](https://github.com/Codey-LAB/Multi-LLM-API-Gateway) *(more stable builds land here first)*.
> This repo ([DEV](https://github.com/VolkanSah/Multi-LLM-API-Gateway)) is where the chaos happens. 🔬 A ⭐ on the repos will be cool 😙

---

<details>
<summary><b>🧠 Usernote: how this was actually built (no vibecode — or am I lying? 😄)</b></summary>

I wrote this myself. And I broke myself doing it — properly. Not "my code doesn't run" broken, but "I've been staring at my own architecture for months and my brain is soup" broken. At that point AI stepped in — not to write my project, but to help me untangle my thoughts and shape what was already in my head.

Is that vibecoding? **No.** Vibecoding is shipping code you don't understand and hoping for the best. I understand every line here — including the weird ones. The guardian pattern, the sandbox, the `.pyfun` single-source-of-truth, the no-key-no-tool rule: my decisions, made before any AI touched anything. I code differently than everyone else — you'll see it the moment you read the source. The AI was a sparring partner with fast fingers (and, to be fair, the occasional bug that I then had to hunt).

...or am I lying? Don't take my word for it. Read the code.

</details>

---

<details>
<summary><b>🆕 What's new in v1.2.0 — memory that survives a reboot</b></summary>

> Full changelog (with proof) in [`REVIEW.md`](REVIEW.md). The short version, my words:

- **Memory orchestration.** `persist_result` without `DATABASE_URL` used to throw a `RuntimeError`. Now everything lands in a restart-safe `persist_queue` in SQLite — proven: 3 entries in, reboot, 3 entries out — and a background flusher pushes them into PostgreSQL whenever the bridge is back. `persist_status` (MCP + `/api`) shows queue depth, attempts and bridge state.
- **Auto-pricing, konsequent.** I asked whether the manual `cost_*` lines still make sense. OpenRouter FAQ, checked live: no markup, per-token = vendor list price (the ~5.5 % fee hits credit purchase, not tokens). So 34 cost lines are gone. What stays: my own models (`smollm2-360m` = 0.0 — no external source knows my own deployment). `.pyfun` values still win as override, always.
- **ShellMaster = SECURITY LEVEL ROT.** My CLI commander for the local Quart agent (`localhost:5004`) — commands run on YOUR machine, so: local only, never on a public Space. Token lives in `.env` via `SHELLMASTER_TOKEN` (same pattern as every provider); the `.pyfun` field is only a local test fallback. `active = "false"`. Stays off until someone consciously flips it.
- **Score slot for my filter.** `hub_results.score` exists — the hub passes it through completely uninterpreted. What's worth remembering long-term gets decided by my own filter, not by the hub. That part is my code, my rules.

*Tests: 26/26 orchestration + 4/4 smoke — green.*

<details>
<summary><b>v1.1.0 (the rebuild) — older news</b></summary>

- **Fixed:** `/mcp` 404 (mount middleware stripped the prefix FastMCP expects) and the missing session-manager lifespan — MCP is fully functional again.
- **New:** **Auto model pricing** — costs fetched from the public OpenRouter models API + the LiteLLM community price list, cached in `db_sync` (24h TTL). `.pyfun [MODELS]` values win as override.
- **New:** Brave + Tavily search providers with fallback chain — `web_search` registers as soon as `BRAVE_API_KEY` or `TAVILY_API_KEY` is set.
- **New:** `db_query` as MCP tool (SELECT-only, app/* tables).
- **Cleanup:** dead code removed, `requirements.txt` pins `mcp>=1.10,<2` (not `fastmcp`), LF line endings.

</details>
</details>

--- 

<details>
<summary><b>❓ Why this exists</b></summary>

The AI ecosystem is full of servers with hardcoded keys, `os.environ` scattered everywhere, zero sandboxing. One misconfigured fork and your API keys are gone.

This is exactly the kind of negligence (and worse — outright fraud) that [Wall of Shames](https://github.com/Wall-of-Shames) documents: fake "AI tools" exploiting non-technical users — API wrappers dressed up as custom models, Telegram payment funnels, bought stars. If you build on open source, you should know this exists.

This hub is the antidote:

- **Structural sandboxing** — `app/*` can never touch `fundaments/` or `.env`. Not by convention. By design.
- **Guardian pattern** — `main.py` is the only process that reads secrets. It injects validated services as a dict. `app/*` never sees the raw environment.
- **Graceful degradation** — No key? Tool doesn't register. Server still starts. No crash, no error, no empty `None` floating around.
- **Single source of truth** — All tool/provider/model config lives in `app/.pyfun`. Adding a provider = edit one file. No code changes.

</details>

--- 

<details>
<summary><b>🔌 Two interfaces — one server</b></summary>

Two completely independent interfaces on the same hypercorn instance:

```
POST /api          → REST interface — for custom clients, desktop apps, CMS plugins
GET+POST /mcp      → MCP interface — for Claude Desktop, Cursor, Windsurf, any MCP client
GET /              → Health check — uptime, status
```

They share the same tool registry, provider config, and fallback chain. Adding a tool once makes it available on both interfaces automatically.

**REST (`/api`)** — simple JSON POST, no protocol overhead, works with any HTTP client:

```json
POST /api
{"tool": "llm_complete", "params": {"prompt": "Hello", "provider": "anthropic"}}
```

Used by: Desktop Client (`DESKTOP_CLIENT/hub.py`), WordPress plugin, any custom integration.

**MCP (`/mcp`)** — full MCP protocol: tool discovery, structured calls, streaming responses.

- Primary transport: **Streamable HTTP** (MCP spec 2025-11-25)
- Fallback transport: **SSE** (legacy, configurable via `.pyfun`)

```ini
[HUB]
HUB_TRANSPORT = "streamable-http"   # default — MCP spec 2025-11-25
# HUB_TRANSPORT = "sse"             # legacy fallback for older clients
[HUB_END]
```

Auth, rate limiting and logging can all hook into the `/mcp` route in `app/app.py` before a request ever reaches FastMCP.

</details>

---

<details>
<summary><b>🏗 Architecture — the sandbox is structural</b></summary>

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
     ├── app/shellmaster.py ← CLI commander (SECURITY LEVEL ROT — local only)
     ├── app/pricing.py     ← Auto model pricing (OpenRouter + LiteLLM)
     ├── app/config.py      ← .pyfun parser (single source of truth)
     └── app/db_sync.py     ← SQLite: short-term state + persist_queue (memory bridge)
                              ≠ fundaments/postgresql.py (Guardian-only, long-term)
```

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

**Why Quart + hypercorn?**

**Quart** is async Flask — fully `async/await` native. FastMCP's handlers are async; mixing sync Flask would require thread hacks. With Quart, `/mcp` hands off directly to FastMCP — no bridging, no blocking.

**hypercorn** is an ASGI server (vs. waitress/gunicorn which are WSGI). WSGI servers handle one request per thread — wrong for long-lived MCP connections. hypercorn handles both Streamable HTTP and SSE natively, and runs without extra config on HuggingFace Spaces. HTTP/2 support (`config.h2 = True`) is built-in.

</details>


---

<details>
<summary><b>💾 Memory — short-term, long-term, and my filter (v1.2.0)</b></summary>

The design is deliberately simple:

```
SQLite  = short-term memory  — all AI tools read & write here, nobody waits for the cloud
SQLite persist_queue = the bridge — restart-safe, survives reboots (proven)
PostgreSQL = long-term memory — what survived MY filter goes here
```

```
persist_result (MCP + /api)
   ↓ enqueue_persist()        ← immediately, ALWAYS, no crash — even without DATABASE_URL
persist_queue (SQLite)        ← survives restarts; on HF Spaces /tmp, PG is the only durable memory
   ↓ flush_persist_queue()    ← background flusher, retry every 60 s (configurable)
PostgreSQL hub_results        ← (id, created_at, source, model, score, payload JSONB)
```

- **No `DATABASE_URL`?** `status: "queued"` — no RuntimeError, no 502, nothing lost.
- **PostgreSQL down?** Entry stays, `attempts` +1, sanitized `last_error`. Flusher catches up when the bridge returns.
- **`score REAL`** — a reserved slot for my own quality filter. The hub passes it through untouched; the filter writes it (or filters on read) later. No hub code interprets it. That's on purpose — my filter, my rules.
- **Limits:** queue max 500 (oldest evicted, like `tool_cache`) · at-least-once semantics (a duplicate beats a loss) · observability via `persist_status` (queue depth, oldest entry, attempts, bridge state).

**Guardian layer vs app layer — table ownership, hard rule:**

| Table | Owner | Access |
| :--- | :--- | :--- |
| `users` | `fundaments/user_handler.py` | Guardian only |
| `sessions` | `fundaments/user_handler.py` | Guardian only |
| `hub_state`, `tool_cache`, `persist_queue` | `app/db_sync.py` | app/* only |
| `hub_results` | PostgreSQL / Guardian | via `persist_result` → queue → flusher |

</details>

---

<details>
<summary><b>🧰 Tools — no key, no tool, server still starts</b></summary>

Tools register at startup — only if the required API key exists. No key, no tool. Server always starts.

| ENV Secret | Tool | Notes |
| :--- | :--- | :--- |
| `ANTHROPIC_API_KEY` | `llm_complete` | Claude Haiku / Sonnet / Opus |
| `GEMINI_API_KEY` | `llm_complete` | Gemini 2.0 / 2.5 / 3.x Flash & Pro |
| `OPENROUTER_API_KEY` | `llm_complete` | 100+ models via OpenRouter |
| `HF_TOKEN` | `llm_complete` | HuggingFace Inference API |
| `BRAVE_API_KEY` | `web_search` | Independent web index |
| `TAVILY_API_KEY` | `web_search` | AI-optimized search with synthesized answers |
| `DATABASE_URL` | `persist_result`, `persist_status` | Long-term memory bridge (queue without it) |
| `SHELLMASTER_TOKEN` | `shellmaster*` | ⛔ SECURITY LEVEL ROT — local only, `active="false"` |
| *(always)* | `list_active_tools` | Shows key names only — never values. Plus `pricing_stats` |
| *(always)* | `health_check` | Status + uptime + active transport |
| *(always)* | `get_model_info` | Limits, costs, capabilities per model (auto-merged) |
| *(always)* | `get_model_pricing` | Auto-fetched costs — `.pyfun` values win as override |
| *(always)* | `refresh_pricing` | Force refresh from OpenRouter + LiteLLM |
| *(always)* | `db_query` | Read-only SELECT on internal hub state |

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

Current built-in tools: `llm_complete`, `code_review`, `summarize`, `translate`, `web_search`, `db_query`, `persist_result`, `persist_status`
Future hooks (commented, ready): `image_gen`, `code_exec`, ShellMaster 2.0 (ROT — local only), Discord, GitHub webhooks

</details>

---

<details>
<summary><b>🔁 Fallback chain</b></summary>

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

</details>

--- 


<details open>
<summary><b>🚀 Quick start</b></summary>

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
ENABLE_PUBLIC_LOGS="false"
HF_TOKEN=""
HUB_SPACE_URL=""
```

> Note: HF Spaces prints stdout to a **publicly visible log tab**. `ENABLE_PUBLIC_LOGS="false"` keeps your logs yours. Default in code is still `true` — my fundament, my call, documented everywhere; flip it consciously.

Transport is configured in `app/.pyfun [HUB]` — not via ENV.

</details>

---


<details>
<summary><b>🔗 Connect an MCP client</b></summary>

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
> (`HUB_STATELESS="true"` is intentional for HF Spaces — no `Mcp-Session-Id` header is expected, not a bug.)

</details>


---


<details>
<summary><b>🖥 Desktop client (experimental — ~80 % me, 20 % typing assistant)</b></summary>

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

</details>

---

<details>
<summary><b>🧩 CMS & custom clients</b></summary>

| Client | Interface used | Notes |
| :--- | :--- | :--- |
| [Desktop Client](DESKTOP_CLIENT/hub.py) | REST `/api` | PySide6, local |
| [WP AI Hub](https://github.com/VolkanSah/WP-AI-HUB/) | REST `/api` | WordPress plugin |
| TYPO3 (soon) | REST `/api` | — |
| Claude Desktop | MCP `/mcp` | Streamable HTTP |
| Cursor / Windsurf | MCP `/mcp` | Streamable HTTP |

</details>


---


<details>
<summary><b>⚙️ Configuration (.pyfun) — one file to rule the app</b></summary>

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

**About `[MODELS]` costs (v1.2.0):** auto-pricing fills everything from OpenRouter + LiteLLM. Manual `cost_*` lines only remain for models no external source can know — my own deployments. If you ever get different conditions than the public lists (custom deals exist), just add `cost_*` lines for THAT model — `.pyfun` wins, always. The section also carries `requests_per_min/day`, `context_tokens`, `capabilities` and the provider mapping — none of that exists in any price API.

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

</details>


---


<details>
<summary><b>📦 Dependencies</b></summary>

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

</details>


---


<details>
<summary><b>🛡 Security design</b></summary>

- API keys live in HF Secrets / `.env` — never in `.pyfun`, never in code
- `list_active_tools` returns key **names** only — never values
- `db_query` is SELECT-only, enforced at application level (not just docs)
- `app/*` has zero import access to `fundaments/` internals
- Direct execution of `app/app.py` blocked by design — warning + null-fundaments fallback
- `fundaments/` initialized conditionally — missing services degrade gracefully, never crash
- Streamable HTTP uses standard Bearer headers — no token-in-URL (unlike SSE)
- ShellMaster: **SECURITY LEVEL ROT** — hardblock list (rm -rf, mkfs, …), JSONL command registry, explicit confirmation before every execution, local-only (`localhost:5004`), token via `.env`

> PyFundaments is not perfect. But it's more secure than most of what runs in production today.

[→ Full Security Policy](SECURITY.md)

</details>


---

<details>
<summary><b>🧱 Foundation: PyFundaments</b></summary>

Built on [PyFundaments](PyFundaments.md) — a security-first Python boilerplate, crafted by hand over months (see the usernote at the top):

- `config_handler.py` — env loading with validation
- `postgresql.py` — async DB pool (Guardian-only)
- `encryption.py` — key-based encryption layer
- `access_control.py` — role/permission management
- `user_handler.py` — user lifecycle management
- `security.py` — unified security manager composing the above
- `debug.py` — conditional startup diagnostics

None accessible from `app/*`. Injected as a validated dict by `main.py`.

[→ PyFundaments Function Overview](PyFundaments%20–%20Function%20Overview.md)
[→ Module Docs](docs/app/)
[→ Source Repo](https://github.com/VolkanSah/Multi-LLM-API-Gateway)

</details>


---


<details>
<summary><b>📜 History & related projects</b></summary>

[ShellMaster](https://github.com/VolkanSah/ChatGPT-ShellMaster) (2023, MIT) was the precursor — browser-accessible shell for ChatGPT with session memory, built before MCP was a concept. Universal AI Hub is its natural evolution: same idea, proper architecture, dual interface. And yes — the ShellMaster idea is back as a tool hook, now with a red security level and a conscience.

Related:

- [Customs LLMs for free — Build Your Own LLM Service](https://github.com/VolkanSah/SmolLM2-customs/)
- [WP AI Hub (WordPress Client)](https://github.com/VolkanSah/WP-AI-HUB/)
- [ShellMaster (2023 precursor)](https://github.com/VolkanSah/ChatGPT-ShellMaster)

</details>

---


<details>
<summary><b>⚖️ License — Multi-License & ESOL (mandatory)</b></summary>

- Projekt GNU Affero General Public License v3.0 [see License](https://www.gnu.org/licenses/agpl-3.0.html) + Ethical Security Operations License v2.1 [see License](ESOL) — mandatory, non-severable
- Some scripts i made Apache License 2.0 [see License](https://www.apache.org/licenses/LICENSE-2.0) + GNU General Public License v3.0 [see License](https://www.gnu.org/licenses/gpl-3.0.html) + MIT License [see License](https://opensource.org/licenses/MIT) (only for as for examples, some skills and agents public on MIT or other open source license i use here in this hub for showcases, see files for more infos)

##### By using this software you agree to all ethical constraints defined in ESOL v2.1.

</details>

---

*Architecture, security decisions and 100 % of brocken code ( ok 100 % Running with less than 1 % mistakes) and PyFundaments by me.*
*Built with Claude (Anthropic not claude coder or codex or wtf!) after 20 % own desaster 5 seps bevor relase, played with GLM to see an outher focus but at the end as a typing assistant for docs (and the occasional bug) after i brick my hole work after i made it to complex... so i mean its not ready!*

> crafted with passion - just wanted to understand how it works, don't actually need it, have a CLI 😄
> 
> README.md craftet (rewritten) by gemini 3 Pro , fixed by me, looks terrible i know! AI slop from claude, geminie and me! Soon better i sewar! 

