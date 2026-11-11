---
name: hub core index
description: "ai hub orchestrierung"
tags: core
---

# duty
- core_skill:     app/db/skills/core/core.db
- how to use it readable for humans : app/db/skills/core/core_agent.md
- how_use for agents_ID=(value): app/db/skills/core/core_agent.db
- hub_tools: `show_core_skills` app/db/skills/core/core_agent.md
- description: "FUCK! restrict ai slop and saves me money"


# User skills
folder:  /app/db/skills/user/*
autoload: true
hub_tools: `show_skills`
vector_files: *.db
human_readable: *.md
