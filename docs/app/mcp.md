## File: `app/mcp.py`

**Description:** This module implements the **Model Context Protocol (MCP)** server using `FastMCP`. It serves as the SSE communication interface and registers the tools made available to LLMs and other MCP-compatible clients.

### Main Functions

- **`initialize()`**: Creates the MCP instance, loads configuration, and triggers tool registration across all categories (LLM, Search, System).
- **`handle_request()`**: The central entry point for incoming requests from the Quart web server. This is where logging, authentication, or rate limiting for all MCP traffic can be implemented.
- **`_register_llm_tools()`**: Registers the `llm_complete` tool if active LLM providers are available. All logic is fully delegated to `tools.py`.
- **`_register_search_tools()`**: Registers the `web_search` tool for web queries (once providers are configured).
- **`_register_system_tools()`**: Provides tools that require no API keys: `list_active_tools`, `health_check`, and `get_model_info`.

### Core Logic

The file follows the **delegation principle**: `mcp.py` only defines *what* is visible externally as a tool — it executes no logic itself. All actual work is handed off to `tools.py` and `providers.py`. This keeps the MCP interface stable even as new providers are added in the background.
