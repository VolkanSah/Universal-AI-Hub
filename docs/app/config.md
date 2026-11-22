## File: `app/config.py`

**Description:** This module is the central parser for the `.pyfun` configuration file. It serves as the **single source of truth** for the entire app sandbox — converting the custom config format into structured Python dictionaries.

### Main Functions

- **`_parse()`**: The internal core parser. Reads `.pyfun` line by line, ignores comments, and processes nested sections (e.g. `[LLM_PROVIDER.anthropic]`).
- **Caching (`get()`)**: Loads configuration into an internal cache on first access to avoid repeated disk reads on subsequent calls.
- **Filtered getters (`get_active_llm_providers`, `get_active_tools`, etc.)**: Return only entries explicitly set to `active = "true"` in the configuration.
- **Section-specific accessors**: Functions like `get_hub()`, `get_limits()`, and `get_db_sync()` provide fast access to specific configuration blocks by topic.

### Core Logic

The parser implements robust **string sanitization** (stripping quotes and inline comments) and supports hierarchical section structure. A notable feature is automatic prefix removal — within an `anthropic` provider block, `anthropic.base_url` becomes simply `base_url`, keeping configuration clean and readable.
