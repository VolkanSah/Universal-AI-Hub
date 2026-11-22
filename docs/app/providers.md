## File: `app/providers.py`

**Description:** This module manages the integration of LLM providers (Anthropic, Gemini, OpenRouter, HuggingFace) and implements an automatic fallback chain for provider failures.

### Main Functions

- **`BaseProvider` & subclasses**: A base class handles all shared HTTP logic (`_post`), while provider-specific subclasses (e.g. `GeminiProvider`) only implement response parsing for their respective API.
- **`initialize()`**: At startup, registers all providers for which a valid API key is found in the environment.
- **`llm_complete()`**: The core function for text generation. Sends a request via the selected provider and automatically falls back to the next provider in the chain on failure.
- **`search()`**: Placeholder for future web search providers (Brave, Tavily).
- **Registry helpers (`list_active_llm`, `get`)**: Returns information about which providers are currently registered and active.

### Core Logic

The module implements a **fallback chain**: if a provider call fails, `.pyfun` is consulted for the configured `fallback_to` replacement. A visited set prevents infinite loops. The principle: no keys = no providers registered = no crash.

**Adding a new provider** requires two steps: add the class here and register it in `_PROVIDER_CLASSES`, then add the corresponding `[LLM_PROVIDER.name]` block in `app/.pyfun`. Commented dummy classes for OpenAI, Mistral, and xAI are included as copy-paste starting points.
