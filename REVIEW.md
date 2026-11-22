# Code-Review & Rebuild — Multi-LLM-API-Gateway v1.1.0

**Datum:** 10.11.2026 · **Basis:** `Multi-LLM-API-Gateway_brocken.7z` + `PyFundaments-Quart-Boilerplate.zip`
**Getestet:** Live-Boot, MCP-Session-Flow, /api-Endpunkte, echtes Pricing-Fetch (OpenRouter + LiteLLM), Fehlerpfade

---

## 1. Kurzfassung

Der Gateway **startete**, aber **MCP war komplett tot** (`POST /mcp` → 404). Zwei verlorene Boilerplate-Konzepte waren die Ursache. Nach dem Fix wurde der Hub um Auto-Model-Pricing erweitert (dein Schmerzpunkt: Kosten nie wieder von Hand pflegen) und um echte Search-Provider.

**Finale Tool-Liste (live getestet):**
`llm_complete` · `web_search` · `list_active_tools` · `health_check` · `get_model_info` · `get_model_pricing` · `refresh_pricing` · `db_query`

---

## 2. Was kaputt war (mit Beweis)

| # | Befund | Beweis / Test |
|---|--------|---------------|
| **A1** | `/mcp` → **404**. Die Mount-Middleware stripte den `/mcp`-Prefix, aber FastMCP registriert seine Route **intern selbst** unter `/mcp` (`settings.streamable_http_path`). Der gestripte Pfad `/` existiert in der inneren Starlette-App nicht. | Minimal-Repro: gestript → 404; Pfad unangetastet → erreichbar. Das Boilerplate hat genau das mit `strip_prefix=False` (Default) abgefangen. |
| **A2** | **Session-Manager lief nie.** `mcp.lifespan()` fehlte komplett, `serve()` lag nicht im `async with`. | Ohne lifespan: `RuntimeError: Task group is not initialized` bei jedem Request (nachgewiesen). |
| **A3** | **SSE-Fallback tot.** `mcp.handle_request()` rief `_mcp.handle_sse(request)` — diese Methode existiert im MCP-SDK 1.27 nicht. | SDK-Inspektion. |
| **A4** | `tools.run()` verlangte `prompt` positional → `/api` ohne `prompt` = roher 500er. | Live-Test. |
| **A5** | `providers.search()` war ein Stub („not yet implemented") — Brave/Tavily standen in der `.pyfun`, `web_search` registrierte sich nie. | Boot-Log: „No active search providers". |
| **A6** | `requirements.txt` pinnte `fastmcp` — dein Code importiert aber `mcp.server.fastmcp` (das **mcp**-SDK). Funktionierte nur zufällig, weil fastmcp mcp als Dep nachzieht. | `pip show fastmcp` → `mcp<2.0,>=1.24.0`. |
| **A7** | Leichen: `mcp_old.py` (379 Zeilen), leere `example.py`, `app/web/`, `app/tmp/`, und `app/web/tmp/__init__py` **ohne Punkt** im Dateinamen. | Datei-Inspektion. |
| **A8** | `.pyfun`: `[MODEL.gemini-2.5-flash-lite]` wurde mit `[MODEL.gemini-2.0-flash-lite_END]` geschlossen (Parser toleriertes es, war aber falsch); doppelter Header-Kommentar. | Parser-Review. |
| **A9** | `shellmaster.py` war nie verdrahtet — `initialize()` wurde von niemandem gerufen. | Import-Graph. |
| **A10** | CRLF-Zeilenenden überall (Windows-Checkout). | `file`-Check: „with CRLF line terminators". |

## 3. Fundament-Befunde (pyfun) — nur relevant mit `DATABASE_URL`

| # | Befund | Status |
|---|--------|--------|
| **D1** | `UserHandler` erwartet ein SQLite-`Database`-Objekt (`fetchone`/`execute`, synchron) — `main.py` übergab aber den **asyncpg-Pool** → `AttributeError` beim ersten Login. | **Gefixt:** `main.py` baut jetzt `UserHandler(Database())` mit `setup_tables()` — exakt wie im `__main__`-Demo deines eigenen `user_handler.py`. |
| **D2** | `Security.user_login()` awaited eine **synchrone** Methode → `TypeError: bool can't be awaited`. | **Gefixt:** `await` entfernt (UserHandler bleibt synchron/SQLite). |
| **D3** | `Security.check_permission(user_id, name)` rief `has_permission(name)` mit 2 Argumenten auf, obwohl die Signatur nur 1 nimmt → `TypeError`. | **Gefixt:** `has_permission(permission_name, user_id=None)` — optionaler Override, rückwärtskompatibel. |
| **D4** | `AccessControl()` wurde ohne `user_id` gebaut → Permission-Check wäre immer `False` gewesen. | **Mit D3 gelöst** (user_id kann pro Call übergeben werden). |
| **D5** | Gemischte Stores: users/sessions in **SQLite**, Rollen/Permissions in **PostgreSQL**. | **Dokumentiert, nicht geändert** — das ist ein Design-Decision, die dir gehört. |
| **D6** | `main.py` loggte „Database service initialized." obwohl `init_db_pool()` `None` zurückgab. | **Gefixt:** Rückgabewert wird geprüft, Warnung stattdessen. |

> **Hinweis fürs PyFundaments-Repo:** D1–D4 sind im Boilerplate genauso drin. Die Fixes (`main.py`-Wiring, `security.py`-Signaturen, `access_control.py`-Override) kannst du 1:1 übernehmen.

---

## 4. Was im Rebuild passiert ist (Datei für Datei)

### `app/app.py`
- Mount-Middleware zurück auf Boilerplate-Logik: `prefixes`-Tuple, **kein Strip**, `strip_prefix` nur noch bewusster Opt-in.
- `serve()` läuft jetzt in `async with mcp.lifespan():` — der FastMCP-Session-Manager lebt.
- `mcp.mount_prefixes()` bestimmt die Prefixe (streamable-http → `/mcp`; sse → `/sse` + `/messages`).
- `HUB_HOST` wird aus `.pyfun` gelesen (stand vorher auf `0.0.0.0` hardcoded).
- `/api`: saubere Fehlercodes — `ValueError` → 400 (fehlender Prompt, unbekanntes Tool), `KeyError` → 404, `RuntimeError` → 502 (Provider-Kette erschöpft, Message upstream sanitisiert), kaputtes JSON crasht nicht.
- `get_model_pricing` als REST-Tool direkt daneben.
- `shellmaster.initialize()` + `pricing.initialize()` in die Startup-Sequenz.
- Tote SSE-Quart-Route entfernt, ungenutzte Imports (`threading`, `requests`, `time`) raus, `datetime.utcnow()` (deprecated) → `datetime.now(timezone.utc)`.

### `app/mcp.py`
- `lifespan()` + `mount_prefixes()` zurück (mit dem WARUM-Kommentar aus dem Boilerplate).
- Tote `handle_request()`/`handle_sse()` entfernt.
- **Neu:** `_register_pricing_tools()` → `get_model_pricing` + `refresh_pricing`.
- `get_model_info` ist jetzt async und liefert **gemergte** Kosten (.pyfun gewinnt, Auto-Werte füllen).
- `_register_db_tools()` aktiviert — alle drei im Kommentar genannten Bedingungen sind erfüllt.
- `list_active_tools` zeigt jetzt `pricing_stats`.

### `app/pricing.py` — **NEU** (dein Schmerzpunkt)
- **Das Problem:** Nicht jeder Anbieter hat eine Pricing-API (Anthropic & Google: keine). Kosten von Hand in `[MODELS]` pflegen nervt.
- **Die Lösung:** Zwei freie, öffentliche Quellen + deine `.pyfun` als Override:

| Priorität | Quelle | Deckt | Getestet |
|-----------|--------|-------|----------|
| 1 | `.pyfun [MODELS]` | Deine manuellen Werte — **gewinnen immer** | ✅ `claude-opus-4-6` → 0.015/0.075 $/1k aus deiner Config |
| 2 | OpenRouter `/api/v1/models` (öffentlich, kein Key) | Alle OR-Modelle, per-Token-Preise, Context-Längen | ✅ 465 Modelle live geholt |
| 3 | LiteLLM `model_prices_and_context_window.json` (Community-Standard) | Anthropic, Gemini, OpenAI, Mistral, … | ✅ 4615 Modelle live geholt |

- **Merge-Logik:** `.pyfun` > OpenRouter > LiteLLM. Fehlt beides → `source: "unknown"`, kein Crash.
- **Matching:** exakt normalisiert (lowercase, Vendor-Prefixe wie `anthropic/`, `gemini/` gestrippt) + Loose-Match gegen Punkt/Strich-Differenzen (OR schreibt `claude-opus-4.6`, du `claude-opus-4-6` — beides findet sich).
- **Caching:** landet in `db_sync` (`hub_state["pricing.cache"]`), TTL aus `.pyfun` (Default 24 h). **Restart innerhalb der TTL = null externe Calls** (live getestet).
- **Nicht-blockierend:** Fetch läuft als Background-Task — der Server startet sofort, auch wenn beide Quellen down sind.
- **MCP/REST:** `get_model_pricing(model_name)` liefert alles gemergt; `refresh_pricing()` erzwingt den Refresh.

### `app/providers.py`
- **Neu:** `BaseSearchProvider` + `BraveSearchProvider` (Auth via `X-Subscription-Token`, **nicht** Bearer) + `TavilySearchProvider` (Key im Payload, `include_answer`-Support).
- `_initialize_search()` mit demselben Key-Gating wie die LLMs: kein Key = Provider übersprungen = kein Crash.
- `search()` mit Fallback-Kette via `fallback_to` (brave → tavily), mit Visited-Set gegen Endlosschleifen.
- Fehler sanitisiert wie bei LLMs: nur HTTP-Status + safe_url, niemals Keys/Bodies.

### `app/tools.py`
- `prompt` jetzt optional + `**params`-Fänger → unbekannte Parameter crashen nicht mehr.
- Fehlender Prompt → sauberer `ValueError` → 400.

### `app/config.py`, `app/models.py`
- `get_pricing()` für die neue `[PRICING]`-Sektion.
- `models.get()` fällt auf die rohe `.pyfun` zurück, falls das Registry noch nicht initialisiert ist.

### `app/.pyfun`
- **Neu:** `[PRICING]`-Sektion (Enabled, TTL, beide Quell-URLs).
- END-Marker-Fix + Doppel-Header entfernt.
- `HUB_VERSION` → `1.1.0`.

### `app/db_sync.py`, `app/shellmaster.py`
- Unverändert bzw. nur verdrahtet — beide waren solide gebaut.
- ShellMaster bleibt **inactive** in `.pyfun` bis Token + `shellmaster_commands.jsonl` + Agent-URL da sind.

### `fundaments/` (minimal)
- `security.py`: D2 + D3 (siehe oben).
- `access_control.py`: D3-Override.
- `main.py` (Guardian): D1-Wiring + D6-Log.
- Sonst **keine** Änderungen — dein Fundament bleibt deins.

### Projekt
- Entfernt: `mcp_old.py`, `example.py`, `app/web/`, `app/tmp/`.
- `requirements.txt`: `fastmcp` → `mcp>=1.10,<2`.
- `Dockerfile`: `EXPOSE 7860`.
- `example-mcp___.env`: aufgeräumt (stale `APP_MODE`/`MCP_TRANSPORT` raus, alle Provider-Keys dokumentiert).
- Alle Dateien auf LF normalisiert (40 Dateien, CRLF kam aus dem Windows-Checkout).

---

## 5. Live-Test-Ergebnisse (alle bestanden)

```
✅ Boot ohne ENV-Keys          → Server startet, 3 System-Tools, kein Crash
✅ Boot mit Dummy-Keys         → anthropic + brave registrieren sich, 8 Tools
✅ MCP initialize              → JSON-RPC Response mit serverInfo
✅ MCP tools/list              → 8 Tools
✅ MCP tools/call (pricing)    → gemini-2.5-flash: .pyfun-Werte gewinnen
✅ MCP tools/call (db_query)   → SELECT auf hub_state funktioniert
✅ Pricing-Quellen             → OpenRouter 465 + LiteLLM 4615 Modelle live
✅ Pricing-Lookup              → .pyfun-Override ✅ / Auto-Fill ✅ / Unknown ✅
✅ Cache-TTL                   → Restart: „cache fresh — no external fetch needed"
✅ Fallback-Kette              → anthropic 403 → Kette durch → RuntimeError, KEIN Key im Log
✅ /api Fehlerpffade           → fehlender Prompt 400, unbekanntes Tool 400,
                                 Schreib-SQL blockiert, kaputtes JSON 400
```

---

## 6. Wie du weitermachst

```bash
# 1. .env anlegen
cp example-mcp___.env .env   # dann Keys eintragen

# 2. Starten
python main.py

# 3. Kosten abfragen (macht jetzt der Hub selbst)
curl -X POST localhost:7860/api -H "Content-Type: application/json" \
  -d '{"tool":"get_model_pricing","params":{"model_name":"gemini-2.5-flash"}}'

# 4. Preise erzwingen zu aktualisieren
curl -X POST localhost:7860/api -H "Content-Type: application/json" \
  -d '{"tool":"refresh_pricing"}'
```

**Du kannst in `.pyfun [MODELS]` jetzt alle `cost_*`-Zeilen löschen** — sie werden automatisch gefüllt. Einträge, die du setzt, gewinnen weiterhin als Override (z. B. wenn du Sonderkonditionen hast).

**Nächste sinnvolle Schritte:** echte Keys testen · `persist_result` mit echter PostgreSQL-Tabelle · ShellMaster aktivieren (Token + Registry-Datei + Agent) · Auth-Layer vor `/api` und `/mcp` legen.

---

## 7. Debug- & Secrets-Audit (nachgetragen am 10.11.2026)

**Anlass:** Nutzer-Hinweis auf die Debug-Einstellungen in `.pyfun` und `.env` — die fehlte im ersten Review. Nachgetragen, mit Beweis.

### Secrets — wo auch immer gesucht, nichts gefunden

| Prüfung | Ergebnis | Beweis |
|---------|----------|--------|
| Echte API-Keys im Work-Tree | ✅ keine | `.pyfun` enthält nur ENV-**Namen** (`env_key = "ANTHROPIC_API_KEY"`), `shellmaster_token = ""` |
| Echte API-Keys in **gesamter Git-History** (~30 Commits) | ✅ keine | Alle Muster-Treffer sind `...`-Platzhalter in Doku-Kommentaren (`sk-ant-...`, `hf_...`, `BSA...`, `tvly-...`) |
| `.env` je committed | ✅ nein | `git log --all --diff-filter=A` — nur `example-mcp___.env` (Platzhalter) |
| Logs über HTTP ausgeliefert | ✅ nein | `app.py` hat nur `/`, `/api`, `/crypto` — keine Log-Route |
| Prompts/Responses/Bodies in Logs | ✅ nein | `grep` über alle `logger.*`-Aufrufe in `app/` — nur Status, Registrierungen, sanitized URLs |

### Debug-Schalter — wer liest sie WIRKLICH?

| Schalter | Wo gelesen | Default | Tatsächliche Wirkung |
|----------|-----------|---------|---------------------|
| `PYFUNDAMENTS_DEBUG` | `.env` → `main.py` + `fundaments/debug.py` | `false` | Startup-Diagnose (Python-Version, `sys.path`, Datei-Checks) |
| `LOG_LEVEL` | `.env` | `INFO` | Log-Tiefe |
| `LOG_TO_TMP` | `.env` | `false` | Logs nach `/tmp` (nur Host-lokal, nicht öffentlich) |
| `ENABLE_PUBLIC_LOGS` | `.env` | **`true`** | `false` = nur CRITICAL. **Achtung HF Spaces:** stdout-Logs sind dort im Log-Tab **öffentlich einsehbar** — für öffentliche Spaces `false` setzen |
| `.pyfun [DEBUG]`-Sektion | **niemand** | — | **TOTE CONFIG** — kein `app/*`-Code liest sie (Stand v1.1.0, per `grep` über `app/` bewiesen) |

### Was daraus bereinigt wurde

- **`.pyfun [DEBUG]`**: Stand auf `DEBUG="ON"`, `DEBUG_LEVEL="FULL"`, `LOG_RESPONSES="true"` — wirkungslos heute, aber eine **Landmine**: wäre die Sektion später verdrahtet worden, hätte sie sofort sensitiv geloggt. Jetzt: `OFF`/`false`-Defaults + „not wired yet"-Hinweis im Header.
- **`example-mcp___.env`**: shippte Dev-Defaults (`PYFUNDAMENTS_DEBUG="true"`, `LOG_LEVEL="DEBUG"`, `ENABLE_PUBLIC_LOGS="true"`). Template jetzt produktions-sicher (`false`/`INFO`/`false`) + Warnhinweis zum öffentlichen HF-Spaces-Log-Tab. Für die Testphase einfach in deiner lokalen `.env` umschalten.
- **Nicht geändert (Empfehlung):** Der Code-Default `ENABLE_PUBLIC_LOGS=true` in `main.py`/`fundaments/debug.py`. Für öffentliche HF Spaces wäre `false` der sicherere Default — aber das ist dein Fundament, deine Entscheidung. Eindeutig dokumentiert in der `.env`-Vorlage.

> **Wichtig fürs Verständnis:** Runtime-Verhalten hat sich durch die Bereinigung **nicht** geändert — die `[DEBUG]`-Sektion war tot, und die `.env`-Vorlage wirkt erst, wenn du sie kopierst. Geändert haben sich nur die Defaults, mit denen das Projekt ausgeliefert wird.

---

## 8. Update 2 (v1.2.0): Auto-Pricing konsequent, Gedächtnis-Orchestrierung, ShellMaster ROT

### 8.1 Die Pricing-Frage: Brauchen wir `[MODELS]`-Kosten noch?

**Antwort auf die OpenRouter-Frage (belegt, OpenRouter-FAQ live):**
> *„We charge a small fee when purchasing credits. We never mark-up the pricing of the underlying providers, and you'll always pay the same as the provider's listed price."*

- **Per-Token = Original-Listenpreis.** Kein Markup. Du irrst dich NICHT.
- **ABER:** ~5,5 % Gebühr beim **Kredit-Kauf** (Stripe, $0,80 Min; Coinbase 5 %) — effektiv also Listenpreis + Kaufgebühr, nicht pro Token.
- Wer mit eigenem `ANTHROPIC_API_KEY` direkt callt, zahlt ohnehin Vendor-Listenpreis → gleiche Zahlen.

**Konsequenz umgesetzt:** 34 `cost_*`-Zeilen aus `[MODELS]` entfernt. Übrig bleiben nur die 4 Zeilen der Custom-Modelle (`smollm2-360m`, `codey-lab/...` = eigenes Deployment, 0.0 ist dort die Wahrheit — keine externe Quelle kennt sie). **Die Sektion selbst bleibt** — `requests_per_min/day`, `context_tokens`, `capabilities` und das Provider-Mapping gibt es in keiner Preisquelle.

**Live bewiesen (26/26 Checks):** Alle 17 übrigen Modelle füllen Kosten automatisch:
`claude-*` → litellm (z. B. opus 0.005/0.025 $/1k) · `gemini-3.*-preview` → openrouter · `mistral-7b` → litellm (**OpenRouter hat es aus dem Katalog genommen** — der Fallback greift genau richtig) · `smollm2-360m` → pyfun/0.0 · unbekannt → unknown.

> ⚠️ Beachte: `claude-opus-4-6` zeigt jetzt **0.005/0.025** (LiteLLM) statt deiner alten 0.015/0.075. Prüfe das gegen deine echte Rechnung — falls du andere Konditionen hast, einfach die `cost_*`-Zeilen für DIESES Modell wieder eintragen (Override gewinnt immer).

**Kleiner Fix nebenbei:** `.pyfun`-Kosten kamen als String (`"0.00000"`), Auto-Werte als Float — `get_pricing()` normalisiert jetzt alles auf Float. Und `/api list_active_tools` liefert jetzt auch `pricing_stats` (vorher nur die MCP-Version).

### 8.2 Gedächtnis-Orchestrierung — dein Architektur-Wunsch, umgesetzt

**Dein Design:** SQLite = Kurzzeit-Gedächtnis (alle KI-Tools lesen/schreiben) · Wichtiges → PostgreSQL = Dauer-Gedächtnis · Auswahl was wichtig ist = **dein** Bullshit-Filter (deine Sache).

```
persist_result (MCP + /api)
   ↓ enqueue_persist()        ← sofort, IMMER, kein Crash
persist_queue (SQLite)        ← überlebt Restarts (BEWIESEN: 3 vor, 3 nach Reboot)
   ↓ flush_persist_queue()    ← Background-Flusher, Retry alle 60 s (config)
PostgreSQL hub_results        ← CREATE TABLE IF NOT EXISTS + INSERT
```

| Eigenschaft | Umsetzung |
|---|---|
| Ohne `DATABASE_URL` | `status: "queued"` statt RuntimeError/502 — **kein Crash mehr** (vorher Befund!) |
| PG temporär weg | Eintrag bleibt, `attempts`+1, sanitisierter `last_error` (nur Exception-Typ) |
| PG wieder da | Flusher (5 s nach Boot, dann Intervall) holt alles nach |
| Schema | `hub_results(id, created_at, source, model, score, payload JSONB)` |
| **Score-Slot** | `score REAL` — Spalte + Parameter durchgereicht bis PostgreSQL, **uninterpretiert**. Dein Filter schreibt später einfach mit `score` mit oder filtert beim Lesen. Kein Hub-Code rührt ihn an. |
| Observability | `persist_status` (MCP + /api): queue_depth, ältester Eintrag, Fehlversuche, Bridge-Status |
| Grenzen | Queue max. 500 (älteste fliegen raus, wie `tool_cache`) · at-least-once (Duplikat statt Verlust) · HF Spaces: `/tmp`-SQLite überlebt Container-Reset nicht — dort ist PG das einzige Dauer-Gedächtnis |

**Live bewiesen:** queued ohne PG ✅ · score 0.85 + source + model landen korrekt ✅ · `db_query` liest die Queue ✅ · MCP registriert `persist_result` + `persist_status` (8 Tools) ✅ · **Restart: 3 Einträge überleben** ✅

### 8.3 ShellMaster = dein CLI-Commander — SECURITY LEVEL ROT

Bestätigt, was du erkannt hast: Er ist für den **lokalen Quart-Agenten** gebaut (`localhost:5004`, Bearer-Token, Befehle auf DEINEM Rechner). Schutzmuster im Code: Hardblock-Liste (rm -rf, mkfs, …) + JSONL-Registry + **explizite Bestätigung** vor jeder Ausführung + Recovery-Plan im Gedächtnis.

Trotzdem gilt: **Stufe rot, nur lokal.** Dokumentiert in `.pyfun` (Kommentar-Block über `[TOOL.shellmaster]`). Zusätzlich entschärft: Token-Field in `.pyfun` war eine Einladung, ein Secret in die Config zu legen — jetzt gibt es `shellmaster_env_key = "SHELLMASTER_TOKEN"` (→ `.env`, exakt wie bei allen Providern), das `.pyfun`-Feld ist nur noch lokaler Test-Fallback. `active` bleibt `"false"`.

---

*Rebuild nach PyFundaments-Architektur: Guardian → Orchestrator → sandboxes app/* · No key = no tool = no crash.*
