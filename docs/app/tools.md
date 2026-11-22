## File: `app/tools.py`

**Description:** The central tool registry module — a modular wrapper layer for the Universal MCP Hub. It manages the configuration and execution of tools within the sandboxed environment.

### Main Functions

- **`initialize()`**: Loads all active tools from the `.pyfun` configuration into an internal registry (`_registry`).
- **`run()`**: The central execution interface. Validates tool configuration, prepares the prompt, and delegates the request to the appropriate provider (LLM, Search, or DB).
- **Registry helpers (`get`, `get_description`, etc.)**: Utility functions for reading specific tool parameters (descriptions, timeouts, system prompts) directly from the registry.
- **List functions (`list_all`, `list_by_type`)**: Returns an overview of all available tools, or filters them by type (e.g. LLM tools only).

### Core Logic

The module follows the **single source of truth** principle: tools are defined exclusively through external configuration — no tool logic is hardcoded here. `tools.py` acts as a pure logical layer between configuration (`config.py`) and execution (`providers.py`). It performs no direct API communication itself.
