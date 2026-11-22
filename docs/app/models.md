## Datei: `app/models.py`

**Beschreibung:** Dieses Modul verwaltet die Metadaten, Limits und Kostenstrukturen der verschiedenen KI-Modelle. Es dient als zentrale Informationsquelle für die Kapazitäten der einzelnen Modelle innerhalb des Hubs.

### Hauptfunktionen:

* **`initialize()`**: Initialisiert das Modell-Register beim Start, indem es die Definitionen aus der `.pyfun`-Konfiguration lädt.
* **`get()` & `get_limit()**`: Ruft die vollständige Konfiguration oder spezifische Einzelwerte (wie Limits) eines bestimmten Modells ab.
* **`for_provider()`**: Filtert und liefert alle Modelle, die einem spezifischen Provider (z. B. Anthropic oder Gemini) zugeordnet sind.
* **Spezifische Getter (`max_tokens`, `context_size`, `cost_input/output`)**: Bequeme Hilfsfunktionen, um technische Grenzen (Token-Limits) und ökonomische Daten (Kosten pro 1k Token) direkt abzufragen.
* **`list_all()`**: Gibt eine Liste aller im System registrierten Modellnamen zurück.

### Kern-Logik:

Das Modul fungiert als **Data-Access-Layer** für Modell-Spezifikationen. Es enthält selbst keine Logik zur Textverarbeitung, sondern stellt sicher, dass andere Module (wie `providers.py` oder `mcp.py`) wissen, wie viel Kontext ein Modell verträgt oder welche Kosten bei der Nutzung anfallen.
