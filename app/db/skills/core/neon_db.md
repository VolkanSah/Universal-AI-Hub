---
name: Neon Persistence & Python AI Hub Bridge
description: "Set up Neon PostgreSQL for a Python AI hub using SQLite short-term memory, a restart-safe persist_queue bridge, and filtered long-term storage in hub_results"
tags: neon, postgres, postgresql, sqlite, python, persist, bridge, queue, memory, guardian, observability, cli
---

# Neon Setup & Persistence Bridge (`AGENT.md` — Python AI Hub)

## Architektur-Grundsatz
- **SQLite (Kurzzeitgedächtnis & Shared Hub):** Alle KIs und Tools lesen und schreiben lokal in SQLite ohne Cloud-Wartezeit.
- **SQLite `persist_queue` (Restart-safe Bridge):** `enqueue_persist()` schreibt sofort und fehlertolerant (auch ohne `DATABASE_URL` oder bei ausgefallenem Neon/PG — kein `RuntimeError`, kein `502`).
- **Neon PostgreSQL `hub_results` (Langzeitgedächtnis):** Nur Einträge, die den Filter passieren, landen über `flush_persist_queue()` (Hintergrund-Flusher, z. B. alle 60 s, at-least-once) in `hub_results (id, created_at, source, model, score, payload JSONB)`.
- **Filter-Regel (`score REAL`):** Der Hub reicht `score` unverändert durch und bewertet/filtert ihn nicht selbst; deine Filterlogik bestimmt, was persistiert oder gelesen wird.
- **Limits & Observability:** Queue-Limit max. 500 Einträge (älteste werden verworfen), Status & Telemetrie über `persist_status` (Queue-Tiefe, ältester Eintrag, `attempts`, Bridge-State). Guardian-Layer und App-Layer trennen Tabellen-Ownership strikt.

## Setup-Schritte auf Neon (ohne Node/npm-Zwang im Projekt)
1. **CLI bereitstellen:** Nutze die `neon`-CLI, falls installiert; falls nicht vorhanden, führe sie über `npx neon@latest` aus (kein `npm run build` oder `neon.ts` für die Python-App erzwingen).
2. **Auth prüfen:** Falls ein `neon`-Befehl einen Browser-Login öffnet, den Abschluss im Browser abwarten; bei Timeout oder Fehler `neon login` ausführen und den Schritt wiederholen.
3. **Projekt initialisieren (im Projekt-Root):**
   ```bash
   neon init -y --org-id "{pull_guard_value_}" --project-id {pull_guard_value_} --branch production --services postgres
