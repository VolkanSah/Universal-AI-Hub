# Contributing to Universal MCP Hub

Thanks for considering a contribution. Read this before opening a PR.

---

## Before You Start

Open an issue first for anything non-trivial. Describe what you want to change and why. This avoids situations where you spend time on a PR that conflicts with the architecture or is out of scope.

For typos, doc fixes, or obvious bugs — just open the PR directly.

---

## The Non-Negotiables

These rules are not up for debate in PRs. If you disagree with them, open an issue and make the case.

**1. Don't break the sandbox.**  
`app/*` must never import from `fundaments/`, read `os.environ`, or access `.env` directly. The Guardian pattern (`main.py` injects services, `app/*` receives them) is the core security guarantee of this project. PRs that route around it will be closed.

**2. No secrets in code or config.**  
`app/.pyfun` references ENV variable names only — never values. If your PR puts an API key, token, or credential anywhere in the codebase, it gets closed immediately.

**3. `db_query` stays SELECT-only.**  
The read-only enforcement in `db_sync.py` is not a suggestion. Don't submit PRs that add write access through the MCP tool layer.

**4. New providers need a dummy too.**  
If you add a new LLM or search provider, add a commented-out dummy class following the existing pattern. Future contributors should be able to add the next provider by copying your dummy.

---

## How to Add a New LLM Provider

Three files, nothing else:

**1. `app/providers.py`** — add your class (copy the OpenAI dummy as starting point):

```python
class YourProvider(BaseProvider):
    """Your Provider API — brief description."""

    async def complete(self, prompt: str, model: str = None, max_tokens: int = 1024) -> str:
        data = await self._post(
            f"{self.base_url}/chat/completions",  # adjust endpoint
            headers={
                "Authorization": f"Bearer {self.key}",
                "content-type":  "application/json",
            },
            payload={
                "model":      model or self.model,
                "max_tokens": max_tokens,
                "messages":   [{"role": "user", "content": prompt}],
            },
        )
        return data["choices"][0]["message"]["content"]  # adjust response parsing
```

**2. `app/providers.py`** — register in `_PROVIDER_CLASSES`:

```python
_PROVIDER_CLASSES = {
    ...
    "yourprovider": YourProvider,
}
```

**3. `app/.pyfun`** — add provider block:

```ini
[LLM_PROVIDER.yourprovider]
active        = "true"
base_url      = "https://api.yourprovider.com/v1"
env_key       = "YOUR_PROVIDER_API_KEY"
default_model = "your-default-model"
models        = "your-default-model, other-model"
fallback_to   = ""
[LLM_PROVIDER.yourprovider_END]
```

Then add a commented dummy of your class above the `_PROVIDER_CLASSES` dict with the `.pyfun` block as a comment — same style as OpenAI/Mistral/xAI dummies already there.

Most OpenAI-compatible APIs (Mistral, xAI, Together, etc.) need zero changes to the class body — just different `base_url` and `env_key`. Providers with non-standard auth (like Gemini's `?key=` param) need their own `complete()` implementation.

---

## How to Add a New Search Provider

Search providers follow the same pattern as LLM providers but register in `_SEARCH_REGISTRY` (not yet implemented — first search provider PR sets the pattern). Look at how `BraveProvider` and `TavilyProvider` are planned in `.pyfun` for reference.

---

## How to Add a New Tool

Tools are defined in `app/.pyfun` under `[TOOLS]` and registered in `app/mcp.py`. A tool needs:

- A `[TOOL.name]` block in `.pyfun` with `provider_type`, `default_provider`, `system_prompt` (for LLM tools)
- Registration in `mcp.py` via the appropriate `_register_*_tools()` function
- Execution logic in `app/tools.py`

Tools that require an API key must gate their registration — if the key isn't set, the tool must not register. No key, no tool, no crash. See existing tool registration in `mcp.py` for the pattern.

---

## Code Style

- Match the existing style — same header format, same section separators, same logging pattern
- One logger per module: `logger = logging.getLogger("module_name")`
- Async where the code is already async (`app/*`), sync where it's sync (`fundaments/*`)
- Comments explain *why*, not *what* — the code shows what

---

## Testing Your Changes

Before submitting:

```bash
# install dependencies
pip install -r requirements.txt

# start with minimal config (no keys needed for basic startup test)
python main.py

# check that startup completes without errors
# check that list_active_tools returns expected results
# check that missing keys don't crash the server
```

If you're adding a provider: test with a real key in a local `.env`. Document the response format you're parsing from in your PR description.

---

## PR Checklist

- [ ] Sandbox rules not broken (`app/*` has no new `os.environ` or `fundaments/` imports)
- [ ] No credentials or secrets in any file
- [ ] New provider includes dummy class + `.pyfun` block
- [ ] Startup still works with zero API keys configured
- [ ] Code follows existing style and header format
- [ ] PR description explains what changed and why

---

## What Won't Be Merged

- Features that add attack surface without clear security justification
- AI-generated code the author can't explain in review
- PRs that touch `fundaments/` without a very good reason — that layer is stable by design
- Dependency additions without justification — the stack is intentionally lean

---

*Questions? Open an issue. The architecture docs in `README.md`, `SECURITY.md`, and `docs/` cover most of the "why" behind the design decisions.*
