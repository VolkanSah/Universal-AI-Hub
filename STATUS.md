#### THX to Claude 5.5 lost the overvies of this projekt, no time 😸

## Wie ich es verstehe

`main.py` (Guardian) baut optional die Fundaments (Postgres-Pool, AES-GCM, RBAC, User/Session, Security-Fassade) und reicht sie als Dict an `app/app.py` weiter. Das ist ein Quart/Hypercorn-Server mit REST `/api` und FastMCP `/mcp`. Das Verhalten kommt aus `app/.pyfun` (Parser: `config.py`): Provider mit Fallback-Kette, Modelle, Tools, Limits. `tools.run()` verteilt nach `provider_type`, `db_sync.py` ist SQLite-Zwischenspeicher plus Postgres-Bridge. Der Desktop-Client spricht nur REST.

**Nachweislich gelaufen** (Log 14.03.): Neon-Pool, Provider-Registry nach Key, Fallback anthropic → gemini/smollm. Der `.pyfun`-Parser ist solide (getestet: 19 Modelle, 9 aktive Tools, der Zyklus in der Fallback-Kette wird per `visited` abgefangen).

## 1. Startet aktuell nicht
- `from . import providers` steht in app.py, mcp.py und tools.py, die Datei heißt aber `provider.py`. Das gibt einen ImportError (**getestet**), vermutlich ein Rename nach dem 14.03.
- In requirements.txt fehlt `aiosqlite` (hartes Import in db_sync). `mcp` fehlt auch: Der Code importiert `mcp.server.fastmcp`, gelistet ist aber das andere Paket `fastmcp`. Pins gibt es keine.

## 2. README verspricht, Code liefert nicht
- **Suche:** Brave/Tavily sind nicht implementiert. `search()` ist ein Platzhalter, `list_active_search()` gibt `[]` zurück, also wird `web_search` nie als MCP-Tool registriert (**gelesen**).
- **MCP-Tools:** „Tool einmal definieren, beide Interfaces“ stimmt nicht. MCP registriert hart nur `llm_complete` und 3 System-Tools. `code_review`, `summarize`, `translate`, `db_query` und `persist_result` gehen nur über REST.
- **Streamable-HTTP-Mount (Vermutung, hier nicht testbar):** Die Middleware schneidet `/mcp` ab, FastMCP erwartet aber die Route `/mcp`, das gäbe 404. Außerdem wird das Lifespan des Sub-Apps (Session-Manager) nie gestartet. Im Log ist kein MCP-Request erkennbar. Der SSE-Fallback ruft `_mcp.handle_sse` auf, das es nicht gibt.
- **Tote Config (per grep geprüft):** Retries, Parallelität, `timeout_sec`, Modell-Limits/Kosten, `tool_cache` und `LOG_REQUESTS` werden nirgends gelesen. „`.env` überschreibt `.pyfun`“ ist nicht implementiert.
- **Fallback-Bugs:** Das `model` des ersten Providers wird an den Fallback weitergereicht (claude-… an Gemini gibt 404). `system` nutzt nur Anthropic, bei allen anderen verschwindet es.

## 3. Auth-Schicht: nicht verdrahtet und in sich kaputt
- `/api` und `/mcp` haben keine eigene Auth, nur den HF-Private-Space. Die Fundaments-Security wird in `app/` nie benutzt.
- **Getestet:** `login()` liefert True, aber `is_logged_in()` und `validate_session()` liefern False, weil die Session-ID nur im Speicher regeneriert wird und die DB die alte behält.
- **Getestet:** `Security.user_login` macht `await` auf eine sync-Methode (TypeError). `check_permission` ruft `has_permission` mit 2 statt 1 Argument auf (TypeError).
- **Gelesen:** `UserHandler` bekommt in main.py den asyncpg-Pool statt der SQLite-`Database`. `setup_tables()` wird nie aufgerufen, ein einziges `_session` gilt für alle Nutzer, und die RBAC-Tabellen-DDL fehlt im Repo.

## 4. Sicherheit (das wichtigste Kapitel)
- **healer:** `heal_code` und `heal_task` sind `active=true`, aber ihr Dispatch-Block steht hinter einem `return` (**getestet**: Antwort „not yet implemented“). Nicht einfach „reparieren“: `healer.py` führt Code per `subprocess` ohne Sandbox und mit vollem Environment (alle API-Keys) aus. Zusammen mit dem offenen `/api` wäre das Remote Code Execution. Dein eigenes Memo im File sagt „nicht implementiert, wird wohl nicht“. Außerdem scheitert dort der LLM-Call im Worker-Thread (**getestet**: `no current event loop`).
- **db_query:** Es prüft nur `startswith("SELECT")`, eine Tabellen-Allowlist gibt es nicht. `users` ist nur deshalb nicht lesbar, weil `cms_database.db` und `.hub_state.db` standardmäßig getrennte Dateien sind. README und Docs behaupten „gleiche Datei, geschützt“.
- **Sandbox-Claim:** „app/* liest nie `os.environ`“ (README, SECURITY.md) stimmt nicht. `provider.py` liest Keys, `APP_URL` und `HUB_NAME` per `os.getenv`, db_sync liest `SPACE_ID`, healer `HEALER_*`. Im selben Prozess ist das ohnehin nur Konvention, keine Erzwingung. Entweder Secrets aktiv injizieren oder die Aussage abschwächen.
- **Logging (getestet):** `LOG_LEVEL` und `ENABLE_PUBLIC_LOGS` wirken nicht, weil `postgresql.py` beim Import `basicConfig` aufruft. Wird das gefixt, loggt `LOG_LEVEL=DEBUG` (steht so in der Env-Vorlage) das DSN mit Passwort.
- **Desktop-Client:** Der HF-Token liegt im Klartext in `mcp_desktop.json` neben dem Skript und ist nicht in `.gitignore`. Bilder werden still verworfen, und jeder Prompt ist Single-Turn, die History geht nie mit.

**Kleinkram:**
- In `.pyfun` passt bei OpenRouter und HF keiner der 11 deklarierten Modellnamen zu einem `[MODEL]`-Block, und bei gemini-2.5-flash-lite ist der END-Tag falsch.
- Die Claude-Modelle und Preise sind vermutlich veraltet, die HF-`base_url` solltest du prüfen.
- Die Docs widersprechen sich: config_handler.md sagt „fail-fast“, mcp.md „SSE“, dazu `providers.py` vs `provider.py`, `mcp_desktop.py` vs `hub.py` und ein Müll-Tag am Ende von PyFundaments.md.

## Mein Vorschlag für den Weg
- **Phase 1, läuft ehrlich:** Rename, requirements, heal-Tools aus, Logging-Fix, Doku-Abgleich.
- **Phase 2, Features:** Brave/Tavily, dynamische MCP-Registrierung aus `.pyfun`, MCP-Mount testen, Fallback- und `system`-Fixes.
- **Phase 3, Guardian-Entscheidung:** Auth wirklich verdrahten (Bearer-Check in `/api` und `/mcp`) oder die kaputte User/Session-Schicht streichen.

Phase 3 hängt davon ab, was „fertig“ für dich heißt: ein persönlicher Hub auf HF mit Desktop-Client, ein Showcase der PyFundaments-Architektur oder ein Template zum Forken?

