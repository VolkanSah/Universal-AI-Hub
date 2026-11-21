---
name: Neon Persistence & Python AI Hub Bridge
description: "Set up Neon PostgreSQL for a Python AI hub using SQLite short-term memory, a restart-safe persist_queue bridge, and filtered long-term storage in hub_results"
tags: neon, postgres, postgresql, sqlite, python, persist, bridge, queue, memory, guardian, observability, cli
---


## HUB Architecture Principles
- **SQLite (Short-Term Memory & Shared Bus):** All AI models and tools read and write locally to SQLite without waiting on the cloud.
- **SQLite `persist_queue` (Restart-Safe Bridge):** `enqueue_persist()` writes immediately and never crashes—even without `DATABASE_URL` or when Neon/PostgreSQL is down (no `RuntimeError`, no `502`).
- **Neon PostgreSQL `hub_results` (Long-Term Memory):** Only entries that pass your filter reach `hub_results (id, created_at, source, model, score, payload JSONB)` via `flush_persist_queue()` (background flusher, e.g., every 60s, at-least-once semantics).
- **Filter Rule (`score REAL`):** The hub passes `score` through untouched without interpreting or filtering it itself; your filter logic decides what gets persisted or read.
- **Limits & Observability:** Max queue cap of 500 entries (evicting oldest entries), status and telemetry via `persist_status` (queue depth, oldest entry, `attempts`, bridge state), and strict table ownership separation between the guardian layer and the app layer.

## Neon Setup Steps (Pure Python Environment)
1. **CLI & Auth:** Use the `neon` CLI if installed in your environment. If a `neon` command opens a browser sign-in prompt, wait for completion there; if it times out or fails, run `neon login` and retry.
2. **Initialize Project (from project root):**
   ```bash
   neon init -y --org-id "{pull_guard_value_}" --project-id {pull_guard_value_} --branch production --services postgres
