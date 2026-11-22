## File: `app/app.py`

**Description:** This is the **Orchestrator** of the entire sandboxed application. It acts as the central control point — starting the web server (Quart/Hypercorn), initializing internal modules, and managing all external communication (API & MCP).

### Main Functions

- **`start_application()`**: The main entry point. Called exclusively by the Guardian (`main.py`), it receives the injected foundation services (`fundaments`) and decides which features (encryption, auth, DB) are activated based on service availability.
- **API Endpoints**:
  - **`/` (Health Check)**: Returns uptime and status for monitoring systems (required for HuggingFace Spaces).
  - **`/api`**: A generic REST entry point for invoking tools directly via JSON POST (e.g. for system queries or manual tool tests).
  - **`/mcp`**: The critical path for **MCP SSE Transport** — all Model Context Protocol data flows through here.
- **Server Management**: Uses **Hypercorn** (an async ASGI server) to run the Quart app in a fully native async environment.

### Core Logic

`app.py` strictly enforces the **sandbox rules**. It is the only place where the global `fundaments` (such as PostgreSQL access or encryption keys) exist transiently. They are **not** passed down to submodules like `providers.py` or `tools.py` — those modules operate independently and read their own configuration from `.pyfun`.
