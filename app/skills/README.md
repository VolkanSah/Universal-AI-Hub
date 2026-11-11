# app/skills/ — System-Prompts als Dateien

Eine Markdown-Datei hier = ein "Skill" für ein LLM-Tool. In `app/.pyfun`:

    [TOOL.mein_tool]
    provider_type      = "llm"
    system_prompt_file = "mein_skill.md"     # wird NACH system_prompt angehängt (falls beides gesetzt)

- Nur `.md` / `.txt`, nur direkt in diesem Ordner (kein `../`), max. 20 000 Zeichen.
- Es wird **nur Text gelesen** und als `system`-Prompt gesendet. Aus diesem Ordner wird nie Code
  importiert oder ausgeführt — eine Datei hier kann nichts ausführen.
- Änderungen werden nach einem Neustart wirksam (Cache). Fehlende Datei = Warnung beim Start.
- Skills von Dritten (z. B. Neons Agent-Skills) sind Anleitungen für Coding-Agents und gehören
  nicht ungeprüft hierher — vorher lesen.
