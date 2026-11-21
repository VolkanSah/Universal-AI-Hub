#!/usr/bin/env python3
"""
orbrige.py - modulare Testdatei fuer OpenRouter (Stand der Doku: Nov 2026).

Wie hfbrige.py: ein Modul (Funktion + CLI-Subcommand) pro Feature, nur mit
`requests` - kein SDK noetig.

Setup:
    pip install -U requests
    export OPENROUTER_API_KEY=sk-or-...

Module / Endpunkte (Base: https://openrouter.ai/api/v1):
    models   GET  /models  (+ /videos/models, /images/models)   Modelle filtern
    chat     POST /chat/completions   Streaming, Fallbacks, Provider-Routing,
                                      Reasoning, JSON-Schema, Tools, Plugins, Bild-Input
    resp     POST /responses          Responses API (stateless, multimodaler Input)
    embed    POST /embeddings         Text + multimodal
    rerank   POST /rerank
    image    POST /images             NEU: eigener Bild-Endpunkt (inkl. Editing)
    video    POST /videos             async: submit -> poll -> download
    tts      POST /audio/speech
    stt      POST /audio/transcriptions
    all      Smoke-Test

Beispiele:
    python orbrige.py models --modality video
    python orbrige.py chat "Hallo" --stream
    python orbrige.py chat "Hallo" --fallback anthropic/claude-sonnet-4.5 openai/gpt-5-mini
    python orbrige.py chat "Denk nach: 17*23" --reasoning high
    python orbrige.py chat "Beschreibe" --image bild.jpg
    python orbrige.py chat "JSON bitte" --schema
    python orbrige.py resp "Hello, world!"
    python orbrige.py embed "Hund" "Katze" --dimensions 256
    python orbrige.py rerank "Hauptstadt von Frankreich?" "Paris ist..." "Berlin ist..."
    python orbrige.py image "a red panda astronaut" --resolution 2K --aspect 16:9
    python orbrige.py image "Mach es Aquarell" --ref foto.jpg
    python orbrige.py video "A time-lapse of a flower blooming" --duration 4
    python orbrige.py tts "Hallo Welt" --out hallo.mp3
    python orbrige.py stt hallo.mp3
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

BASE = os.environ.get("OPENROUTER_BASE", "https://openrouter.ai/api/v1")
OUT_DIR = Path("or_out")

# Modelle aus den Doku-Beispielen; per --model oder hier aenderbar.
DEFAULTS = {
    "chat": "openai/gpt-5-mini",
    "chat_vlm": "openai/gpt-5-mini",
    "resp": "openai/o4-mini",
    "embed": "openai/text-embedding-3-small",
    "rerank": "cohere/rerank-v3.5",
    "image": "bytedance-seed/seedream-4.5",
    "video": "google/veo-3.1",
    "tts": "mistralai/voxtral-mini-tts-2603",
    "stt": "openai/whisper-large-v3",
}


# ---------------------------------------------------------------------------
# Helfer
# ---------------------------------------------------------------------------

def headers(extra: Optional[dict] = None) -> dict[str, str]:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("OPENROUTER_API_KEY fehlt. Setze: export OPENROUTER_API_KEY=sk-or-...")
    h = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # optional, fuer Rankings auf openrouter.ai
        "X-OpenRouter-Title": os.environ.get("OPENROUTER_TITLE", "orbrige-test"),
    }
    if os.environ.get("OPENROUTER_REFERER"):
        h["HTTP-Referer"] = os.environ["OPENROUTER_REFERER"]
    h.update(extra or {})
    return h


class ORError(RuntimeError):
    pass


def check(r: requests.Response) -> requests.Response:
    """Wie raise_for_status, aber mit dem OpenRouter-Fehlertext."""
    if r.ok:
        return r
    try:
        err = r.json().get("error", r.text)
    except Exception:  # noqa: BLE001
        err = r.text
    raise ORError(f"HTTP {r.status_code}: {err}")


def post(path: str, body: dict, *, timeout: int = 300, stream: bool = False) -> requests.Response:
    return check(requests.post(f"{BASE}{path}", headers=headers(), json=body, timeout=timeout, stream=stream))


def get(path: str, params: Optional[dict] = None, *, auth: bool = True, timeout: int = 60) -> requests.Response:
    return check(requests.get(f"{BASE}{path}", headers=headers() if auth else None, params=params, timeout=timeout))


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
    """URL bleibt, lokale Datei -> data:-URL (base64)."""
    if src.startswith(("http://", "https://", "data:")):
        return src
    mime = mimetypes.guess_type(src)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(Path(src).read_bytes()).decode()}"


def file_b64(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode()


def sse_events(r: requests.Response) -> Iterator[dict]:
    """Parst SSE. Kommentarzeilen (': ...') werden laut Doku ignoriert."""
    for raw in r.iter_lines(decode_unicode=True):
        if not raw or raw.startswith(":"):
            continue
        if raw.startswith("data:"):
            data = raw[5:].strip()
            if data == "[DONE]":
                return
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                continue


def json_schema_format(name: str, schema: dict, strict: bool = True) -> dict:
    return {"type": "json_schema", "json_schema": {"name": name, "strict": strict, "schema": schema}}


def demo_tool() -> list[dict]:
    return [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Aktuelles Wetter fuer eine Stadt abrufen",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }]


# ---------------------------------------------------------------------------
# Modul 1: Models (Discovery)
# ---------------------------------------------------------------------------

def list_models(
    *,
    output_modalities: Optional[str] = None,   # text(default)|image|video|audio|speech|transcription|embeddings|rerank|all
    supported_parameters: Optional[str] = None,  # z.B. "tools"
    sort: Optional[str] = None,  # pricing-low-to-high|context-high-to-low|throughput-high-to-low|latency-low-to-high|most-popular|top-weekly|newest ...
    limit: Optional[int] = None,
    offset: Optional[int] = None,
) -> list[dict]:
    """Hinweis: ohne output_modalities kommen NUR Text-Modelle zurueck."""
    params = drop_none(dict(
        output_modalities=output_modalities, supported_parameters=supported_parameters,
        sort=sort, limit=limit, offset=offset,
    ))
    return get("/models", params).json()["data"]


def list_video_models() -> list[dict]:
    """Inkl. erlaubter durations/resolutions/aspect_ratios je Modell."""
    return get("/videos/models", auth=False).json()["data"]


def list_image_models() -> list[dict]:
    return get("/images/models").json()["data"]


# ---------------------------------------------------------------------------
# Modul 2: Chat Completions
# ---------------------------------------------------------------------------

def chat_completion(
    prompt: str | None = None,
    *,
    messages: Optional[list[dict]] = None,
    system: Optional[str] = None,
    image: Optional[str] = None,                # URL oder Datei
    model: Optional[str] = None,                # Suffixe: :nitro (Speed) / :floor (Preis)
    models: Optional[list[str]] = None,         # Model-Fallback-Liste
    provider: Optional[dict] = None,            # Provider-Routing (siehe provider_prefs)
    reasoning: Optional[dict] = None,           # {"effort": "high"} ODER {"max_tokens": 2000}
    response_format: Optional[dict] = None,
    tools: Optional[list[dict]] = None,
    tool_choice: Optional[Any] = None,          # "none"|"auto"|{"type":"function",...}
    plugins: Optional[list[dict]] = None,       # web | file-parser | response-healing | context-compression
    stream: bool = False,
    max_tokens: Optional[int] = 512,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    seed: Optional[int] = None,
    stop: Optional[list[str]] = None,
    extra: Optional[dict] = None,               # beliebige weitere Felder (top_k, min_p, ...)
    raw: bool = False,                          # True -> komplettes JSON statt Message
):
    """
    stream=False -> Message-Dict (oder volles JSON bei raw=True).
    stream=True  -> Generator von Chunks (Dicts); Text via chunk['choices'][0]['delta'].
    """
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
        model=model or (None if models else DEFAULTS["chat_vlm" if image else "chat"]),
        models=models, route="fallback" if models else None,
        messages=messages, provider=provider, reasoning=reasoning,
        response_format=response_format, tools=tools, tool_choice=tool_choice,
        plugins=plugins, stream=True if stream else None, max_tokens=max_tokens,
        temperature=temperature, top_p=top_p, seed=seed, stop=stop,
    ))
    body.update(extra or {})

    if stream:
        r = post("/chat/completions", body, stream=True)
        return sse_events(r)
    data = post("/chat/completions", body).json()
    if "error" in data and not data.get("choices"):
        raise ORError(str(data["error"]))
    return data if raw else data["choices"][0]["message"]


def stream_text(chunks: Iterator[dict]) -> Iterator[tuple[str, str]]:
    """Zerlegt Chunks in ('content'|'reasoning'|'usage', text)."""
    for ch in chunks:
        if ch.get("usage"):
            yield "usage", json.dumps(ch["usage"])
        for c in ch.get("choices", []):
            d = c.get("delta") or {}
            if d.get("reasoning"):
                yield "reasoning", d["reasoning"]
            if d.get("content"):
                yield "content", d["content"]


def provider_prefs(
    *, order=None, only=None, ignore=None, allow_fallbacks=None, require_parameters=None,
    data_collection=None, zdr=None, sort=None, quantizations=None, max_price=None,
    preferred_max_latency=None, preferred_min_throughput=None, options=None,
) -> dict:
    """Baut das `provider`-Objekt (gilt fuer chat, responses, embeddings, images, ...)."""
    return drop_none(dict(
        order=order, only=only, ignore=ignore, allow_fallbacks=allow_fallbacks,
        require_parameters=require_parameters, data_collection=data_collection, zdr=zdr,
        sort=sort, quantizations=quantizations, max_price=max_price,
        preferred_max_latency=preferred_max_latency,
        preferred_min_throughput=preferred_min_throughput, options=options,
    ))


# ---------------------------------------------------------------------------
# Modul 3: Responses API (stateless)
# ---------------------------------------------------------------------------

def responses(
    input_: str | list[dict],
    *,
    model: Optional[str] = None,
    models: Optional[list[str]] = None,
    instructions: Optional[str] = None,
    image: Optional[str] = None,
    reasoning: Optional[dict] = None,
    text_format: Optional[dict] = None,   # {"type":"json_schema","name":..,"strict":True,"schema":{..}}
    tools: Optional[list[dict]] = None,
    provider: Optional[dict] = None,
    plugins: Optional[list[dict]] = None,
    max_output_tokens: Optional[int] = None,
    extra: Optional[dict] = None,
) -> dict:
    """
    WICHTIG: Stateless. Der volle Verlauf muss bei jedem Request in `input` stehen;
    store=true oder previous_response_id fuehren zu HTTP 400.
    Multimodal: input=[{"role":"user","content":[{"type":"input_text",..},{"type":"input_image",..}]}]
    (Feldnamen laut Doku-Skizze - bei Fehler im Responses-Reference pruefen.)
    """
    if image and isinstance(input_, str):
        input_ = [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": input_},
                {"type": "input_image", "image_url": to_data_url(image)},
            ],
        }]
    body = drop_none(dict(
        model=model or (None if models else DEFAULTS["resp"]), models=models, input=input_,
        instructions=instructions, reasoning=reasoning,
        text={"format": text_format} if text_format else None,
        tools=tools, provider=provider, plugins=plugins, max_output_tokens=max_output_tokens,
    ))
    body.update(extra or {})
    return post("/responses", body).json()


def responses_text(resp: dict) -> str:
    """Zieht den Text aus output-Items (best effort)."""
    if isinstance(resp.get("output_text"), str):
        return resp["output_text"]
    parts: list[str] = []
    for item in resp.get("output", []):
        for c in item.get("content", []) or []:
            if isinstance(c, dict) and c.get("type") in ("output_text", "text") and c.get("text"):
                parts.append(c["text"])
    return "".join(parts)


# ---------------------------------------------------------------------------
# Modul 4: Embeddings (Text + multimodal)
# ---------------------------------------------------------------------------

def embeddings(
    input_: str | list[str] | list[dict],
    *,
    model: Optional[str] = None,
    dimensions: Optional[int] = None,
    encoding_format: Optional[str] = None,  # float | base64
    input_type: Optional[str] = None,       # search_query | search_document
    provider: Optional[dict] = None,
    user: Optional[str] = None,
) -> dict:
    """Multimodal: input=[{"content":[{"type":"text","text":..},{"type":"image_url","image_url":{"url":..}}]}]"""
    body = drop_none(dict(
        model=model or DEFAULTS["embed"], input=input_, dimensions=dimensions,
        encoding_format=encoding_format, input_type=input_type, provider=provider, user=user,
    ))
    return post("/embeddings", body).json()


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = sum(x * x for x in a) ** 0.5, sum(y * y for y in b) ** 0.5
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# Modul 5: Rerank
# ---------------------------------------------------------------------------

def rerank(
    query: str,
    documents: list[str | dict],  # str oder {"text":..,"image":..}
    *,
    model: Optional[str] = None,
    top_n: Optional[int] = None,
    provider: Optional[dict] = None,
) -> dict:
    body = drop_none(dict(
        model=model or DEFAULTS["rerank"], query=query, documents=documents,
        top_n=top_n, provider=provider,
    ))
    return post("/rerank", body).json()


# ---------------------------------------------------------------------------
# Modul 6: Image Generation (/images - dedizierter Endpunkt)
# ---------------------------------------------------------------------------

def generate_image(
    prompt: str,
    *,
    model: Optional[str] = None,
    resolution: Optional[str] = None,     # 512|768|1K|1.5K|2K|4K
    aspect_ratio: Optional[str] = None,   # 1:1, 16:9, ..., auto
    size: Optional[str] = None,           # Tier oder "2048x2048"
    quality: Optional[str] = None,        # auto|low|medium|high
    output_format: Optional[str] = None,  # png|jpeg|webp|svg|gif
    n: Optional[int] = None,              # 1..10
    seed: Optional[int] = None,
    background: Optional[str] = None,
    output_compression: Optional[int] = None,
    references: Optional[list[str]] = None,   # Editing: URLs/Dateien
    response_format: Optional[str] = None,    # "url" -> 24h-Link statt b64_json
    provider: Optional[dict] = None,          # provider.options[slug] fuer Spezialparameter
) -> list[dict]:
    """Rueckgabe: data[]-Liste mit b64_json (+media_type) oder url."""
    body = drop_none(dict(
        model=model or DEFAULTS["image"], prompt=prompt, resolution=resolution,
        aspect_ratio=aspect_ratio, size=size, quality=quality, output_format=output_format,
        n=n, seed=seed, background=background, output_compression=output_compression,
        response_format=response_format, provider=provider,
        input_references=[
            {"type": "image_url", "image_url": {"url": to_data_url(x)}} for x in references
        ] if references else None,
    ))
    return post("/images", body).json()["data"]


def save_images(data: list[dict], stem: str = "image") -> list[Path]:
    paths = []
    for i, img in enumerate(data):
        mt = img.get("media_type", "image/png")
        ext = mimetypes.guess_extension(mt) or ".png"
        p = out_path(f"{stem}_{i}{ext.replace('.jpe', '.jpg')}")
        if img.get("b64_json"):
            p.write_bytes(base64.b64decode(img["b64_json"]))
        elif img.get("url"):
            p.write_bytes(requests.get(img["url"], timeout=120).content)
        paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# Modul 7: Video Generation (async)
# ---------------------------------------------------------------------------

def video_submit(
    prompt: str,
    *,
    model: Optional[str] = None,
    duration: Optional[int] = None,
    resolution: Optional[str] = None,      # 480p..1080p, 1K, 2K, 4K
    aspect_ratio: Optional[str] = None,
    size: Optional[str] = None,            # "WIDTHxHEIGHT" (alternativ zu resolution+aspect)
    first_frame: Optional[str] = None,     # Image-to-Video
    last_frame: Optional[str] = None,
    input_references: Optional[list[str]] = None,
    generate_audio: Optional[bool] = None,
    seed: Optional[int] = None,
    callback_url: Optional[str] = None,
    provider: Optional[dict] = None,
) -> dict:
    frames = []
    if first_frame:
        frames.append({"type": "image_url", "image_url": {"url": to_data_url(first_frame)}, "frame_type": "first_frame"})
    if last_frame:
        frames.append({"type": "image_url", "image_url": {"url": to_data_url(last_frame)}, "frame_type": "last_frame"})
    body = drop_none(dict(
        model=model or DEFAULTS["video"], prompt=prompt, duration=duration, resolution=resolution,
        aspect_ratio=aspect_ratio, size=size, frame_images=frames or None,
        input_references=[{"type": "image_url", "image_url": {"url": to_data_url(x)}} for x in input_references]
        if input_references else None,
        generate_audio=generate_audio, seed=seed, callback_url=callback_url, provider=provider,
    ))
    return post("/videos", body).json()  # 202: id, polling_url, status


def video_poll(job_id: str, *, interval: int = 15, timeout: int = 900, on_status: Optional[Callable] = None) -> dict:
    deadline = time.time() + timeout
    while True:
        job = get(f"/videos/{job_id}").json()
        if on_status:
            on_status(job["status"])
        if job["status"] == "completed":
            return job
        if job["status"] not in ("pending", "in_progress"):
            raise ORError(f"Video-Job {job_id}: {job['status']} - {job.get('error')}")
        if time.time() > deadline:
            raise TimeoutError(f"Video-Job {job_id} nicht fertig nach {timeout}s")
        time.sleep(interval)


def video_download(job_id: str, out: str | Path, index: int = 0) -> Path:
    r = check(requests.get(f"{BASE}/videos/{job_id}/content", params={"index": index}, headers=headers(), timeout=300))
    p = out_path(out)
    p.write_bytes(r.content)
    return p


def generate_video(prompt: str, out: str = "video.mp4", **kw) -> tuple[Path, dict]:
    """Submit -> Poll -> Download in einem Rutsch."""
    job = video_submit(prompt, **kw)
    done = video_poll(job["id"], on_status=lambda s: print(f"  status: {s}", flush=True))
    return video_download(done["id"], out), done.get("usage", {})


# ---------------------------------------------------------------------------
# Modul 8: Text-to-Speech
# ---------------------------------------------------------------------------

def text_to_speech(
    text: str | list[dict],          # String oder Turns [{"text":..,"voice":..,"instructions":..}]
    *,
    model: Optional[str] = None,
    voice: Optional[str] = None,
    response_format: Optional[str] = None,   # mp3 | pcm (Default pcm, 16-bit LE)
    speed: Optional[float] = None,           # nur manche Modelle
    instructions: Optional[str] = None,
    provider: Optional[dict] = None,
) -> bytes:
    body = drop_none(dict(
        model=model or DEFAULTS["tts"], input=text, voice=voice,
        response_format=response_format, speed=speed, instructions=instructions, provider=provider,
    ))
    return post("/audio/speech", body).content


# ---------------------------------------------------------------------------
# Modul 9: Speech-to-Text
# ---------------------------------------------------------------------------

def speech_to_text(
    audio_path: str,
    *,
    model: Optional[str] = None,
    language: Optional[str] = None,      # ISO-639-1
    temperature: Optional[float] = None,
    response_format: Optional[str] = None,  # json | verbose_json
    provider: Optional[dict] = None,
) -> dict:
    fmt = Path(audio_path).suffix.lstrip(".").lower() or "wav"
    body = drop_none(dict(
        model=model or DEFAULTS["stt"],
        input_audio={"data": file_b64(audio_path), "format": fmt},  # rohes base64, KEIN data:-URI
        language=language, temperature=temperature, response_format=response_format, provider=provider,
    ))
    return post("/audio/transcriptions", body).json()


# ---------------------------------------------------------------------------
# CLI-Handler
# ---------------------------------------------------------------------------

def _prov(a: argparse.Namespace) -> Optional[dict]:
    p = provider_prefs(
        order=a.order, only=a.only, ignore=a.ignore,
        sort=a.sort, require_parameters=True if a.require_params else None,
        data_collection="deny" if a.no_collect else None, zdr=True if a.zdr else None,
        allow_fallbacks=False if a.no_fallbacks else None,
    ) if hasattr(a, "order") else {}
    return p or None


def cmd_models(a: argparse.Namespace) -> None:
    if a.modality == "video":
        models = list_video_models()
    elif a.modality == "image":
        models = list_image_models()
    else:
        models = list_models(output_modalities=a.modality, supported_parameters=a.supports, sort=a.sort, limit=a.limit)
    print(f"{len(models)} Modelle")
    for m in models[: a.show]:
        price = (m.get("pricing") or {})
        print(f"{m.get('id', '?'):<55} ctx={m.get('context_length', '-'):<8} in={price.get('prompt', '-')} out={price.get('completion', '-')}")


def cmd_chat(a: argparse.Namespace) -> None:
    reasoning = {"effort": a.reasoning} if a.reasoning else None
    rf = None
    if a.schema:
        rf = json_schema_format("answer", {
            "type": "object",
            "properties": {"answer": {"type": "string"}, "confidence": {"type": "number"}},
            "required": ["answer", "confidence"], "additionalProperties": False,
        })
    elif a.json:
        rf = {"type": "json_object"}
    plugins = [{"id": "response-healing"}] if (a.schema and not a.stream) else None
    if a.web:
        plugins = (plugins or []) + [{"id": "web"}]
    tools = demo_tool() if a.tools else None
    res = chat_completion(
        a.prompt, system=a.system, image=a.image, model=a.model, models=a.fallback,
        provider=_prov(a), reasoning=reasoning, response_format=rf, tools=tools,
        tool_choice="auto" if tools else None, plugins=plugins, stream=a.stream,
        max_tokens=a.max_tokens, temperature=a.temperature, raw=True if not a.stream else False,
    )
    if a.stream:
        for kind, txt in stream_text(res):
            if kind == "usage":
                print(f"\n[usage] {txt}")
            elif kind == "reasoning":
                print(f"\033[2m{txt}\033[0m", end="", flush=True)
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
    print(f"\n[model={res.get('model')} usage={res.get('usage')}]")


def cmd_resp(a: argparse.Namespace) -> None:
    reasoning = {"effort": a.reasoning} if a.reasoning else None
    r = responses(a.prompt, model=a.model, instructions=a.system, image=a.image, reasoning=reasoning,
                  provider=_prov(a), models=a.fallback)
    print(responses_text(r) or json.dumps(r, indent=2, ensure_ascii=False)[:2000])
    print(f"\n[usage={r.get('usage')}]")


def cmd_embed(a: argparse.Namespace) -> None:
    r = embeddings(a.texts if len(a.texts) > 1 else a.texts[0], model=a.model, dimensions=a.dimensions,
                   input_type=a.input_type, provider=_prov(a))
    vecs = [d["embedding"] for d in r["data"]]
    print(f"{len(vecs)} Vektoren, dim={len(vecs[0])}, usage={r.get('usage')}")
    if len(vecs) >= 2:
        print(f"cosine(0,1) = {cosine(vecs[0], vecs[1]):.4f}")


def cmd_rerank(a: argparse.Namespace) -> None:
    r = rerank(a.query, a.documents, model=a.model, top_n=a.top_n)
    for res in r["results"]:
        print(f"{res['relevance_score']:.4f}  [{res['index']}] {res['document'].get('text', '')[:80]}")


def cmd_image(a: argparse.Namespace) -> None:
    data = generate_image(a.prompt, model=a.model, resolution=a.resolution, aspect_ratio=a.aspect,
                          quality=a.quality, output_format=a.format, n=a.n, seed=a.seed,
                          references=a.ref, provider=_prov(a))
    for p in save_images(data, Path(a.out).stem):
        print("gespeichert:", p)


def cmd_video(a: argparse.Namespace) -> None:
    p, usage = generate_video(a.prompt, a.out, model=a.model, duration=a.duration, resolution=a.resolution,
                              aspect_ratio=a.aspect, first_frame=a.first_frame, last_frame=a.last_frame,
                              generate_audio=False if a.no_audio else None, seed=a.seed)
    print("gespeichert:", p, "| usage:", usage)


def cmd_tts(a: argparse.Namespace) -> None:
    fmt = "mp3" if a.out.endswith(".mp3") else "pcm"
    audio = text_to_speech(a.text, model=a.model, voice=a.voice, response_format=fmt, speed=a.speed)
    p = out_path(a.out)
    p.write_bytes(audio)
    print("gespeichert:", p, f"({len(audio)} Bytes, {fmt})")


def cmd_stt(a: argparse.Namespace) -> None:
    r = speech_to_text(a.audio, model=a.model, language=a.language)
    print(r.get("text"))
    print("usage:", r.get("usage"))


def cmd_all(a: argparse.Namespace) -> None:
    tests: list[tuple[str, Callable[[], Any]]] = [
        ("models", lambda: len(list_models(limit=5))),
        ("chat", lambda: chat_completion("Sag 'ok'.", max_tokens=20)["content"]),
        ("chat-stream", lambda: "".join(t for k, t in stream_text(chat_completion("Zaehle bis 3.", stream=True, max_tokens=30)) if k == "content")),
        ("chat-fallback", lambda: chat_completion("Sag 'ok'.", models=[DEFAULTS["chat"], "openai/gpt-4o-mini"], max_tokens=20)["content"]),
        ("responses", lambda: responses_text(responses("Sag 'ok'."))[:40]),
        ("embed", lambda: len(embeddings(["Hallo", "Hi"])["data"])),
        ("rerank", lambda: rerank("Hauptstadt Frankreich?", ["Paris", "Berlin"])["results"][0]["index"]),
    ]
    if a.media:
        tests += [
            ("image", lambda: save_images(generate_image("a red apple", resolution="1K"), "smoke_image")),
            ("tts", lambda: len(text_to_speech("Hallo", response_format="mp3"))),
        ]
    if a.video:
        tests.append(("video", lambda: generate_video("a cat walking", "smoke_video.mp4", duration=4)[0]))
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


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="OpenRouter - modulare Tests")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser, routing: bool = True) -> None:
        sp.add_argument("--model")
        if routing:
            sp.add_argument("--order", nargs="+", help="provider.order")
            sp.add_argument("--only", nargs="+", help="provider.only")
            sp.add_argument("--ignore", nargs="+", help="provider.ignore")
            sp.add_argument("--sort", choices=["price", "throughput", "latency"])
            sp.add_argument("--require-params", action="store_true")
            sp.add_argument("--no-collect", action="store_true", help="data_collection=deny")
            sp.add_argument("--zdr", action="store_true")
            sp.add_argument("--no-fallbacks", action="store_true")

    sp = sub.add_parser("models"); sp.set_defaults(func=cmd_models)
    sp.add_argument("--modality", help="text|image|video|audio|speech|transcription|embeddings|rerank|all")
    sp.add_argument("--supports", help="supported_parameters, z.B. tools")
    sp.add_argument("--sort", default="top-weekly")
    sp.add_argument("--limit", type=int)
    sp.add_argument("--show", type=int, default=25)

    sp = sub.add_parser("chat"); sp.set_defaults(func=cmd_chat)
    sp.add_argument("prompt"); sp.add_argument("--system"); sp.add_argument("--image")
    sp.add_argument("--stream", action="store_true")
    sp.add_argument("--fallback", nargs="+", help="models[] Fallback-Liste")
    sp.add_argument("--reasoning", help="effort: max/xhigh/high/medium/low/minimal/none")
    sp.add_argument("--json", action="store_true"); sp.add_argument("--schema", action="store_true")
    sp.add_argument("--tools", action="store_true"); sp.add_argument("--web", action="store_true", help="plugin web")
    sp.add_argument("--max-tokens", type=int, default=512); sp.add_argument("--temperature", type=float)
    common(sp)

    sp = sub.add_parser("resp"); sp.set_defaults(func=cmd_resp)
    sp.add_argument("prompt"); sp.add_argument("--system"); sp.add_argument("--image")
    sp.add_argument("--fallback", nargs="+"); sp.add_argument("--reasoning")
    common(sp)

    sp = sub.add_parser("embed"); sp.set_defaults(func=cmd_embed)
    sp.add_argument("texts", nargs="+"); sp.add_argument("--dimensions", type=int)
    sp.add_argument("--input-type", choices=["search_query", "search_document"])
    common(sp)

    sp = sub.add_parser("rerank"); sp.set_defaults(func=cmd_rerank)
    sp.add_argument("query"); sp.add_argument("documents", nargs="+"); sp.add_argument("--top-n", type=int)
    common(sp, routing=False)

    sp = sub.add_parser("image"); sp.set_defaults(func=cmd_image)
    sp.add_argument("prompt"); sp.add_argument("--out", default="image.png")
    sp.add_argument("--resolution"); sp.add_argument("--aspect"); sp.add_argument("--quality")
    sp.add_argument("--format"); sp.add_argument("--n", type=int); sp.add_argument("--seed", type=int)
    sp.add_argument("--ref", nargs="+", help="Referenzbilder (Editing)")
    common(sp)

    sp = sub.add_parser("video"); sp.set_defaults(func=cmd_video)
    sp.add_argument("prompt"); sp.add_argument("--out", default="video.mp4")
    sp.add_argument("--duration", type=int); sp.add_argument("--resolution"); sp.add_argument("--aspect")
    sp.add_argument("--first-frame"); sp.add_argument("--last-frame")
    sp.add_argument("--no-audio", action="store_true"); sp.add_argument("--seed", type=int)
    common(sp, routing=False)

    sp = sub.add_parser("tts"); sp.set_defaults(func=cmd_tts)
    sp.add_argument("text"); sp.add_argument("--out", default="speech.mp3")
    sp.add_argument("--voice"); sp.add_argument("--speed", type=float)
    common(sp, routing=False)

    sp = sub.add_parser("stt"); sp.set_defaults(func=cmd_stt)
    sp.add_argument("audio"); sp.add_argument("--language")
    common(sp, routing=False)

    sp = sub.add_parser("all", help="Smoke-Test"); sp.set_defaults(func=cmd_all)
    sp.add_argument("--media", action="store_true", help="auch image + tts (kostet Credits)")
    sp.add_argument("--video", action="store_true", help="auch video (langsam, teurer)")
    sp.add_argument("--verbose", action="store_true")
    return p


def main() -> None:
    a = build_parser().parse_args()
    try:
        a.func(a)
    except ORError as e:
        sys.exit(f"Fehler: {e}")


if __name__ == "__main__":
    main()
