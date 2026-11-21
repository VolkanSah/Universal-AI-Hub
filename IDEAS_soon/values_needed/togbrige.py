#!/usr/bin/env python3
"""
togbrige.py - modulare Testdatei fuer Together AI (Doku-Stand: Nov 2026).

Ein Modul (Funktion + CLI-Subcommand) pro Feature, nur `requests`, kein SDK.

Setup:
    pip install -U requests
    export TOGETHER_API_KEY=...

Module / Endpunkte (Base: https://api.together.ai/v1):
    models   GET  /models
    chat     POST /chat/completions   Stream, Reasoning, JSON-Schema, Regex, Tools, Vision
    resp     POST /responses          nur wenige Modelle (GLM-5.x, DeepSeek-V4.1-Flash), stateless
    image    POST /images/generations (FLUX.2, gpt-image-2, Seedream, ...)
    video    POST /v2/videos + GET /v2/videos/{id}   async
    tts      POST /audio/speech
    stt      POST /audio/transcriptions | /audio/translations  (multipart)
    rerank   POST /rerank       NUR dedicated endpoints (kein Serverless)
    embed    POST /embeddings   NUR dedicated endpoints (kein Serverless)
    all      Smoke-Test

Beispiele:
    python togbrige.py chat "Hallo" --stream
    python togbrige.py chat "Beweise sqrt(2) irrational" --model deepseek-ai/DeepSeek-V4-Pro-0813 --reasoning on
    python togbrige.py chat "Review: super Produkt" --regex "(positive|neutral|negative)"
    python togbrige.py chat "Was ist drauf?" --image foto.jpg --model Qwen/Qwen3.5-9B
    python togbrige.py chat "Wetter in Berlin?" --tools
    python togbrige.py image "a red panda astronaut" --steps 20
    python togbrige.py video "A calm ocean at sunset" --seconds 6
    python togbrige.py tts "Hallo Welt" --voice tara --out hallo.mp3
    python togbrige.py stt hallo.mp3
    python togbrige.py rerank "Tiere in Peru?" "Lamas leben in den Anden" "Pinguine leben in der Antarktis" --model mixedbread-ai/mxbai-rerank-large-v2
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

BASE = os.environ.get("TOGETHER_BASE", "https://api.together.ai/v1")
BASE_V2 = BASE.rsplit("/v1", 1)[0] + "/v2"  # Video-Endpunkte liegen unter /v2
OUT_DIR = Path("tog_out")

# Slugs aus den Doku-Seiten (serverless catalog). Per --model ueberschreibbar.
DEFAULTS = {
    "chat": "zai-org/GLM-5.3-Flash",
    "vlm": "Qwen/Qwen3.5-9B",
    "resp": "zai-org/GLM-5.3",
    "image": "black-forest-labs/FLUX.2-dev",
    "video": "google/veo-3.1-lite",
    "tts": "hexgrad/Kokoro-82M",
    "tts_voice": "af_heart",
    "stt": "openai/whisper-large-v3",
    "rerank": "mixedbread-ai/mxbai-rerank-large-v2",  # dedicated only
    "embed": None,                                    # dedicated only: Endpoint-Name angeben
}


# ---------------------------------------------------------------------------
# Helfer
# ---------------------------------------------------------------------------

class TogError(RuntimeError):
    pass


def _key() -> str:
    k = os.environ.get("TOGETHER_API_KEY")
    if not k:
        sys.exit("TOGETHER_API_KEY fehlt. Setze: export TOGETHER_API_KEY=...")
    return k


def headers(json_body: bool = True) -> dict[str, str]:
    h = {"Authorization": f"Bearer {_key()}", "User-Agent": "togbrige/1.0"}  # leerer UA -> 403 bei Downloads
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def check(r: requests.Response) -> requests.Response:
    if r.ok:
        return r
    try:
        err = r.json().get("error", r.text)
    except Exception:  # noqa: BLE001
        err = r.text
    raise TogError(f"HTTP {r.status_code}: {err}")


def post(path: str, body: dict, *, base: str = BASE, timeout: int = 300, stream: bool = False) -> requests.Response:
    return check(requests.post(f"{base}{path}", headers=headers(), json=body, timeout=timeout, stream=stream))


def get(path: str, params: Optional[dict] = None, *, base: str = BASE, timeout: int = 60) -> requests.Response:
    return check(requests.get(f"{base}{path}", headers=headers(False), params=params, timeout=timeout))


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


def json_schema_format(name: str, schema: dict) -> dict:
    """Together: type=json_schema mit {name, schema} (kein strict-Flag dokumentiert)."""
    return {"type": "json_schema", "json_schema": {"name": name, "schema": schema}}


def regex_format(pattern: str) -> dict:
    """Together-spezifisch: Output wird auf Regex beschraenkt."""
    return {"type": "regex", "pattern": pattern}


def demo_tool() -> list[dict]:
    return [{
        "type": "function",
        "function": {
            "name": "get_current_weather",
            "description": "Get the current weather in a given location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City, e.g. Berlin"},
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
    d = get("/models").json()
    return d if isinstance(d, list) else d.get("data", [])


# ---------------------------------------------------------------------------
# Modul 2: Chat Completions
# ---------------------------------------------------------------------------

def chat_completion(
    prompt: Optional[str] = None,
    *,
    messages: Optional[list[dict]] = None,
    system: Optional[str] = None,
    image: Optional[str] = None,
    model: Optional[str] = None,
    stream: bool = False,
    max_tokens: Optional[int] = 512,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    top_k: Optional[int] = None,
    repetition_penalty: Optional[float] = None,
    seed: Optional[int] = None,
    stop: Optional[list[str]] = None,
    reasoning_enabled: Optional[bool] = None,   # reasoning={"enabled": ..} (Hybrid-Modelle)
    reasoning_effort: Optional[str] = None,     # GPT-OSS: low|medium|high; DeepSeek V4 Pro: high|max
    response_format: Optional[dict] = None,
    tools: Optional[list[dict]] = None,
    tool_choice: Optional[Any] = None,          # auto|none|required|{"type":"function",...}
    chat_template_kwargs: Optional[dict] = None,
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
        model=model or DEFAULTS["vlm" if image else "chat"], messages=messages,
        stream=True if stream else None, max_tokens=max_tokens, temperature=temperature,
        top_p=top_p, top_k=top_k, repetition_penalty=repetition_penalty, seed=seed, stop=stop,
        reasoning={"enabled": reasoning_enabled} if reasoning_enabled is not None else None,
        reasoning_effort=reasoning_effort, response_format=response_format, tools=tools,
        tool_choice=tool_choice, chat_template_kwargs=chat_template_kwargs,
    ))
    body.update(extra or {})

    if stream:
        return sse_events(post("/chat/completions", body, stream=True))
    data = post("/chat/completions", body).json()
    return data if raw else data["choices"][0]["message"]


def stream_text(chunks: Iterator[dict]) -> Iterator[tuple[str, str]]:
    """('content'|'reasoning'|'tool'|'usage', text). Reasoning kommt je nach Modell als
    delta.reasoning ODER delta.reasoning_content (DeepSeek-R1: <think> inline in content)."""
    for ch in chunks:
        if ch.get("usage"):
            yield "usage", json.dumps(ch["usage"])
        for c in ch.get("choices", []):
            d = c.get("delta") or {}
            r = d.get("reasoning") or d.get("reasoning_content")
            if r:
                yield "reasoning", r
            if d.get("content"):
                yield "content", d["content"]
            for tc in d.get("tool_calls") or []:
                fn = tc.get("function") or {}
                yield "tool", (fn.get("name") or "") + (fn.get("arguments") or "")


def message_reasoning(msg: dict) -> Optional[str]:
    return msg.get("reasoning") or msg.get("reasoning_content")


# ---------------------------------------------------------------------------
# Modul 3: Responses API (stateless, nur ausgewaehlte Modelle)
# ---------------------------------------------------------------------------

def responses(
    input_: str | list[dict],
    *,
    model: Optional[str] = None,
    instructions: Optional[str] = None,
    reasoning_effort: Optional[str] = None,  # none|minimal|low|medium|high|xhigh
    text_format: Optional[dict] = None,      # {"type":"json_schema","name":..,"strict":True,"schema":{..}}
    tools: Optional[list[dict]] = None,      # nur function-Tools (name/description/parameters flach!)
    tool_choice: Optional[Any] = None,
    max_output_tokens: Optional[int] = None,
    extra: Optional[dict] = None,
) -> dict:
    """Kein store / previous_response_id / conversation -> Verlauf selbst mitschicken."""
    body = drop_none(dict(
        model=model or DEFAULTS["resp"], input=input_, instructions=instructions,
        reasoning={"effort": reasoning_effort} if reasoning_effort else None,
        text={"format": text_format} if text_format else None,
        tools=tools, tool_choice=tool_choice, max_output_tokens=max_output_tokens,
    ))
    body.update(extra or {})
    return post("/responses", body).json()


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


# ---------------------------------------------------------------------------
# Modul 4: Image Generation
# ---------------------------------------------------------------------------

def generate_image(
    prompt: Optional[str],
    *,
    model: Optional[str] = None,
    steps: Optional[int] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    aspect_ratio: Optional[str] = None,     # Schnell / Kontext statt width+height
    n: Optional[int] = None,
    seed: Optional[int] = None,
    negative_prompt: Optional[str] = None,
    guidance: Optional[float] = None,       # FLUX.2 Dev
    image_url: Optional[str] = None,        # Editing (Kontext)
    reference_images: Optional[list[str]] = None,  # I2I (FLUX.2 Pro)
    base64_out: bool = True,                # True -> b64_json statt temporaerer URL
    extra: Optional[dict] = None,
) -> list[dict]:
    body = drop_none(dict(
        model=model or DEFAULTS["image"], prompt=prompt, steps=steps, width=width, height=height,
        aspect_ratio=aspect_ratio, n=n, seed=seed, negative_prompt=negative_prompt, guidance=guidance,
        image_url=image_url,
        reference_images=[to_data_url(x) for x in reference_images] if reference_images else None,
        response_format="base64" if base64_out else None,
    ))
    body.update(extra or {})
    return post("/images/generations", body).json()["data"]


def save_images(data: list[dict], stem: str = "image") -> list[Path]:
    paths = []
    for i, img in enumerate(data):
        p = out_path(f"{stem}_{i}.png")
        if img.get("b64_json"):
            p.write_bytes(base64.b64decode(img["b64_json"]))
        elif img.get("url"):
            p.write_bytes(requests.get(img["url"], headers={"User-Agent": "togbrige/1.0"}, timeout=120).content)
        paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# Modul 5: Video Generation (async, /v2)
# ---------------------------------------------------------------------------

def video_submit(
    prompt: Optional[str],
    *,
    model: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    resolution: Optional[str] = None,
    ratio: Optional[str] = None,
    seconds: Optional[str] = None,          # String!
    fps: Optional[int] = None,
    steps: Optional[int] = None,            # 10..50
    seed: Optional[int] = None,
    guidance_scale: Optional[int] = None,
    negative_prompt: Optional[str] = None,
    output_format: Optional[str] = None,    # MP4 | WEBM
    output_quality: Optional[int] = None,
    generate_audio: Optional[bool] = None,
    first_frame: Optional[str] = None,      # -> media.frame_images[frame=first]
    last_frame: Optional[str] = None,
    reference_images: Optional[list[str]] = None,
    media: Optional[dict] = None,           # roher media-Block (frame_videos, source_video, audio_inputs ...)
) -> dict:
    m: dict = dict(media or {})
    frames = []
    if first_frame:
        frames.append({"input_image": to_data_url(first_frame), "frame": "first"})
    if last_frame:
        frames.append({"input_image": to_data_url(last_frame), "frame": "last"})
    if frames:
        m["frame_images"] = frames
    if reference_images:
        m["reference_images"] = [to_data_url(x) for x in reference_images]
    body = drop_none(dict(
        model=model or DEFAULTS["video"], prompt=prompt, width=width, height=height,
        resolution=resolution, ratio=ratio, seconds=seconds, fps=fps, steps=steps, seed=seed,
        guidance_scale=guidance_scale, negative_prompt=negative_prompt, output_format=output_format,
        output_quality=output_quality, generate_audio=generate_audio, media=m or None,
    ))
    return post("/videos", body, base=BASE_V2).json()


def video_poll(job_id: str, *, interval: int = 15, timeout: int = 1200, on_status: Optional[Callable] = None) -> dict:
    deadline = time.time() + timeout
    while True:
        job = get(f"/videos/{job_id}", base=BASE_V2).json()
        if on_status:
            on_status(job["status"])
        if job["status"] == "completed":
            return job
        if job["status"] == "failed":
            raise TogError(f"Video-Job {job_id} failed: {job.get('error')}")
        if time.time() > deadline:
            raise TimeoutError(f"Video-Job {job_id} nicht fertig nach {timeout}s")
        time.sleep(interval)


def generate_video(prompt: str, out: str = "video.mp4", **kw) -> tuple[Path, dict]:
    """URLs laufen ab -> direkt nach 'completed' herunterladen."""
    job = video_submit(prompt, **kw)
    done = video_poll(job["id"], on_status=lambda s: print(f"  status: {s}", flush=True))
    url = done["outputs"]["video_url"]
    p = out_path(out)
    p.write_bytes(check(requests.get(url, headers={"User-Agent": "togbrige/1.0"}, timeout=300)).content)
    return p, done.get("outputs", {})


# ---------------------------------------------------------------------------
# Modul 6: Text-to-Speech
# ---------------------------------------------------------------------------

def text_to_speech(
    text: str,
    *,
    model: Optional[str] = None,
    voice: Optional[str] = None,            # Kokoro: af_heart, Orpheus: tara, Cartesia: Voice-ID
    response_format: Optional[str] = None,  # mp3|wav|raw|mulaw (Default wav)
    sample_rate: Optional[int] = None,
    language: Optional[str] = None,
    extra_params: Optional[dict] = None,    # z.B. {"pronunciation_dict": [...]}
) -> bytes:
    body = drop_none(dict(
        model=model or DEFAULTS["tts"], input=text, voice=voice or DEFAULTS["tts_voice"],
        response_format=response_format, sample_rate=sample_rate, language=language,
        extra_params=extra_params,
    ))
    return post("/audio/speech", body).content


def list_voices(model: str) -> Any:
    return get("/voices", {"model": model}).json()


# ---------------------------------------------------------------------------
# Modul 7: Speech-to-Text / Translation (multipart!)
# ---------------------------------------------------------------------------

def speech_to_text(
    audio: str,                      # lokale Datei ODER https-URL (URL nur ueber REST)
    *,
    model: Optional[str] = None,
    language: Optional[str] = None,  # ISO-639-1, Default auto
    response_format: Optional[str] = "json",
    prompt: Optional[str] = None,    # nur Whisper-Familie
    translate: bool = False,         # -> /audio/translations (nach Englisch)
) -> dict:
    data = drop_none(dict(model=model or DEFAULTS["stt"], language=language,
                          response_format=response_format, prompt=prompt))
    path = "/audio/translations" if translate else "/audio/transcriptions"
    if audio.startswith(("http://", "https://")):
        data["file"] = audio
        r = requests.post(f"{BASE}{path}", headers=headers(False), data=data, timeout=600)
    else:
        with open(audio, "rb") as f:
            # Doku: bei Binary-Upload 'model' VOR 'file' -> dict-Reihenfolge + files am Ende
            r = requests.post(f"{BASE}{path}", headers=headers(False), data=data,
                              files={"file": (Path(audio).name, f)}, timeout=600)
    return check(r).json()


# ---------------------------------------------------------------------------
# Modul 8: Rerank + Embeddings (beide NUR dedicated endpoints)
# ---------------------------------------------------------------------------

def rerank(
    query: str,
    documents: list[str] | list[dict],       # dicts nur bei Salesforce/Llama-Rank-V1
    *,
    model: Optional[str] = None,
    top_n: Optional[int] = None,
    return_documents: Optional[bool] = None,
    rank_fields: Optional[list[str]] = None,
) -> dict:
    body = drop_none(dict(model=model or DEFAULTS["rerank"], query=query, documents=documents,
                          top_n=top_n, return_documents=return_documents, rank_fields=rank_fields))
    return post("/rerank", body).json()


def embeddings(input_: str | list[str], *, model: str) -> dict:
    """Kein Serverless-Embedding-Modell: `model` = Name deines Dedicated Endpoints."""
    return post("/embeddings", {"model": model, "input": input_}).json()


def rerank_rows(resp: dict) -> list[dict]:
    """Doku zeigt mal `results`, mal `choices` - beides abfangen."""
    return resp.get("results") or resp.get("choices") or []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_models(a: argparse.Namespace) -> None:
    ms = list_models()
    if a.filter:
        ms = [m for m in ms if a.filter.lower() in json.dumps(m).lower()]
    print(f"{len(ms)} Modelle")
    for m in ms[: a.show]:
        print(f"{m.get('id', '?'):<60} {m.get('type', ''):<10} ctx={m.get('context_length', '-')}")


def cmd_chat(a: argparse.Namespace) -> None:
    rf = None
    if a.regex:
        rf = regex_format(a.regex)
    elif a.schema:
        rf = json_schema_format("answer", {
            "type": "object",
            "properties": {"answer": {"type": "string"}, "confidence": {"type": "number"}},
            "required": ["answer", "confidence"],
        })
    tools = demo_tool() if a.tools else None
    re_on = {"on": True, "off": False}.get(a.reasoning) if a.reasoning else None
    re_eff = a.reasoning if a.reasoning in ("low", "medium", "high", "max") else None
    res = chat_completion(
        a.prompt, system=a.system, image=a.image, model=a.model, stream=a.stream,
        max_tokens=a.max_tokens, temperature=a.temperature, reasoning_enabled=re_on,
        reasoning_effort=re_eff, response_format=rf, tools=tools,
        tool_choice="auto" if tools else None, stop=["\n"] if a.regex and "@" in a.regex else None,
        raw=not a.stream,
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
    if message_reasoning(msg):
        print("[reasoning]", message_reasoning(msg)[:500], "\n")
    for tc in msg.get("tool_calls") or []:
        print(f"TOOL CALL: {tc['function']['name']}({tc['function']['arguments']})")
    print(msg.get("content"))
    print(f"\n[model={res.get('model')} usage={res.get('usage')}]")


def cmd_resp(a: argparse.Namespace) -> None:
    r = responses(a.prompt, model=a.model, instructions=a.system, reasoning_effort=a.reasoning)
    print(responses_text(r) or json.dumps(r, indent=2, ensure_ascii=False)[:2000])
    print(f"\n[usage={r.get('usage')}]")


def cmd_image(a: argparse.Namespace) -> None:
    data = generate_image(a.prompt, model=a.model, steps=a.steps, width=a.width, height=a.height,
                          aspect_ratio=a.aspect, n=a.n, seed=a.seed, negative_prompt=a.negative,
                          image_url=a.edit, reference_images=a.ref)
    for p in save_images(data, Path(a.out).stem):
        print("gespeichert:", p)


def cmd_video(a: argparse.Namespace) -> None:
    p, outs = generate_video(a.prompt, a.out, model=a.model, width=a.width, height=a.height,
                             resolution=a.resolution, ratio=a.ratio, seconds=a.seconds, seed=a.seed,
                             first_frame=a.first_frame, last_frame=a.last_frame,
                             generate_audio=True if a.audio else None)
    print("gespeichert:", p, "| outputs:", {k: v for k, v in outs.items() if k != "video_url"})


def cmd_tts(a: argparse.Namespace) -> None:
    fmt = a.out.rsplit(".", 1)[-1] if "." in a.out else "wav"
    audio = text_to_speech(a.text, model=a.model, voice=a.voice, response_format=fmt, language=a.language)
    p = out_path(a.out)
    p.write_bytes(audio)
    print("gespeichert:", p, f"({len(audio)} Bytes)")


def cmd_voices(a: argparse.Namespace) -> None:
    print(json.dumps(list_voices(a.model or DEFAULTS["tts"]), indent=2, ensure_ascii=False)[:3000])


def cmd_stt(a: argparse.Namespace) -> None:
    r = speech_to_text(a.audio, model=a.model, language=a.language, translate=a.translate)
    print(r.get("text"))


def cmd_rerank(a: argparse.Namespace) -> None:
    r = rerank(a.query, a.documents, model=a.model, top_n=a.top_n, return_documents=True)
    for row in rerank_rows(r):
        doc = row.get("document")
        txt = doc.get("text") if isinstance(doc, dict) else doc
        print(f"{row['relevance_score']:.4f}  [{row['index']}] {str(txt)[:80]}")


def cmd_embed(a: argparse.Namespace) -> None:
    r = embeddings(a.texts if len(a.texts) > 1 else a.texts[0], model=a.model)
    vecs = [d["embedding"] for d in r["data"]]
    print(f"{len(vecs)} Vektoren, dim={len(vecs[0])}")


def cmd_all(a: argparse.Namespace) -> None:
    tests: list[tuple[str, Callable[[], Any]]] = [
        ("models", lambda: len(list_models())),
        ("chat", lambda: chat_completion("Sag 'ok'.", max_tokens=20)["content"]),
        ("chat-stream", lambda: "".join(t for k, t in stream_text(chat_completion("Zaehle bis 3.", stream=True, max_tokens=30)) if k == "content")),
        ("chat-regex", lambda: chat_completion("Sentiment: 'toll!'", response_format=regex_format("(positive|neutral|negative)"), max_tokens=10)["content"]),
        ("chat-tools", lambda: bool(chat_completion("Wetter in Berlin?", tools=demo_tool(), tool_choice="required")["tool_calls"])),
        ("responses", lambda: responses_text(responses("Sag 'ok'."))[:40]),
        ("tts", lambda: len(text_to_speech("Hallo", response_format="mp3"))),
    ]
    if a.audio:
        tests.append(("stt", lambda: speech_to_text(a.audio)["text"][:50]))
    if a.media:
        tests.append(("image", lambda: save_images(generate_image("a red apple", steps=20), "smoke_image")))
    if a.video:
        tests.append(("video", lambda: generate_video("a cat walking", "smoke_video.mp4")[0]))
    rows = []
    for name, fn in tests:
        t0 = time.time()
        try:
            rows.append((name, "OK", f"{time.time() - t0:.1f}s", str(fn())[:60]))
        except Exception as e:  # noqa: BLE001
            rows.append((name, "FAIL", f"{time.time() - t0:.1f}s", f"{type(e).__name__}: {e}"[:110]))
            if a.verbose:
                traceback.print_exc()
    print(f"\n{'Modul':<14}{'Status':<8}{'Zeit':<8}Detail\n" + "-" * 80)
    for r in rows:
        print(f"{r[0]:<14}{r[1]:<8}{r[2]:<8}{r[3]}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Together AI - modulare Tests")
    sub = p.add_subparsers(dest="cmd", required=True)

    def mk(name: str, fn: Callable, **kw: Any) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, **kw)
        sp.set_defaults(func=fn)
        return sp

    sp = mk("models", cmd_models); sp.add_argument("--filter"); sp.add_argument("--show", type=int, default=40)

    sp = mk("chat", cmd_chat)
    sp.add_argument("prompt"); sp.add_argument("--model"); sp.add_argument("--system"); sp.add_argument("--image")
    sp.add_argument("--stream", action="store_true")
    sp.add_argument("--reasoning", help="on|off (Hybrid) oder low|medium|high|max (effort)")
    sp.add_argument("--schema", action="store_true", help="json_schema"); sp.add_argument("--regex")
    sp.add_argument("--tools", action="store_true")
    sp.add_argument("--max-tokens", type=int, default=512); sp.add_argument("--temperature", type=float)

    sp = mk("resp", cmd_resp)
    sp.add_argument("prompt"); sp.add_argument("--model"); sp.add_argument("--system"); sp.add_argument("--reasoning")

    sp = mk("image", cmd_image)
    sp.add_argument("prompt"); sp.add_argument("--model"); sp.add_argument("--out", default="image.png")
    sp.add_argument("--steps", type=int); sp.add_argument("--width", type=int); sp.add_argument("--height", type=int)
    sp.add_argument("--aspect"); sp.add_argument("--n", type=int); sp.add_argument("--seed", type=int)
    sp.add_argument("--negative"); sp.add_argument("--edit", help="image_url (Kontext)")
    sp.add_argument("--ref", nargs="+", help="reference_images (FLUX.2 Pro)")

    sp = mk("video", cmd_video)
    sp.add_argument("prompt"); sp.add_argument("--model"); sp.add_argument("--out", default="video.mp4")
    sp.add_argument("--width", type=int); sp.add_argument("--height", type=int)
    sp.add_argument("--resolution"); sp.add_argument("--ratio"); sp.add_argument("--seconds")
    sp.add_argument("--seed", type=int); sp.add_argument("--audio", action="store_true")
    sp.add_argument("--first-frame"); sp.add_argument("--last-frame")

    sp = mk("tts", cmd_tts)
    sp.add_argument("text"); sp.add_argument("--model"); sp.add_argument("--voice")
    sp.add_argument("--language"); sp.add_argument("--out", default="speech.mp3")

    sp = mk("voices", cmd_voices); sp.add_argument("--model")

    sp = mk("stt", cmd_stt)
    sp.add_argument("audio", help="Datei oder https-URL"); sp.add_argument("--model")
    sp.add_argument("--language"); sp.add_argument("--translate", action="store_true")

    sp = mk("rerank", cmd_rerank, help="nur dedicated")
    sp.add_argument("query"); sp.add_argument("documents", nargs="+"); sp.add_argument("--model"); sp.add_argument("--top-n", type=int)

    sp = mk("embed", cmd_embed, help="nur dedicated")
    sp.add_argument("texts", nargs="+"); sp.add_argument("--model", required=True, help="Dedicated-Endpoint-Name")

    sp = mk("all", cmd_all, help="Smoke-Test")
    sp.add_argument("--audio", help="Audiodatei fuer STT-Test")
    sp.add_argument("--media", action="store_true", help="auch Bild (kostet)")
    sp.add_argument("--video", action="store_true", help="auch Video (langsam, teuer)")
    sp.add_argument("--verbose", action="store_true")
    return p


def main() -> None:
    a = build_parser().parse_args()
    try:
        a.func(a)
    except TogError as e:
        sys.exit(f"Fehler: {e}")


if __name__ == "__main__":
    main()
