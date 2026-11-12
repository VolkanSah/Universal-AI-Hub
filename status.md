# Universal AI Hub — Übergabe & Status
zusammengefasst von claude nachdem ich ihm meine repo gab. 

> **Für die KI: zuerst komplett lesen, dann erst antworten.**
> Teil A–D ändern sich selten (Regeln, Architektur, Entscheidungen). Teil E–G sind der aktuelle Stand.
> Zuletzt aktualisiert: 12.11.2026 · Pflege: am Ende jeder Sitzung „status.md aktualisieren“ sagen.

---

## A. Wie hier gearbeitet wird (Regeln des Besitzers)

Volkan ist erfahren, nicht Anfänger — nur bewusst bequem. So lief die Zusammenarbeit bisher gut:

- **Deutsch, kurz, locker. Keine Romane.** Erst das Ergebnis, dann höchstens ein paar Zeilen Erklärung.
- **Seine Architektur wird nicht umgebaut.** Bedenken in 1–2 Sätzen sagen, dann trotzdem nach seinem Weg arbeiten.
- **`fundaments/` nur mit ausdrücklicher Freigabe anfassen.** `postgresql.py` enthält seinen Neon-SSL-Workaround.
- **Sagt er „kein Code, wir denken“, wird nur geplant** — nichts bauen.
- **Ehrlich markieren, was nicht live getestet ist.** Nichts als „läuft“ ausgeben, was nur gegen Stubs lief.
- **Vor dem Bauen den Repo-Stand abgleichen** (er lädt das ganze Projekt als Zip hoch): diffen, nicht annehmen.
  Ergebnis immer als Zip auf *seinem* Stand ausliefern.
- **Token-Limit ist knapp** → keine Zusatzprojekte ungefragt; nach dem Liefern auf seinen Test warten.
- **Secrets nie ausgeben** (Tokens, Keys) — maskieren. Er schickt Logs; daraus arbeiten.

---

## B. Zweck & Architektur (nicht neu erfinden)

**Zweck:** Ein Gateway für viele LLM- und Such-Provider. Zwei Eingänge: REST `/api` und MCP `/mcp`.
Läuft als HF Space (Docker, Python 3.10), optional mit Neon-Postgres als Langzeitgedächtnis.

**PyFundaments:** `main.py` (Guardian) startet die Services aus `fundaments/` und übergibt sie nur an
`app.start_application()`. `app/*` ist die Sandbox.

**Unverrückbare Prinzipien**
1. `app/.pyfun` ist die **einzige** Konfigurationsquelle (Parser: `app/config.py`).
2. **Kein Key = kein Provider = kein Tool = kein Crash.** Der Hub startet immer, nur mit weniger.
3. **Ohne Datenbank muss alles weiterlaufen** (SQLite-State; Neon ist optional).
4. `app/*` importiert **nie** `fundaments/*`. Secrets nur in `.env` / Space-Secrets; in `.pyfun` stehen nur ENV-**Namen**.
5. Der Hub **wartet nie auf PostgreSQL im Request-Pfad** und stürzt bei DB-Problemen nicht ab.

**Eingänge**

| Pfad | Zweck | Schutz |
|---|---|---|
| `/` | Health für HF | offen |
| `/api` | REST: `{"tool": "...", "params": {...}}` | Token, wenn `HUB_API_TOKEN` gesetzt |
| `/mcp` | MCP (Streamable HTTP) | Token, wenn gesetzt |
| `/discord` | Discord-Interactions | Ed25519-Signatur (inaktiv) |

---

## C. Dateikarte & Rezepte

**`app/`**

| Datei | Aufgabe |
|---|---|
| `app.py` | Quart-Server, `/api`, `/discord`, Startreihenfolge |
| `providers.py` | LLM- + Search-Provider, Fallback-Kette, Retry, Usage-Logging |
| `tools.py` | Tool-Dispatcher, Skills-Injektion, Cache, `health()` |
| `mcp.py` | MCP-Tool-Registrierung |
| `config.py` + `.pyfun` | Parser + Konfiguration |
| `models.py`, `pricing.py` | Modell-Registry; Auto-Preise (OpenRouter + LiteLLM), Katalog, Stale-Check |
| `db_sync.py` | SQLite-State, Persist-Queue → Neon, `usage_log`, Neon-Resilienz |
| `auth.py` | optionaler Token-Schutz |
| `skills_db.py` | liest `app/db/skills/**/*.db` (nur lesend, BM25) |
| `discord_api.py`, `gsc.py` | Integrationen (inaktiv) |
| `shellmaster.py` | gehärtet, **nicht verdrahtet** |
| `skills/` | Markdown-Prompts (`system_prompt_file`) |
| `db/skills/` | Skill-Datenbanken (`core/core.db`, `core/core_agent.db`, `user/`) |

Außerdem: `DESKTOP_UI/hub.py` (PySide6-Client), `local_tools/vector_hub/` (baut die Skill-DBs lokal),
`IDEAS_soon/` (Ideen, nicht im Betrieb).

**Rezepte**
- *Neuer OpenAI-kompatibler Provider:* Block `[LLM_PROVIDER.name]` in `.pyfun` mit `type = "openai_compat"`, `base_url`,
  `env_key`, `default_model`, `fallback_to`; Key in `.env`. Kein Python. Extra-Header: `header.X-Name = "{key}"`
  oder `{env:NAME|fallback}`. Auth abweichend: `auth_header`, `auth_scheme`, `chat_path`.
- *Anderes API-Format:* Klasse in `providers.py` + Eintrag in `_TYPE_CLASSES` (Vorbild: Anthropic, Gemini).
- *Neues Modell:* `[MODEL.<echte-API-ID>]` mit `provider`. Context/Preise kommen automatisch; nur Eigenes manuell
  (z. B. eigener Server). Gültige OpenRouter-IDs: Tool `openrouter_catalog`.
- *Neues Tool:* `[TOOL.name]` mit `provider_type` (`llm|search|db|persist|skills|discord|gsc`). Über `/api` sofort nutzbar;
  für MCP in `mcp.py` registrieren (Vorbild: `_register_skills_tools`).
- *Neuer Skill:* Markdown nach `app/skills/` → `system_prompt_file = "x.md"` im Tool-Block. Eigene Vektor-Skills:
  mit `local_tools/vector_hub/secure_vectorizer.py` bauen → `app/db/skills/user/*.db`.

**Parser-Eigenheiten (`.pyfun`)**: jeder Block braucht sein `_END`; Inline-Kommentar beginnt bei ` #`;
`[MODEL.<id>]` wird am **ersten** Punkt geteilt (IDs mit `/ : .` sind erlaubt); Schlüssel `header.X` werden durchgereicht.

---

## D. Entscheidungen (damit nicht neu gedacht werden muss)

| Entscheidung | Warum |
|---|---|
| Provider per `type` + generischer `openai_compat`-Core | spart Code; neuer Provider = ein Block |
| Modelle von Hand in `[MODELS]` mit echter API-ID, **nicht** automatisch aus OpenRouter generiert | OpenRouter liefert nur Preise/Limits; Auswahl bleibt bei Volkan |
| `model` gilt nur für den ersten Provider der Kette | Modell-IDs sind providerspezifisch |
| `system_prompt` als echtes `system` | vorher im User-Prompt; Caching lief ins Leere |
| Retry nur bei 429/5xx/Verbindungsfehler, nicht bei Timeouts | sonst 60 s × N pro Provider |
| `core.db` nie über Tools abrufbar | enthält seine Restriktionen; Abruf würde Umgehung erleichtern |
| Skills-Injektion **aus** (Default) | `core` ≈ 1.700 Tokens pro Call; bei Kleinmodellen teuer |
| Skills nur BM25 (keine Vektoren im Hub) | keine Embedding-Abhängigkeit im Space |
| ShellMaster nicht verdrahtet | Ausführung würde auf dem **Space** laufen, nicht auf seinem Rechner |
| DuckDuckGo: ehrlicher User-Agent, Cooldown wirft Fehler | Tarnung/„Mensch-Simulation“ wird nicht gebaut; Fehler statt Fake-Ergebnis |
| PG-Schreibzugriffe mit Timeout + Backoff + Queue | Neon schläft nach ~5 Min; Hub darf nie hängen |
| Dauer-Ping (`PG_KEEPALIVE_SEC`) aus | hält Neon wach, kostet Compute-Stunden |

---

## E. Aktueller Stand (läuft laut Startlog vom 11.11.)

- Provider aktiv: `smollm`, `gemini`, `openrouter`, `huggingface` (`anthropic` ohne Key übersprungen).
  Suche: `tavily` (`brave` ohne Key). Auth ist **AN**.
- Tools: `llm_complete`, `code_review`, `summarize`, `translate`, `web_search`, `db_query`, `persist_result`,
  `show_skills`, `search_skills`. System-Tools: `list_active_tools`, `health_check`, `get_model_info`,
  `get_model_pricing`, `refresh_pricing`, `usage_stats`, `openrouter_catalog`, `persist_status`.
- Skills-DB geladen: `core=1 agent=1 user=0` (core-Text 6648 Zeichen).
- Neon-Pool verbindet beim Start (die „SSL not active“-CRITICAL-Zeile ist bekannt und harmlos — Workaround).
- Warnung beim Start: 7 OpenRouter-Modelle in `[MODELS]` nicht im Live-Katalog (siehe F).
- **Tests:** 212 automatische Prüfungen (Testkarte in Teil H) liefen in der KI-Sandbox gegen Stubs — **nicht im Repo**.
  **Live bestätigt (Log 11.11., 20:52–21:26):** Start; LLM-Call mit Fallback (`anthropic` ohne Key → `gemini`); Usage-/Kosten-Log
  (≈ $0,000015 pro Test-Call); Kette `openrouter` → `huggingface` → `smollm` → `gemini`; `smollm` antwortet nach manuellem Hochfahren.
  **Nicht live getestet:** Neon nach Schlaf, Discord, GSC, DuckDuckGo, GUI.

**Wichtige Schalter (`.pyfun`)**

| Schalter | Default | Wirkung |
|---|---|---|
| `[HUB] HUB_AUTH_ENV` | `HUB_API_TOKEN` | Name der ENV-Variable für den Token |
| `MAX_TOKENS_CAP` | 8192 | harte Obergrenze für `max_tokens` |
| `RETRY_COUNT` / `RETRY_DELAY_SEC` | 3 / 2 | Versuche pro Provider / Pause |
| `PG_WRITE_TIMEOUT_SEC` / `PG_BACKOFF_SEC` | 10 / 30 | PG-Limit / Pause nach Verbindungsfehler |
| `PG_REQUEST_WAIT_SEC` | 3 | max. Wartezeit von `persist_result` |
| `PG_KEEPALIVE_SEC` | 0 | aus |
| `SKILLS_INJECT_CORE` / `_RELEVANT` | false | Skills in LLM-Prompts (je Tool: `use_core` / `use_skills`) |
| Tool: `cache = "true"` | aus | Antwort-Cache pro Tool |

---

## F. Offen / im Umbau (Reihenfolge)

1. **Desktop-GUI** an die neue Struktur anpassen: Provider-/Modell-Auswahl, Status-Ansicht (`health_check`,
   `usage_stats`, `openrouter_catalog`), Hub-Token-Feld testen. Die GUI sieht nur Tools aus `list_active_tools`.
2. **OpenRouter-Modelle** neu eintragen (7 gemeldet, auch der Provider-Default `nvidia/nemotron-nano-9b-v2:free`).
   **Log 11.11.: `api-inference.huggingface.co` löst nicht mehr auf** (`ConnectError … No address associated with hostname`)
   → `base_url` von `huggingface` auf `https://router.huggingface.co/v1` ändern (gegen HF-Doku prüfen).
3. **Neon-Test** mit schlafender DB (`persist_result` darf nie hängen; `health_check` zeigt `up` / `no_response`).
4. **ShellMaster planen:** Vorschlag — Hub plant + validiert, die GUI führt lokal nach Bestätigung aus.
5. **User-Skills** selbst vektorisieren (Hub liest bisher nur BM25).
6. **Kosten dauerhaft:** `usage_log` liegt in `/tmp` (geht beim Neustart verloren) → Sync nach Neon über den
   bestehenden Writer (Idee `IDEAS_soon/cost_brige.py`, aber **ohne** zweiten Pool und ohne `fundaments`-Import).
7. **Tests ins Repo** bringen (laufen bisher nur in der KI-Sandbox).

**Aus dem Log vom 11.11. abends (kein Konstruktionsfehler im Hub — alle drei Anfragen kamen an bzw. scheiterten schon am DNS):**
8. **OpenRouter → HTTP 401:** Die Anfrage kam an, der Key wurde abgelehnt. Space-Secret `OPENROUTER_API_KEY` prüfen
   (keine Anführungszeichen/Leerzeichen im Wert, ggf. neu erzeugen). 401 wird bewusst nicht wiederholt.
9. **smollm:** Schläft der Space, kommt 503. Der Retry (3 × 2 s) ist für einen Kaltstart zu kurz — der Fallback greift korrekt.
   Die Antwort enthält kein `usage` → Kosten „n/a“. Option: Tokens schätzen (Zeichen ÷ 4), wenn `usage` fehlt.
   Latenz 12,5 s: Volkans Notiz — der smollm-Server schreibt vor der Antwort ein Dataset; besser im Hintergrund/per Cron
   (liegt im smollm2-Repo, nicht im Hub; Log dort: `smollm2.log`).
10. **Default-Provider:** `[TOOL.llm_complete] default_provider` ist `anthropic` (kein Key) → jede Default-Anfrage loggt eine
    Warnung und fällt auf `gemini` zurück. Auf `gemini` setzen oder Anthropic-Key eintragen.
11. DNS-Fehler (`ConnectError`) werden heute 3× wiederholt (≈ 4 s verschenkt) — könnte als „nicht wiederholen“ gelten.

**Integrationen einschalten (wenn so weit)**
- *Discord:* erst `HUB_API_TOKEN` setzen → `[DISCORD]` ausfüllen, `DISCORD_ACTIVE = "true"`, Tool `discord_post` aktivieren,
  öffentliche URL für `/discord`, `/ask` einmalig bei Discord registrieren (`register_commands`).
- *GSC:* `pip install google-api-python-client google-auth`, `GSC_CREDENTIALS` setzen, `GSC_ACTIVE = "true"`, Tool aktivieren.
- *DuckDuckGo:* `active = "true"` und beim Fallback-Provider `fallback_to = "duckduckgo"` (ungetestet gegen echtes DDG).

---

## G. Fallen & Aufräumen

- Die `.pyfun` im GitHub-Repo war die **alte** (ohne `type=`) → providers würden übersprungen. Immer die aus dem letzten Zip nehmen.
- Space-`.gitignore` hat `app/*` → neue Dateien mit `git add -f` (`auth.py`, `skills_db.py`, `gsc.py`).
- Löschen: `desktop_ui.py` im Hauptordner (veraltet), `IDEAS_soon/discord_api.py` (alte Fassung mit Syntaxfehler und `Bearer`).
- Skill-DBs ohne `*.db-wal` / `*.db-shm` einchecken (vorher `PRAGMA wal_checkpoint(TRUNCATE)`).
- core-Text verweist auf `app/.hub_stats.db` (heißt `.hub_state.db`) und `skills/warloard.md` (existiert nicht).
- `discord_api.py` trägt GPL-3 im Header, das Repo Apache-2 → angleichen.
- Falls noch nicht geschehen: HF-Token rotieren (`mcp_desktop.json` mit Token war im hochgeladenen Zip).
- `requests_per_min/day` in `[MODELS]` werden nur angezeigt, nicht erzwungen.
- Persist-Writes sind nicht exakt-einmal (Abbruch genau nach dem INSERT kann eine Zeile doppeln).
- Die asyncpg-Warnung „Failed to restore the pool connection floor“ nach Neon-Schlaf ist Rauschen, kein Absturz.
- HF-`/tmp` ist flüchtig: SQLite-State und Queue gehen beim Neustart des Space verloren.

---

## H. Testkarte (Stand: alle 212 grün)

Damit nicht jede Sitzung dasselbe neu prüft: **nur die Suite laufen lassen, die zur Änderung passt.**
Vor dem Ausliefern eines Zips einmal alle fünf — Ausgabe auf die Schlusszeile `=== N ok, 0 FAIL ===` kürzen.

| Suite | Prüfungen | Deckt ab | Laufen lassen bei Änderung an |
|---|---|---|---|
| 1 Kern | 74 | Provider-Registry/Typen, Request-Formen (openai_compat, Anthropic, Gemini), Fallback/Retry/Model-Regel, `system`-Prompt, Token-Cap/Clamp, Usage/Kosten, `db_query`-Härtung, Tool-Cache + TTL, Stale-Modelle, Auth-Middleware, ShellMaster-Validierung, REST-Randfälle, `usage_stats` | `providers.py`, `tools.py`, `pricing.py`, `app.py`, `auth.py`, `shellmaster.py` |
| 2 Integrationen | 59 | DuckDuckGo (Parser, Cooldown, Captcha), GSC (Format, Clamps, Credentials, Fehler ohne Leak), Discord (Bot-Auth, Signatur, Replay, `/discord`-Route, `/ask`, `discord_post` + Allowlists), `app/skills/*.md` (Pfad-Tricks), MCP-Registrierung | `discord_api.py`, `gsc.py`, DDG-Teil in `providers.py`, `system_prompt_file` |
| 3 Neon-Resilienz | 26 | hängender Writer, `persist_result` blockiert nicht, Backoff, Single-Flight (keine Doppel-Inserts), Abbruch bei Verbindungsfehler, `pg_status`, `health()`, Keepalive | `db_sync.py` (Flush/Status), `health()` |
| 4 Skills-DB | 37 | echte `core.db`/`core_agent.db` + synthetische User-DB: Laden, kaputte DB übersprungen, BM25/Tags/FTS-Sonderzeichen, `core` nie abrufbar, read-only, Injektion (opt-in), Tools/MCP | `skills_db.py`, Injektion in `tools.py`, `[SKILLS]` |
| 5 Postgres | 16 | Pool-Parameter, Retry-Logik (Reads ja, Writes nur vor dem Senden), 08006-Neustart bleibt | nur `fundaments/postgresql.py` |

**Grenzen:** Alles läuft gegen Stubs (httpx, aiosqlite, Quart, hypercorn, asyncpg, Google-Libs) und ein simuliertes Netz.
Das beweist die Logik, **nicht** das Verhalten bei echtem Neon, Discord, Google, DuckDuckGo und nicht die GUI.

**Regeln:** Neue Funktion → Test in die passende Suite ergänzen, keine neue Suite anlegen. Fehlgeschlagene Prüfung erst
fragen: Code-Fehler oder falsche Testannahme? (Beides kam vor.)

**Ort:** bisher nur in der KI-Sandbox — **nicht im Repo**, nach Sitzungsende weg. Sobald sie als `tests/` im Repo liegen,
hier Pfad + Startbefehl eintragen.

---

## I. Neue Sitzung starten

Hochladen: **diese Datei + aktuelles Projekt-Zip + letztes Startlog** (ggf. Fehlermeldung).
Erste Nachricht z. B.: *„Lies status.md, gleiche das Zip damit ab, dann: …“*.
Ende der Sitzung: *„status.md aktualisieren“* — E, F und G anpassen, A–D nur bei geänderten Regeln/Entscheidungen.
