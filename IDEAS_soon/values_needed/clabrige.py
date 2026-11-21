#!/usr/bin/env python3
"""
clabrige.py - Lern- und Testdatei fuer die Claude Messages API (Doku-Stand: Nov 2026).

------------------------------------------------------------------------------
LERNHINWEISE
------------------------------------------------------------------------------
1. Die Claude-API ist "zustandslos": Der Server merkt sich NICHTS. Bei jedem Request
   schickst du den kompletten Gespraechsverlauf (`messages`) mit.
2. `messages` ist eine Liste abwechselnder Turns {"role": "user"|"assistant", "content": ...}.
   Der System-Prompt steht NICHT als Message, sondern im Top-Level-Feld `system`.
3. `content` ist ein String ODER eine Liste von "Content Blocks" (text, image, document,
   tool_use, tool_result, thinking ...). Die Antwort von Claude ist ebenfalls eine Liste
   von Blocks - z.B. erst ein `thinking`-Block, dann ein `text`-Block.
4. Header: `x-api-key` (dein Key), `anthropic-version: 2023-06-01`, `content-type: application/json`.
5. `max_tokens` ist PFLICHT (anders als bei OpenAI) und begrenzt die Ausgabe inkl. Thinking.
6. Tool Use: Claude fuehrt NIE selbst etwas aus. Es antwortet mit einem `tool_use`-Block
   (name + input). DU fuehrst die Funktion aus und schickst ein `tool_result` zurueck.
7. Streaming (SSE) besteht aus Events: message_start -> (content_block_start ->
   content_block_delta* -> content_block_stop)* -> message_delta -> message_stop.
   `accumulate_stream` unten baut daraus wieder die fertige Message zusammen.
8. Prompt Caching: Wiederholte Prompt-Anfaenge (langer System-Prompt, grosses Dokument)
   werden gecacht -> Lesen kostet nur ein Bruchteil. Steuerung: `cache_control`.

------------------------------------------------------------------------------
MODULE
------------------------------------------------------------------------------
    models   GET  /models
    chat     POST /messages        Stream, Thinking(adaptiv)+Effort, Structured Output,
                                   Tools (+Loop), Bilder, PDF, Caching
    count    POST /messages/count_tokens   Tokens zaehlen, bevor du bezahlst
    batch    POST /messages/batches        50% guenstiger, asynchron (bis 24h)
    all      Smoke-Test

Setup:
    pip install requests
    export ANTHROPIC_API_KEY=sk-ant-...

Beispiele:
    python clabrige.py chat "Erklaer Rekursion in 2 Saetzen" --stream
    python clabrige.py chat "Beweise: sqrt(2) irrational" --thinking --effort high --show-thinking
    python clabrige.py chat "Was ist auf dem Bild?" --image foto.jpg
    python clabrige.py chat "Fasse zusammen" --pdf bericht.pdf --cache
    python clabrige.py chat "Rezept fuer Pfannkuchen" --schema
    python clabrige.py chat "Wetter in Berlin?" --tools
    python clabrige.py count "Wie viele Tokens ist das?"
    python clabrige.py batch run prompts.txt --model claude-haiku-5-5

Unsicher (aus der gelesenen Doku nicht belegt - bei Fehler anpassen):
  * Pfad /messages/count_tokens stammt aus allgemeinem API-Wissen (Doku listet nur "Token counting").
  * temperature/top_p/top_k: in der gelesenen Request-Doku nicht aufgefuehrt; wir senden sie nur,
    wenn du sie setzt. Neuere Modelle koennen sie ablehnen (HTTP 400).
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

import requests

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

BASE = os.environ.get("ANTHROPIC_BASE", "https://api.anthropic.com/v1")
API_VERSION = "2023-06-01"
OUT_DIR = Path("cla_out")

DEFAULTS = {
    "chat": "claude-sonnet-5-5",   # gute Mischung aus Tempo und Qualitaet
    "fast": "claude-haiku-5-5",    # billig + schnell (Klassifikation, Extraktion, Batch)
    "strong": "claude-opus-5-5",   # Standard fuer anspruchsvolle Arbeit
    "max": "claude-fable-5-2",     # fuer das Schwierigste
}


# ---------------------------------------------------------------------------
# Helfer
# ---------------------------------------------------------------------------

class ClaudeError(RuntimeError):
    pass


def headers(extra: Optional[dict] = None) -> dict[str, str]:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_API_KEY fehlt. Setze: export ANTHROPIC_API_KEY=sk-ant-...")
    h = {"x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json"}
    h.update(extra or {})
    return h


def check(r: requests.Response) -> requests.Response:
    """Fehler kommen als {"type":"error","error":{"type":...,"message":...}}."""
    if r.ok:
        return r
    try:
        e = r.json().get("error", {})
        msg = f"{e.get('type')}: {e.get('message')}"
    except Exception:  # noqa: BLE001
        msg = r.text
    raise ClaudeError(f"HTTP {r.status_code}: {msg}")


def post(path: str, body: dict, *, extra_headers: Optional[dict] = None,
         timeout: int = 600, stream: bool = False) -> requests.Response:
    return check(requests.post(f"{BASE}{path}", headers=headers(extra_headers), json=body,
                               timeout=timeout, stream=stream))


def get(path: str, params: Optional[dict] = None, timeout: int = 60) -> requests.Response:
    return check(requests.get(f"{BASE}{path}", headers=headers(), params=params, timeout=timeout))


def drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def out_path(name: str | Path) -> Path:
    p = Path(name)
    if not p.is_absolute() and p.parent == Path("."):
        OUT_DIR.mkdir(exist_ok=True)
        p = OUT_DIR / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def b64_file(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode()


def sse_events(r: requests.Response) -> Iterator[dict]:
    """SSE lesen: Zeilen 'event: ...' und 'data: {json}'. Das `type`-Feld im JSON nennt das Event."""
    for raw in r.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data:"):
            continue
        try:
            yield json.loads(raw[5:].strip())
        except json.JSONDecodeError:
            continue


# ---------------------------------------------------------------------------
# Content-Block-Bausteine
# ---------------------------------------------------------------------------

def text_block(text: str, cache: bool = False) -> dict:
    return drop_none({"type": "text", "text": text, "cache_control": {"type": "ephemeral"} if cache else None})


def image_block(src: str) -> dict:
    """URL oder lokale Datei. Erlaubt: jpeg, png, gif, webp."""
    if src.startswith(("http://", "https://")):
        return {"type": "image", "source": {"type": "url", "url": src}}
    mt = mimetypes.guess_type(src)[0] or "image/png"
    return {"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64_file(src)}}


def pdf_block(src: str, cache: bool = False, title: Optional[str] = None) -> dict:
    source = ({"type": "url", "url": src} if src.startswith(("http://", "https://"))
              else {"type": "base64", "media_type": "application/pdf", "data": b64_file(src)})
    return drop_none({"type": "document", "source": source, "title": title,
                      "cache_control": {"type": "ephemeral"} if cache else None})


def tool_def(name: str, description: str, input_schema: dict, strict: bool = False) -> dict:
    """Eigenes Tool. strict=True erzwingt, dass Claudes Input exakt zum Schema passt."""
    return drop_none({"name": name, "description": description, "input_schema": input_schema,
                      "strict": True if strict else None})


# ---------------------------------------------------------------------------
# Antwort-Auswertung
# ---------------------------------------------------------------------------

def blocks_of(msg: dict, btype: str) -> list[dict]:
    return [b for b in msg.get("content", []) if b.get("type") == btype]


def message_text(msg: dict) -> str:
    return "".join(b.get("text", "") for b in blocks_of(msg, "text"))


def message_thinking(msg: dict) -> str:
    return "\n".join(b.get("thinking", "") for b in blocks_of(msg, "thinking"))


def usage_line(msg: dict) -> str:
    u = msg.get("usage", {})
    td = (u.get("output_tokens_details") or {}).get("thinking_tokens")
    return (f"in={u.get('input_tokens')} out={u.get('output_tokens')} thinking={td} "
            f"cache_write={u.get('cache_creation_input_tokens')} cache_read={u.get('cache_read_input_tokens')} "
            f"stop={msg.get('stop_reason')}")


# ---------------------------------------------------------------------------
# Modul 1: Models
# ---------------------------------------------------------------------------

def list_models(lifecycle: Optional[list[str]] = None) -> list[dict]:
    """Liefert u.a. id, max_input_tokens, max_tokens, capabilities, lifecycle (active/deprecated/retired)."""
    params = [("lifecycle", x) for x in (lifecycle or ["active"])]
    r = check(requests.get(f"{BASE}/models", headers=headers(), params=params, timeout=60))
    return r.json().get("data", [])


# ---------------------------------------------------------------------------
# Modul 2: Messages
# ---------------------------------------------------------------------------

def messages_create(
    messages: list[dict],
    *,
    model: Optional[str] = None,
    max_tokens: int = 1024,
    system: Optional[str | list[dict]] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    top_k: Optional[int] = None,
    stop_sequences: Optional[list[str]] = None,
    thinking: Optional[dict] = None,            # {"type":"adaptive"} | {"type":"enabled","budget_tokens":N} (nur aeltere Modelle)
    thinking_display: Optional[str] = None,     # "summarized" -> Denktext sichtbar | "omitted"
    effort: Optional[str] = None,               # low|medium|high|xhigh|max  (output_config.effort)
    json_schema: Optional[dict] = None,         # Structured Output (output_config.format)
    tools: Optional[list[dict]] = None,
    tool_choice: Optional[dict] = None,         # {"type":"auto"|"any"|"tool"|"none", "name":..}
    cache: bool = False,                        # Top-Level cache_control = automatisches Caching
    cache_ttl: Optional[str] = None,            # "5m" (Default) | "1h"
    service_tier: Optional[str] = None,         # auto | standard_only
    metadata_user_id: Optional[str] = None,
    extra: Optional[dict] = None,
    extra_headers: Optional[dict] = None,       # z.B. Beta-Header
    stream: bool = False,
):
    """
    Kernfunktion. stream=False -> Message-Dict, stream=True -> Generator von SSE-Events.
    Merke: Thinking-Tokens zaehlen zu max_tokens. Gib bei Thinking genug Luft (z.B. 8000+).
    """
    if thinking and thinking_display:
        thinking = {**thinking, "display": thinking_display}
    output_config = drop_none({
        "effort": effort,
        "format": {"type": "json_schema", "schema": json_schema} if json_schema else None,
    })
    body = drop_none(dict(
        model=model or DEFAULTS["chat"], max_tokens=max_tokens, messages=messages, system=system,
        temperature=temperature, top_p=top_p, top_k=top_k, stop_sequences=stop_sequences,
        thinking=thinking, output_config=output_config or None, tools=tools, tool_choice=tool_choice,
        cache_control=drop_none({"type": "ephemeral", "ttl": cache_ttl}) if cache else None,
        service_tier=service_tier,
        metadata={"user_id": metadata_user_id} if metadata_user_id else None,
        stream=True if stream else None,
    ))
    body.update(extra or {})
    r = post("/messages", body, extra_headers=extra_headers, stream=stream)
    return sse_events(r) if stream else r.json()


def accumulate_stream(events: Iterator[dict],
                      on_text: Optional[Callable[[str], None]] = None,
                      on_thinking: Optional[Callable[[str], None]] = None) -> dict:
    """
    Baut aus den Stream-Events wieder eine normale Message (identisch zu stream=False).
    Wichtig: tool_use-Input kommt als Text-Schnipsel (`partial_json`) und darf erst am
    `content_block_stop` mit json.loads geparst werden.
    """
    blocks: dict[int, dict] = {}
    json_buf: dict[int, str] = {}
    message: dict = {}
    for ev in events:
        t = ev.get("type")
        if t == "message_start":
            message = dict(ev["message"])
        elif t == "content_block_start":
            blk = dict(ev["content_block"])
            blocks[ev["index"]] = blk
            if blk["type"] in ("tool_use", "server_tool_use"):
                json_buf[ev["index"]] = ""
        elif t == "content_block_delta":
            i, d = ev["index"], ev["delta"]
            dt = d.get("type")
            if dt == "text_delta":
                blocks[i]["text"] = blocks[i].get("text", "") + d["text"]
                on_text and on_text(d["text"])
            elif dt == "thinking_delta":
                blocks[i]["thinking"] = blocks[i].get("thinking", "") + d["thinking"]
                on_thinking and on_thinking(d["thinking"])
            elif dt == "signature_delta":
                blocks[i]["signature"] = d["signature"]
            elif dt == "input_json_delta":
                json_buf[i] += d["partial_json"]
        elif t == "content_block_stop":
            i = ev["index"]
            if i in json_buf:
                blocks[i]["input"] = json.loads(json_buf.pop(i) or "{}")
        elif t == "message_delta":
            message["stop_reason"] = ev["delta"].get("stop_reason")
            message["usage"] = {**message.get("usage", {}), **(ev.get("usage") or {})}  # kumulativ
        elif t == "error":
            raise ClaudeError(str(ev.get("error")))
        # ping, message_stop und unbekannte Events: ignorieren (laut Doku erlaubt)
    message["content"] = [blocks[i] for i in sorted(blocks)]
    return message


def run_tool_loop(prompt: str, fns: dict[str, Callable[..., Any]], tools: list[dict],
                  *, model: Optional[str] = None, max_rounds: int = 5, verbose: bool = True, **kw: Any) -> str:
    """
    Der Tool-Zyklus Schritt fuer Schritt:
      1. Request mit `tools`. Claude antwortet mit stop_reason == "tool_use" und tool_use-Block(s).
      2. Wir haengen Claudes KOMPLETTE Antwort (inkl. evtl. thinking-Blocks, unveraendert!) als
         assistant-Message an den Verlauf.
      3. Wir fuehren die Funktion(en) aus und haengen EINE user-Message mit tool_result-Blocks an
         (tool_use_id muss zur id des tool_use passen).
      4. Wiederholen, bis stop_reason != "tool_use".
    """
    messages: list[dict] = [{"role": "user", "content": prompt}]
    for _ in range(max_rounds):
        msg = messages_create(messages, model=model, tools=tools, **kw)
        messages.append({"role": "assistant", "content": msg["content"]})
        if msg.get("stop_reason") != "tool_use":
            return message_text(msg)
        results = []
        for tu in blocks_of(msg, "tool_use"):
            if verbose:
                print(f"  -> Claude ruft {tu['name']}({tu['input']})")
            try:
                out, is_err = json.dumps(fns[tu["name"]](**tu["input"]), ensure_ascii=False), False
            except Exception as e:  # noqa: BLE001 - Fehler dem Modell melden statt abstuerzen
                out, is_err = f"Fehler: {e}", True
            results.append(drop_none({"type": "tool_result", "tool_use_id": tu["id"], "content": out,
                                      "is_error": True if is_err else None}))
        messages.append({"role": "user", "content": results})
    return "(max_rounds erreicht)"


# ---------------------------------------------------------------------------
# Modul 3: Token Counting
# ---------------------------------------------------------------------------

def count_tokens(messages: list[dict], *, model: Optional[str] = None,
                 system: Optional[str] = None, tools: Optional[list[dict]] = None) -> int:
    body = drop_none(dict(model=model or DEFAULTS["chat"], messages=messages, system=system, tools=tools))
    return post("/messages/count_tokens", body).json()["input_tokens"]


# ---------------------------------------------------------------------------
# Modul 4: Message Batches (50% Rabatt, asynchron)
# ---------------------------------------------------------------------------

def batch_create(requests_: list[dict]) -> dict:
    """requests_: [{"custom_id": "...", "params": {<normale Messages-Parameter>}}, ...]
    custom_id: 1-64 Zeichen [a-zA-Z0-9_-], eindeutig. KEIN stream/speed/max_tokens=0 in params."""
    return post("/messages/batches", {"requests": requests_}).json()


def batch_get(batch_id: str) -> dict:
    return get(f"/messages/batches/{batch_id}").json()


def batch_cancel(batch_id: str) -> dict:
    return post(f"/messages/batches/{batch_id}/cancel", {}).json()


def batch_list() -> list[dict]:
    return get("/messages/batches").json().get("data", [])


def batch_results(batch: dict) -> list[dict]:
    """results_url liefert JSONL (1 Ergebnis pro Zeile). Reihenfolge ist NICHT garantiert -> custom_id nutzen."""
    r = check(requests.get(batch["results_url"], headers=headers(), timeout=600))
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


def run_batch(prompts: list[str], *, model: Optional[str] = None, max_tokens: int = 512,
              poll: int = 30, timeout: int = 3600) -> list[dict]:
    reqs = [{"custom_id": f"req-{i}", "params": {"model": model or DEFAULTS["fast"], "max_tokens": max_tokens,
                                                  "messages": [{"role": "user", "content": p}]}}
            for i, p in enumerate(prompts)]
    b = batch_create(reqs)
    print(f"  batch {b['id']} status={b['processing_status']}")
    deadline = time.time() + timeout
    while b["processing_status"] != "ended":
        if time.time() > deadline:
            raise TimeoutError(f"Batch {b['id']} laeuft noch (spaeter: batch status {b['id']})")
        time.sleep(poll)
        b = batch_get(b["id"])
        print(f"  status={b['processing_status']} {b.get('request_counts')}", flush=True)
    return sorted(batch_results(b), key=lambda x: x["custom_id"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def demo_weather(location: str) -> dict:
    return {"location": location, "temperature_c": 21, "condition": "sonnig"}


WEATHER_TOOL = tool_def("get_weather", "Aktuelles Wetter fuer einen Ort abrufen",
                        {"type": "object", "properties": {"location": {"type": "string"}},
                         "required": ["location"], "additionalProperties": False}, strict=True)


def cmd_models(a: argparse.Namespace) -> None:
    ms = list_models(["active", "deprecated"] if a.all else ["active"])
    for m in ms:
        print(f"{m['id']:<32} in={m.get('max_input_tokens')} out={m.get('max_tokens')} "
              f"lifecycle={m.get('lifecycle')} retires={m.get('retires_at')}")


def cmd_chat(a: argparse.Namespace) -> None:
    model = a.model or (DEFAULTS["fast"] if a.fast else DEFAULTS["strong"] if a.strong else None)
    if a.tools:
        print(run_tool_loop(a.prompt, {"get_weather": demo_weather}, [WEATHER_TOOL], model=model))
        return
    content: Any = a.prompt
    if a.image or a.pdf:
        content = [pdf_block(p, cache=a.cache) for p in (a.pdf or [])] + [image_block(i) for i in (a.image or [])]
        content.append(text_block(a.prompt))
    schema = None
    if a.schema:
        schema = {"type": "object",
                  "properties": {"title": {"type": "string"}, "ingredients": {"type": "array", "items": {"type": "string"}},
                                 "minutes": {"type": "integer"}},
                  "required": ["title", "ingredients", "minutes"], "additionalProperties": False}
    kw = dict(
        model=model, system=a.system, max_tokens=a.max_tokens, temperature=a.temperature,
        thinking={"type": "adaptive"} if a.thinking else None,
        thinking_display="summarized" if a.show_thinking else None,
        effort=a.effort, json_schema=schema, cache=a.cache,
    )
    msgs = [{"role": "user", "content": content}]
    if a.stream:
        msg = accumulate_stream(
            messages_create(msgs, stream=True, **kw),
            on_text=lambda t: print(t, end="", flush=True),
            on_thinking=lambda t: print(f"\033[2m{t}\033[0m", end="", flush=True))
        print(f"\n\n[{usage_line(msg)}]")
        return
    msg = messages_create(msgs, **kw)
    if message_thinking(msg):
        print(f"\033[2m[thinking] {message_thinking(msg)[:800]}\033[0m\n")
    print(message_text(msg))
    print(f"\n[model={msg.get('model')} {usage_line(msg)}]")


def cmd_count(a: argparse.Namespace) -> None:
    print(count_tokens([{"role": "user", "content": a.prompt}], model=a.model, system=a.system), "Input-Tokens")


def cmd_batch(a: argparse.Namespace) -> None:
    if a.action == "run":
        prompts = [l.strip() for l in Path(a.arg).read_text(encoding="utf-8").splitlines() if l.strip()]
        for row in run_batch(prompts, model=a.model):
            res = row["result"]
            txt = message_text(res["message"]) if res["type"] == "succeeded" else res["type"]
            print(f"{row['custom_id']}: {txt[:100]}")
    elif a.action == "list":
        for b in batch_list():
            print(b["id"], b["processing_status"], b.get("request_counts"))
    elif a.action == "status":
        print(json.dumps(batch_get(a.arg), indent=2))
    elif a.action == "cancel":
        print(batch_cancel(a.arg)["processing_status"])


def cmd_all(a: argparse.Namespace) -> None:
    schema = {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
              "required": ["name", "age"], "additionalProperties": False}
    u = lambda t: [{"role": "user", "content": t}]  # noqa: E731
    tests: list[tuple[str, Callable[[], Any]]] = [
        ("models", lambda: len(list_models())),
        ("chat", lambda: message_text(messages_create(u("Sag 'ok'."), max_tokens=30, model=DEFAULTS["fast"]))),
        ("stream", lambda: message_text(accumulate_stream(messages_create(u("Zaehle bis 3."), max_tokens=50, model=DEFAULTS["fast"], stream=True)))),
        ("thinking", lambda: bool(message_thinking(messages_create(u("17*23?"), max_tokens=4000, model=DEFAULTS["fast"],
                                                                    thinking={"type": "adaptive"}, thinking_display="summarized")))),
        ("schema", lambda: message_text(messages_create(u("Max, 31 Jahre"), max_tokens=100, model=DEFAULTS["fast"], json_schema=schema))),
        ("tools", lambda: run_tool_loop("Wetter in Berlin?", {"get_weather": demo_weather}, [WEATHER_TOOL],
                                        model=DEFAULTS["fast"], verbose=False)[:50]),
        ("count", lambda: count_tokens(u("Hallo Welt"))),
    ]
    rows = []
    for name, fn in tests:
        t0 = time.time()
        try:
            rows.append((name, "OK", f"{time.time() - t0:.1f}s", str(fn())[:60]))
        except Exception as e:  # noqa: BLE001
            rows.append((name, "FAIL", f"{time.time() - t0:.1f}s", f"{type(e).__name__}: {e}"[:110]))
            if a.verbose:
                traceback.print_exc()
    print(f"\n{'Modul':<12}{'Status':<8}{'Zeit':<8}Detail\n" + "-" * 80)
    for r in rows:
        print(f"{r[0]:<12}{r[1]:<8}{r[2]:<8}{r[3]}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Claude Messages API - Lern-/Testdatei")
    sub = p.add_subparsers(dest="cmd", required=True)

    def mk(name: str, fn: Callable, **kw: Any) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, **kw)
        sp.set_defaults(func=fn)
        return sp

    sp = mk("models", cmd_models); sp.add_argument("--all", action="store_true", help="auch deprecated")

    sp = mk("chat", cmd_chat)
    sp.add_argument("prompt"); sp.add_argument("--model")
    sp.add_argument("--fast", action="store_true", help="Haiku"); sp.add_argument("--strong", action="store_true", help="Opus")
    sp.add_argument("--system"); sp.add_argument("--stream", action="store_true")
    sp.add_argument("--thinking", action="store_true", help="adaptive thinking")
    sp.add_argument("--show-thinking", action="store_true", help="display=summarized")
    sp.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    sp.add_argument("--image", nargs="+"); sp.add_argument("--pdf", nargs="+")
    sp.add_argument("--schema", action="store_true"); sp.add_argument("--tools", action="store_true")
    sp.add_argument("--cache", action="store_true", help="Top-Level cache_control")
    sp.add_argument("--max-tokens", type=int, default=2048); sp.add_argument("--temperature", type=float)

    sp = mk("count", cmd_count)
    sp.add_argument("prompt"); sp.add_argument("--model"); sp.add_argument("--system")

    sp = mk("batch", cmd_batch)
    sp.add_argument("action", choices=["run", "list", "status", "cancel"])
    sp.add_argument("arg", nargs="?", help="run: Datei (1 Prompt/Zeile) | status/cancel: batch_id")
    sp.add_argument("--model")

    sp = mk("all", cmd_all, help="Smoke-Test"); sp.add_argument("--verbose", action="store_true")
    return p


def main() -> None:
    a = build_parser().parse_args()
    try:
        a.func(a)
    except ClaudeError as e:
        sys.exit(f"Fehler: {e}")


if __name__ == "__main__":
    main()
