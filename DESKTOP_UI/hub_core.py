# =============================================================================
# DESKTOP_UI/hub_core.py
# GUI-independent core of the desktop client: HTTP client, config, chat store.
# Universal MCP Hub (Sandboxed) - based on PyFundaments Architecture
# Copyright 2026 - Volkan Kücükbudak
# Apache License V. 2 + ESOL 1.1
# =============================================================================
# stdlib only — no extra dependencies.
# Auth (app/auth.py): X-Hub-Token = hub gate, Authorization = HF proxy (private Space).
# =============================================================================
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

APP_DIR     = Path.home() / ".universal-ai-hub"
CONFIG_FILE = APP_DIR / "config.json"
CHATS_FILE  = APP_DIR / "chats.json"
LEGACY_CONFIG = Path(__file__).parent / "mcp_desktop.json"

LLM_TOOLS   = {"llm_complete", "code_review", "summarize", "translate"}
KNOWN_TOOLS = ["llm_complete", "code_review", "summarize", "translate", "web_search",
               "db_query", "persist_result", "show_skills", "search_skills"]

DEFAULT_CONFIG: Dict[str, Any] = {
    "hub_url": "", "hf_token": "", "hub_token": "",
    "default_tool": "llm_complete", "default_provider": "", "default_model": "",
    "font_size": 11, "theme": "dark",
}


class HubError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


# ── storage ──────────────────────────────────────────────────────────────────

def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_config() -> Dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    stored = _read_json(CONFIG_FILE)
    if stored is None:
        legacy = _read_json(LEGACY_CONFIG) or {}
        stored = {k: legacy[k] for k in
                  ("hub_url", "hf_token", "hub_token", "default_tool", "default_provider", "default_model")
                  if k in legacy}
    if isinstance(stored, dict):
        cfg.update({k: v for k, v in stored.items() if k in DEFAULT_CONFIG})
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    _write_json(CONFIG_FILE, {k: cfg.get(k, v) for k, v in DEFAULT_CONFIG.items()})


def load_chats() -> Dict[str, Any]:
    data = _read_json(CHATS_FILE)
    return data if isinstance(data, dict) else {}


def save_chats(chats: Dict[str, Any]) -> None:
    _write_json(CHATS_FILE, chats)


def new_chat(chats: Dict[str, Any]) -> str:
    cid = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    while cid in chats:
        cid += "+"
    chats[cid] = {"title": f"Chat {cid}", "messages": []}
    return cid


# ── URL handling ─────────────────────────────────────────────────────────────

def normalize_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "https://" + url
    return url


def is_insecure(url: str) -> bool:
    p = urllib.parse.urlparse(url)
    return p.scheme == "http" and (p.hostname or "") not in ("localhost", "127.0.0.1", "::1")


# ── HTTP client ──────────────────────────────────────────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # Tokens must never follow a redirect to another host.
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class HubClient:
    def __init__(self, url: str, hf_token: str = "", hub_token: str = ""):
        self.url = normalize_url(url)
        if not self.url:
            raise HubError("Hub URL fehlt (Settings).")
        self.headers = {"Accept": "application/json"}
        if hf_token:
            self.headers["Authorization"] = f"Bearer {hf_token}"
        if hub_token:
            self.headers["X-Hub-Token"] = hub_token

    def request(self, method: str, path: str, payload: Any = None, timeout: float = 30) -> Any:
        headers = dict(self.headers)
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
        try:
            with _OPENER.open(req, timeout=timeout) as r:
                status, body = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, body = e.code, e.read()
        except urllib.error.URLError as e:
            raise HubError(f"Connection failed: {e.reason}") from None
        except (TimeoutError, OSError) as e:
            raise HubError(f"Connection failed: {type(e).__name__}") from None

        try:
            parsed = json.loads(body.decode("utf-8", errors="replace"))
        except ValueError:
            parsed = None

        if status >= 300 or (isinstance(parsed, dict) and "error" in parsed):
            msg = parsed.get("error") if isinstance(parsed, dict) else None
            msg = msg or body.decode("utf-8", errors="replace")[:200].strip() or "no body"
            hint = ""
            if 300 <= status < 400:
                hint = " — Redirect nicht gefolgt (Token-Schutz), URL prüfen (http/https?)"
            elif status == 401:
                hint = " — Hub-Token fehlt oder falsch (Settings → Hub token)"
            elif status in (502, 503, 504):
                hint = " — Hub/Space nicht erreichbar oder schläft noch"
            raise HubError(f"HTTP {status}: {msg}{hint}", status)
        if parsed is None:
            raise HubError(f"Unexpected response (not JSON): {body[:120]!r}", status)
        return parsed

    def health(self) -> Dict[str, Any]:
        return self.request("GET", "/", timeout=10)

    def tool(self, name: str, params: Optional[Dict[str, Any]] = None, timeout: float = 120) -> Any:
        resp = self.request("POST", "/api", {"tool": name, "params": params or {}}, timeout=timeout)
        return resp.get("result", resp) if isinstance(resp, dict) else resp


# ── tool params / output ─────────────────────────────────────────────────────

def build_params(tool: str, text: str, provider: str = "", model: str = "",
                 max_tokens: int = 1024) -> Dict[str, Any]:
    text = text or ""
    if tool == "db_query":
        return {"sql": text}
    if tool == "show_skills":
        params: Dict[str, Any] = {"scope": "all"}
        if text.strip():
            params["tag"] = text.strip().lower()
        return params
    if tool == "search_skills":
        return {"prompt": text, "scope": "all", "top_k": 3}
    params = {"prompt": text}
    if tool in LLM_TOOLS:
        params["max_tokens"] = int(max_tokens)
        if provider and provider != "default":
            params["provider_name"] = provider
        if model and model != "default":
            params["model"] = model
    return params


def format_result(value: Any) -> str:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in "[{":
            try:
                value = json.loads(stripped)
            except ValueError:
                return value
        else:
            return value
    if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
        if all("heading" in v and "text" in v for v in value):
            return "\n\n".join(
                f"▸ {v.get('name', v.get('skill', '?'))} › {v['heading']}"
                f"   [{v.get('db', '?')}, rank {v.get('rank', '?')}]\n{v['text']}"
                for v in value)
        if all("chunks" in v and "description" in v for v in value):
            return "\n".join(
                f"▸ {v.get('name', v.get('skill', '?'))} [{v.get('db', '?')}, "
                f"{v['chunks']} chunks]  tags: {', '.join(v.get('tags') or []) or '-'}\n    {v['description']}"
                for v in value)
    return json.dumps(value, indent=2, ensure_ascii=False)


def read_text_file(path: str, max_bytes: int = 200_000) -> str:
    p = Path(path)
    if p.stat().st_size > max_bytes:
        raise HubError(f"File too large ({p.stat().st_size} bytes, max {max_bytes}).")
    raw = p.read_bytes()
    if b"\x00" in raw:
        raise HubError("Binary file — hub accepts text only.")
    return raw.decode("utf-8", errors="replace")
