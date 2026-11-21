#!/usr/bin/env python3
"""
groqbrige.py - modulare Testdatei fuer Groq (Doku-Stand: Nov 2026).

Ein Modul (Funktion + CLI-Subcommand) pro Feature, nur `requests`, kein SDK.

Setup:
    pip install -U requests
    export GROQ_API_KEY=gsk_...

Module / Endpunkte (Base: https://api.groq.com/openai/v1):
    models   GET  /models
    chat     POST /chat/completions   Stream, Reasoning, Structured Outputs, Tools,
                                      Web-Search-Settings, service_tier
    resp     POST /responses (beta)   Built-in Tools browser_search / code_interpreter (nur gpt-oss), MCP
    stt      POST /audio/transcriptions | /audio/translations
    tts      POST /audio/speech       (Orpheus)
    batch    POST /files + /batches   24h-7d Batch-Verarbeitung (Chat Completions)
    all      Smoke-Test

Was es bei Groq NICHT (mehr) gibt - deshalb kein Modul:
    - Embeddings, Rerank, Bild- und Video-Generierung
    - groq/compound(-mini): laut Doku am 21.09.2026 abgeschaltet
    - Chat-Parameter: logprobs, frequency/presence_penalty, logit_bias, n>1, store

Beispiele:
    python groqbrige.py models
    python groqbrige.py chat "Hallo" --stream
    python groqbrige.py chat "Rechne 17*23" --model openai/gpt-oss-120b --effort high
    python groqbrige.py chat "Rechne 17*23" --model qwen/qwen3.8-27b --reasoning-format parsed --effort medium
    python groqbrige.py chat "Extrahiere: Max, 31" --schema --model openai/gpt-oss-20b
    python groqbrige.py chat "Wetter in Berlin?" --tools
    python groqbrige.py resp "Top Tech-News heute?" --browser
    python groqbrige.py resp "Was ist 1312 x 3333?" --code
    python groqbrige.py stt aufnahme.mp3 --model whisper-large-v3-turbo --words
    python groqbrige.py tts "[cheerful] Hallo Welt" --voice troy --out hallo.wav
    python groqbrige.py batch run prompts.txt
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

BASE = os.environ.get("GROQ_BASE", "https://api.groq.com/openai/v1")
OUT_DIR = Path("groq_out")

# IDs aus der Models-Seite (Nov 2026). Mit `models` aktuelle Liste pruefen.
DEFAULTS = {
    "chat": "llama-3.3-70b-versatile",
    "fast": "llama-3.1-8b-instant",
    "reasoning": "openai/gpt-oss-120b",       # Strict Structured Outputs, Built-in Tools
    "resp": "openai/gpt-oss-20b",
    "stt": "whisper-large-v3-turbo",
    "tts": "canopylabs/orpheus-v1-english",
    "tts_voice": "troy",                       # weitere: hannah, austin
}


# ---------------------------------------------------------------------------
# Helfer
# ---------------------------------------------------------------------------

class GroqError(RuntimeError):
    pass


def headers(json_body: bool = True, extra: Optional[dict] = None) -> dict[str, str]:
    k = os.environ.get("GROQ_API_KEY")
    if not k:
        sys.exit("GROQ_API_KEY fehlt. Setze: export GROQ_API_KEY=gsk_...")
    h = {"Authorization": f"Bearer {k}"}
    if json_body:
        h["Content-Type"] = "application/json"
    h.update(extra or {})
    return h


def check(r: requests.Response) -> requests.Response:
    if r.ok:
        return r
    try:
        err = r.json().get("error", r.text)
    except Exception:  # noqa: BLE001
        err = r.text
    raise GroqError(f"HTTP {r.status_code}: {err}")


def post(path: str, body: dict, *, extra_headers: Optional[dict] = None,
         timeout: int = 300, stream: bool = False) -> requests.Response:
    return check(requests.post(f"{BASE}{path}", headers=headers(extra=extra_headers),
                               json=body, timeout=timeout, stream=stream))


def get(path: str, params: Optional[dict] = None, timeout: int = 60) -> requests.Response:
    return check(requests.get(f"{BASE}{path}", headers=headers(False), params=params, timeout=timeout))


def drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def out_path(name: str | Path) -> Path:
    p = Path(name)
    if not p.is_absolute() and p.parent == Path("."):
        OUT_DIR.mkdir(exist_ok=True)
        p = OUT_DIR / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def to_data_url(src: str) -> str:
    if src.startswith(("http://", "https://", "data:")):
        return src
    mime = mimetypes.guess_type(src)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(Path(src).read_bytes()).decode()}"


def sse_events(r: requests.Response) -> Iterator[dict]:
    for raw in r.iter_lines(decode_unicode=True):
        if not raw or raw.startswith(":") or not raw.startswith("data:"):
            continue
        data = raw[5:].strip()
        if data == "[DONE]":
            return
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue


def json_schema_format(name: str, schema: dict, strict: bool = True) -> dict:
    """strict=True nur auf gpt-oss-20b/120b, qwen3.8-27b, muse-spark-open. Pflicht dafuer:
    alle Properties in `required` + additionalProperties:false; kein Streaming, keine Tools."""
    return {"type": "json_schema", "json_schema": {"name": name, "strict": strict, "schema": schema}}


def demo_tool() -> list[dict]:
    return [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                    "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                },
                "required": ["location"],
            },
        },
    }]


# ---------------------------------------------------------------------------
# Modul 1: Models
# ---------------------------------------------------------------------------

def list_models() -> list[dict]:
    return get("/models").json()["data"]


# ---------------------------------------------------------------------------
# Modul 2: Chat Completions
# ---------------------------------------------------------------------------

def chat_completion(
    prompt: Optional[str] = None,
    *,
    messages: Optional[list[dict]] = None,
    system: Optional[str] = None,
    image: Optional[str] = None,                 # Doku listet kein explizites Vision-Modell -> ggf. 400
    model: Optional[str] = None,
    stream: bool = False,
    max_completion_tokens: Optional[int] = 512,  # max_tokens ist deprecated
    temperature: Optional[float] = None,         # 0..2
    top_p: Optional[float] = None,
    seed: Optional[int] = None,
    stop: Optional[list[str]] = None,            # max 4
    reasoning_effort: Optional[str] = None,      # gpt-oss: low|medium|high; qwen3.8: none|default|low|medium|high
    reasoning_format: Optional[str] = None,      # raw|parsed|hidden (NICHT bei gpt-oss)
    include_reasoning: Optional[bool] = None,    # gpt-oss; schliesst reasoning_format aus
    response_format: Optional[dict] = None,
    tools: Optional[list[dict]] = None,
    tool_choice: Optional[Any] = None,
    parallel_tool_calls: Optional[bool] = None,
    service_tier: Optional[str] = None,          # auto|on_demand|flex|performance
    search_settings: Optional[dict] = None,      # {"include_domains":[..],"exclude_domains":[..]}
    documents: Optional[list[dict]] = None,      # Kontext-Dokumente (zitierbar, citation_options)
    citation_options: Optional[str] = None,      # enabled|disabled
    extra: Optional[dict] = None,
    raw: bool = False,
):
    if messages is None:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        content: Any = prompt
        if image:
            content = [
                {"type": "text", "text": prompt or ""},
                {"type": "image_url", "image_url": {"url": to_data_url(image)}},
            ]
        messages.append({"role": "user", "content": content})

    body = drop_none(dict(
        model=model or DEFAULTS["chat"], messages=messages, stream=True if stream else None,
        max_completion_tokens=max_completion_tokens, temperature=temperature, top_p=top_p,
        seed=seed, stop=stop, reasoning_effort=reasoning_effort, reasoning_format=reasoning_format,
        include_reasoning=include_reasoning, response_format=response_format, tools=tools,
        tool_choice=tool_choice, parallel_tool_calls=parallel_tool_calls, service_tier=service_tier,
        search_settings=search_settings, documents=documents, citation_options=citation_options,
    ))
    body.update(extra or {})
    if stream:
        return sse_events(post("/chat/completions", body, stream=True))
    data = post("/chat/completions", body).json()
    return data if raw else data["choices"][0]["message"]


def stream_text(chunks: Iterator[dict]) -> Iterator[tuple[str, str]]:
    for ch in chunks:
        for c in ch.get("choices", []):
            d = c.get("delta") or {}
            if d.get("reasoning"):
                yield "reasoning", d["reasoning"]
            if d.get("content"):
                yield "content", d["content"]
            for tc in d.get("tool_calls") or []:
                fn = tc.get("function") or {}
                yield "tool", (fn.get("name") or "") + (fn.get("arguments") or "")
        usage = ch.get("usage") or (ch.get("x_groq") or {}).get("usage")
        if usage:
            yield "usage", json.dumps(usage)


def run_tool_loop(prompt: str, fns: dict[str, Callable[..., Any]], tools: list[dict],
                  *, model: Optional[str] = None, max_rounds: int = 4) -> str:
    """Kompletter Tool-Zyklus: Modell -> tool_calls -> Ergebnis als role=tool -> Antwort."""
    messages: list[dict] = [{"role": "user", "content": prompt}]
    for _ in range(max_rounds):
        msg = chat_completion(messages=messages, model=model, tools=tools, tool_choice="auto")
        messages.append(msg)
        calls = msg.get("tool_calls") or []
        if not calls:
            return msg.get("content") or ""
        for tc in calls:
            name = tc["function"]["name"]
            result = fns[name](**json.loads(tc["function"]["arguments"] or "{}"))
            messages.append({"role": "tool", "tool_call_id": tc["id"], "name": name,
                             "content": json.dumps(result, ensure_ascii=False)})
    return "(max_rounds erreicht)"


# ---------------------------------------------------------------------------
# Modul 3: Responses API (beta, stateless)
# ---------------------------------------------------------------------------

def responses(
    input_: str | list[dict],
    *,
    model: Optional[str] = None,
    instructions: Optional[str] = None,
    tools: Optional[list[dict]] = None,        # function | browser_search | code_interpreter | mcp
    tool_choice: Optional[Any] = None,
    reasoning_effort: Optional[str] = None,
    text_format: Optional[dict] = None,        # {"type":"json_schema","name":..,"strict":True,"schema":..}
    max_output_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    metrics: bool = False,                     # Header Groq-Beta: inference-metrics
    extra: Optional[dict] = None,
) -> dict:
    """Nicht unterstuetzt: previous_response_id, store(=true), truncation, include -> Verlauf selbst senden.
    Built-in Tools nur auf openai/gpt-oss-20b und -120b."""
    body = drop_none(dict(
        model=model or DEFAULTS["resp"], input=input_, instructions=instructions, tools=tools,
        tool_choice=tool_choice, max_output_tokens=max_output_tokens, temperature=temperature,
        reasoning={"effort": reasoning_effort} if reasoning_effort else None,
        text={"format": text_format} if text_format else None,
    ))
    body.update(extra or {})
    return post("/responses", body, extra_headers={"Groq-Beta": "inference-metrics"} if metrics else None).json()


def responses_text(resp: dict) -> str:
    if isinstance(resp.get("output_text"), str):
        return resp["output_text"]
    out = []
    for item in resp.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("text"):
                    out.append(c["text"])
    return "".join(out)


BROWSER_TOOL = {"type": "browser_search"}
CODE_TOOL = {"type": "code_interpreter", "container": {"type": "auto"}}


def mcp_tool(label: str, url: str, **kw: Any) -> dict:
    return {"type": "mcp", "server_label": label, "server_url": url, **kw}


# ---------------------------------------------------------------------------
# Modul 4: Speech-to-Text / Translation (multipart)
# ---------------------------------------------------------------------------

def speech_to_text(
    audio: str,                                  # Datei ODER https-URL (URL fuer >25/100 MB)
    *,
    model: Optional[str] = None,                 # whisper-large-v3-turbo | whisper-large-v3
    language: Optional[str] = None,              # ISO-639-1; bei translate nur "en"
    prompt: Optional[str] = None,                # max 224 Tokens
    response_format: Optional[str] = None,       # json | verbose_json | text
    timestamps: Optional[list[str]] = None,      # ["word","segment"], braucht verbose_json
    temperature: Optional[float] = None,
    translate: bool = False,                     # nur whisper-large-v3
) -> dict | str:
    data: dict[str, Any] = drop_none(dict(
        model=model or ("whisper-large-v3" if translate else DEFAULTS["stt"]),
        language=language, prompt=prompt, response_format=response_format, temperature=temperature,
    ))
    if timestamps:
        data["response_format"] = "verbose_json"
        data["timestamp_granularities[]"] = timestamps
    path = "/audio/translations" if translate else "/audio/transcriptions"
    if audio.startswith(("http://", "https://")):
        data["url"] = audio
        r = requests.post(f"{BASE}{path}", headers=headers(False), data=data, timeout=600)
    else:
        with open(audio, "rb") as f:
            r = requests.post(f"{BASE}{path}", headers=headers(False), data=data,
                              files={"file": (Path(audio).name, f)}, timeout=600)
    check(r)
    return r.text if data.get("response_format") == "text" else r.json()


# ---------------------------------------------------------------------------
# Modul 5: Text-to-Speech (Orpheus)
# ---------------------------------------------------------------------------

def text_to_speech(
    text: str,
    *,
    model: Optional[str] = None,                 # canopylabs/orpheus-v1-english | orpheus-arabic-saudi
    voice: Optional[str] = None,
    response_format: Optional[str] = None,       # Default wav; (API-Referenz nennt auch flac/mp3/mulaw/ogg)
    sample_rate: Optional[int] = None,
    speed: Optional[float] = None,
) -> bytes:
    """Vocal Directions im Text, z.B. "[cheerful] Hallo!". sample_rate/speed stehen nur in der
    API-Referenz (die noch playai-tts nennt) - bei 400 einfach weglassen."""
    body = drop_none(dict(
        model=model or DEFAULTS["tts"], input=text, voice=voice or DEFAULTS["tts_voice"],
        response_format=response_format, sample_rate=sample_rate, speed=speed,
    ))
    return post("/audio/speech", body).content


# ---------------------------------------------------------------------------
# Modul 6: Batch (Files + Batches)
# ---------------------------------------------------------------------------

def upload_file(path: str | Path, purpose: str = "batch") -> dict:
    with open(path, "rb") as f:
        r = requests.post(f"{BASE}/files", headers=headers(False), data={"purpose": purpose},
                          files={"file": (Path(path).name, f)}, timeout=300)
    return check(r).json()


def build_batch_jsonl(prompts: list[str], model: str, path: str | Path, max_tokens: int = 256) -> Path:
    """Jede Zeile: custom_id + method + url + body (OpenAI-Batch-Format)."""
    p = Path(path)
    with p.open("w", encoding="utf-8") as f:
        for i, pr in enumerate(prompts):
            f.write(json.dumps({
                "custom_id": f"req-{i}", "method": "POST", "url": "/v1/chat/completions",
                "body": {"model": model, "max_completion_tokens": max_tokens,
                         "messages": [{"role": "user", "content": pr}]},
            }, ensure_ascii=False) + "\n")
    return p


def create_batch(input_file_id: str, *, completion_window: str = "24h", metadata: Optional[dict] = None) -> dict:
    return post("/batches", drop_none(dict(
        input_file_id=input_file_id, endpoint="/v1/chat/completions",
        completion_window=completion_window, metadata=metadata))).json()


def get_batch(batch_id: str) -> dict:
    return get(f"/batches/{batch_id}").json()


def cancel_batch(batch_id: str) -> dict:
    return post(f"/batches/{batch_id}/cancel", {}).json()


def list_batches() -> list[dict]:
    return get("/batches").json().get("data", [])


def file_content(file_id: str) -> str:
    return check(requests.get(f"{BASE}/files/{file_id}/content", headers=headers(False), timeout=300)).text


def run_batch(prompts: list[str], *, model: Optional[str] = None, poll: int = 20, timeout: int = 3600) -> list[dict]:
    """JSONL bauen -> hochladen -> Batch starten -> warten -> Ergebnisse (Liste von Zeilen-Dicts)."""
    jl = build_batch_jsonl(prompts, model or DEFAULTS["fast"], out_path("batch_input.jsonl"))
    fid = upload_file(jl)["id"]
    b = create_batch(fid)
    print(f"  batch {b['id']} status={b['status']}")
    deadline = time.time() + timeout
    while b["status"] not in ("completed", "failed", "expired", "cancelled"):
        if time.time() > deadline:
            raise TimeoutError(f"Batch {b['id']} nicht fertig nach {timeout}s (laeuft weiter: batch status {b['id']})")
        time.sleep(poll)
        b = get_batch(b["id"])
        print(f"  status={b['status']} {b.get('request_counts')}", flush=True)
    if b["status"] != "completed" or not b.get("output_file_id"):
        raise GroqError(f"Batch {b['status']}: errors={b.get('errors')} error_file={b.get('error_file_id')}")
    return [json.loads(line) for line in file_content(b["output_file_id"]).splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_models(a: argparse.Namespace) -> None:
    ms = list_models()
    if a.filter:
        ms = [m for m in ms if a.filter.lower() in m["id"].lower()]
    print(f"{len(ms)} Modelle")
    for m in sorted(ms, key=lambda x: x["id"]):
        print(f"{m['id']:<50} ctx={m.get('context_window', '-'):<8} active={m.get('active')} owner={m.get('owned_by')}")


def cmd_chat(a: argparse.Namespace) -> None:
    rf = None
    if a.schema:
        rf = json_schema_format("answer", {
            "type": "object",
            "properties": {"answer": {"type": "string"}, "confidence": {"type": "number"}},
            "required": ["answer", "confidence"], "additionalProperties": False,
        }, strict=not a.loose)
    elif a.json:
        rf = {"type": "json_object"}
    tools = demo_tool() if a.tools else None
    model = a.model or (DEFAULTS["reasoning"] if (a.schema or a.effort) and not a.model else None)
    res = chat_completion(
        a.prompt, system=a.system, image=a.image, model=model, stream=a.stream,
        max_completion_tokens=a.max_tokens, temperature=a.temperature,
        reasoning_effort=a.effort, reasoning_format=a.reasoning_format,
        include_reasoning=False if a.hide_reasoning else None, response_format=rf, tools=tools,
        tool_choice="auto" if tools else None, service_tier=a.tier,
        search_settings={"include_domains": a.domains} if a.domains else None, raw=not a.stream,
    )
    if a.stream:
        for kind, txt in stream_text(res):
            if kind == "reasoning":
                print(f"\033[2m{txt}\033[0m", end="", flush=True)
            elif kind == "tool":
                print(f"[tool]{txt}", end="", flush=True)
            elif kind == "usage":
                print(f"\n[usage] {txt}")
            else:
                print(txt, end="", flush=True)
        print()
        return
    msg = res["choices"][0]["message"]
    if msg.get("reasoning"):
        print("[reasoning]", msg["reasoning"][:500], "\n")
    for tc in msg.get("tool_calls") or []:
        print(f"TOOL CALL: {tc['function']['name']}({tc['function']['arguments']})")
    print(msg.get("content"))
    u = res.get("usage") or {}
    print(f"\n[model={res.get('model')} tokens={u.get('total_tokens')} total_time={u.get('total_time')}s tier={res.get('service_tier')}]")


def cmd_resp(a: argparse.Namespace) -> None:
    tools: list[dict] = []
    if a.browser:
        tools.append(BROWSER_TOOL)
    if a.code:
        tools.append(CODE_TOOL)
    if a.mcp:
        tools.append(mcp_tool("mcp", a.mcp))
    r = responses(a.prompt, model=a.model, instructions=a.system, tools=tools or None,
                  tool_choice="required" if tools else None, reasoning_effort=a.effort, metrics=a.metrics)
    print(responses_text(r) or json.dumps(r, indent=2, ensure_ascii=False)[:2000])
    print(f"\n[usage={r.get('usage')}]" + (f" [metrics={r.get('metadata')}]" if a.metrics else ""))


def cmd_stt(a: argparse.Namespace) -> None:
    r = speech_to_text(a.audio, model=a.model, language=a.language, translate=a.translate,
                       timestamps=["word", "segment"] if a.words else None,
                       response_format="text" if a.text_only else None)
    if isinstance(r, str):
        print(r)
        return
    print(r.get("text"))
    if a.words:
        print("Segmente:", len(r.get("segments", [])), "| Woerter:", len(r.get("words", [])))


def cmd_tts(a: argparse.Namespace) -> None:
    fmt = a.out.rsplit(".", 1)[-1] if "." in a.out else "wav"
    audio = text_to_speech(a.text, model=a.model, voice=a.voice, response_format=fmt)
    p = out_path(a.out)
    p.write_bytes(audio)
    print("gespeichert:", p, f"({len(audio)} Bytes)")


def cmd_batch(a: argparse.Namespace) -> None:
    if a.action == "run":
        prompts = [l.strip() for l in Path(a.arg).read_text(encoding="utf-8").splitlines() if l.strip()]
        for row in run_batch(prompts, model=a.model):
            body = (row.get("response") or {}).get("body") or {}
            txt = (body.get("choices") or [{}])[0].get("message", {}).get("content")
            print(f"{row.get('custom_id')}: {str(txt)[:100]}")
    elif a.action == "list":
        for b in list_batches():
            print(b["id"], b["status"], b.get("request_counts"))
    elif a.action == "status":
        print(json.dumps(get_batch(a.arg), indent=2))
    elif a.action == "cancel":
        print(cancel_batch(a.arg)["status"])


def cmd_all(a: argparse.Namespace) -> None:
    tests: list[tuple[str, Callable[[], Any]]] = [
        ("models", lambda: len(list_models())),
        ("chat", lambda: chat_completion("Sag 'ok'.", max_completion_tokens=20)["content"]),
        ("chat-stream", lambda: "".join(t for k, t in stream_text(chat_completion("Zaehle bis 3.", stream=True, max_completion_tokens=30)) if k == "content")),
        ("json_object", lambda: chat_completion("Gib JSON mit key a=1.", response_format={"type": "json_object"}, max_completion_tokens=40)["content"]),
        ("strict-schema", lambda: chat_completion("Name: Max, Alter 31", model=DEFAULTS["reasoning"], max_completion_tokens=300,
                                                  response_format=json_schema_format("p", {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
                                                                                           "required": ["name", "age"], "additionalProperties": False}))["content"]),
        ("reasoning", lambda: bool(chat_completion("17*23?", model=DEFAULTS["reasoning"], reasoning_effort="low", max_completion_tokens=400).get("reasoning"))),
        ("tools", lambda: bool(chat_completion("Wetter in Berlin?", tools=demo_tool(), tool_choice="required")["tool_calls"])),
        ("responses", lambda: responses_text(responses("Sag 'ok'."))[:40]),
        ("resp-code", lambda: responses_text(responses("1312*3333? Nur die Zahl.", tools=[CODE_TOOL], tool_choice="required"))[:40]),
        ("tts", lambda: len(text_to_speech("[cheerful] Hallo"))),
    ]
    if a.audio:
        tests.append(("stt", lambda: speech_to_text(a.audio)["text"][:50]))  # type: ignore[index]
    rows = []
    for name, fn in tests:
        t0 = time.time()
        try:
            rows.append((name, "OK", f"{time.time() - t0:.1f}s", str(fn())[:60]))
        except Exception as e:  # noqa: BLE001
            rows.append((name, "FAIL", f"{time.time() - t0:.1f}s", f"{type(e).__name__}: {e}"[:110]))
            if a.verbose:
                traceback.print_exc()
    print(f"\n{'Modul':<15}{'Status':<8}{'Zeit':<8}Detail\n" + "-" * 80)
    for r in rows:
        print(f"{r[0]:<15}{r[1]:<8}{r[2]:<8}{r[3]}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Groq - modulare Tests")
    sub = p.add_subparsers(dest="cmd", required=True)

    def mk(name: str, fn: Callable, **kw: Any) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, **kw)
        sp.set_defaults(func=fn)
        return sp

    sp = mk("models", cmd_models); sp.add_argument("--filter")

    sp = mk("chat", cmd_chat)
    sp.add_argument("prompt"); sp.add_argument("--model"); sp.add_argument("--system"); sp.add_argument("--image")
    sp.add_argument("--stream", action="store_true")
    sp.add_argument("--effort", help="reasoning_effort: low|medium|high (gpt-oss) / none|default|... (qwen)")
    sp.add_argument("--reasoning-format", choices=["raw", "parsed", "hidden"], help="nicht fuer gpt-oss")
    sp.add_argument("--hide-reasoning", action="store_true", help="include_reasoning=false (gpt-oss)")
    sp.add_argument("--schema", action="store_true", help="Structured Output (strict)")
    sp.add_argument("--loose", action="store_true", help="mit --schema: strict=false")
    sp.add_argument("--json", action="store_true", help="json_object")
    sp.add_argument("--tools", action="store_true")
    sp.add_argument("--tier", choices=["auto", "on_demand", "flex", "performance"])
    sp.add_argument("--domains", nargs="+", help="search_settings.include_domains")
    sp.add_argument("--max-tokens", type=int, default=512); sp.add_argument("--temperature", type=float)

    sp = mk("resp", cmd_resp, help="Responses API (beta)")
    sp.add_argument("prompt"); sp.add_argument("--model"); sp.add_argument("--system"); sp.add_argument("--effort")
    sp.add_argument("--browser", action="store_true", help="Tool browser_search (gpt-oss)")
    sp.add_argument("--code", action="store_true", help="Tool code_interpreter (gpt-oss)")
    sp.add_argument("--mcp", help="URL eines Remote-MCP-Servers")
    sp.add_argument("--metrics", action="store_true", help="Header Groq-Beta: inference-metrics")

    sp = mk("stt", cmd_stt)
    sp.add_argument("audio", help="Datei oder https-URL"); sp.add_argument("--model"); sp.add_argument("--language")
    sp.add_argument("--translate", action="store_true", help="nach Englisch (whisper-large-v3)")
    sp.add_argument("--words", action="store_true", help="verbose_json + word/segment timestamps")
    sp.add_argument("--text-only", action="store_true")

    sp = mk("tts", cmd_tts)
    sp.add_argument("text"); sp.add_argument("--model"); sp.add_argument("--voice"); sp.add_argument("--out", default="speech.wav")

    sp = mk("batch", cmd_batch)
    sp.add_argument("action", choices=["run", "list", "status", "cancel"])
    sp.add_argument("arg", nargs="?", help="run: Datei mit einem Prompt pro Zeile | status/cancel: batch_id")
    sp.add_argument("--model")

    sp = mk("all", cmd_all, help="Smoke-Test")
    sp.add_argument("--audio", help="Audiodatei fuer STT-Test"); sp.add_argument("--verbose", action="store_true")
    return p


def main() -> None:
    a = build_parser().parse_args()
    try:
        a.func(a)
    except GroqError as e:
        sys.exit(f"Fehler: {e}")


if __name__ == "__main__":
    main()
