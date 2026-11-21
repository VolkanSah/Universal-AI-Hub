#!/usr/bin/env python3
"""
gembrige.py - Lern- und Testdatei fuer die Gemini API (Doku-Stand: Nov 2026).

ZIEL: Jedes Feature ist EINE kleine Funktion, die du einzeln lesen, aufrufen und
verstehen kannst. Die CLI ganz unten ist nur eine duenne Huelle darum.

------------------------------------------------------------------------------
LERNHINWEISE - was passiert hier eigentlich?
------------------------------------------------------------------------------
1. Eine "API" ist hier nur eine URL, an die wir per HTTP einen JSON-Text
   schicken (POST) und einen JSON-Text zurueckbekommen. Das Modul `requests`
   macht das: requests.post(url, headers=..., json=...).
2. Der API-Key steht im Header `x-goog-api-key` (bei Gemini) - nie im Code,
   sondern in einer Umgebungsvariable: export GEMINI_API_KEY=...
3. NEU bei Gemini: Die Doku nutzt jetzt die "Interactions API"
   (POST /v1beta/interactions) statt generateContent. Statt `candidates[]`
   bekommst du eine Liste von `steps` zurueck. Jeder Step hat einen `type`:
     model_output  -> die eigentliche Antwort (Text, Bild, Audio, Video)
     thought       -> Denk-Schritt (Zusammenfassung)
     function_call -> das Modell will DEINE Funktion aufrufen
4. Binaerdaten (Bild/Audio/Video) werden in JSON als base64-Text uebertragen.
   base64.b64decode(...) macht daraus wieder Bytes, die wir in eine Datei schreiben.
5. Streaming = Server-Sent Events (SSE): Die Antwort kommt in vielen kleinen
   Zeilen "data: {...}" statt auf einmal. Wir lesen Zeile fuer Zeile.
6. Lange Aufgaben (Video) sind "asynchron": abschicken -> Status abfragen
   (Polling) -> Ergebnis holen.

------------------------------------------------------------------------------
MODULE
------------------------------------------------------------------------------
    models   GET  /models
    chat     POST /interactions      Text, Stream, Thinking, JSON-Schema, Tools, Vision, Search
    legacy   POST /models/X:generateContent   (altes Format - zum Vergleichen)
    image    POST /interactions      response_format.type = "image"
    video    POST /interactions      gemini-omni, response_format.type = "video"
    tts      POST /interactions      response_format.type = "audio"
    stt      POST /interactions      gemini-3.5-transcribe (+ Files API)
    embed    POST /models/X:embedContent | :batchEmbedContents
    all      Smoke-Test

Setup:
    pip install requests
    export GEMINI_API_KEY=...

Beispiele:
    python gembrige.py chat "Erklaer Rekursion in 2 Saetzen"
    python gembrige.py chat "Beweise: sqrt(2) ist irrational" --thinking high --show-thoughts
    python gembrige.py chat "Was ist auf dem Bild?" --image foto.jpg
    python gembrige.py chat "Aktuelle Tech-News?" --search
    python gembrige.py chat "Wetter in Berlin?" --tools
    python gembrige.py chat "Rezept fuer Pfannkuchen" --schema
    python gembrige.py image "Ein Pandabaer als Astronaut" --aspect 16:9 --size 2K
    python gembrige.py video "Regnerische Neonstrasse bei Nacht" --aspect 9:16
    python gembrige.py tts "Hab einen wundervollen Tag!" --voice Kore --style "froehlich"
    python gembrige.py stt aufnahme.mp3 --speakers --words
    python gembrige.py embed "Hund" "Katze" --dim 768

WICHTIG - Unsicherheiten (aus der Doku nicht eindeutig):
  * Die Doku-Seiten nennen /v1beta/interactions, die API-Referenz /v1/interactions.
    -> BASE ist per Umgebungsvariable GEMINI_BASE umstellbar.
  * `system_instruction` und `temperature` sind fuer Modell-Requests in der Doku
    nicht klar beschrieben. Wir senden system_instruction top-level; bei HTTP 400
    nimm es raus und schreib die Anweisung in den Prompt.
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

BASE = os.environ.get("GEMINI_BASE", "https://generativelanguage.googleapis.com/v1beta")
UPLOAD_BASE = BASE.replace("googleapis.com/", "googleapis.com/upload/", 1)  # Files-Upload hat eigenen Pfad
OUT_DIR = Path("gem_out")

DEFAULTS = {
    "chat": "gemini-3.8-flash",          # Stable, schnell
    "pro": "gemini-3.1-pro-preview",     # staerker, Preview
    "image": "gemini-nano-banana-2.1",
    "video": "gemini-omni-1.1-flash",    # ersetzt Veo 3.1 (am 22.10.2026 abgeschaltet)
    "tts": "gemini-3.8-flash-tts",
    "stt": "gemini-3.5-transcribe",
    "embed": "gemini-embedding-2",
}


# ---------------------------------------------------------------------------
# Helfer (die "Werkzeugkiste")
# ---------------------------------------------------------------------------

class GemError(RuntimeError):
    """Eigene Fehlerklasse, damit die CLI saubere Meldungen statt Tracebacks zeigt."""


def headers(json_body: bool = True, extra: Optional[dict] = None) -> dict[str, str]:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY fehlt. Setze: export GEMINI_API_KEY=...")
    h = {"x-goog-api-key": key}
    if json_body:
        h["Content-Type"] = "application/json"
    h.update(extra or {})
    return h


def check(r: requests.Response) -> requests.Response:
    """HTTP-Fehler (4xx/5xx) in lesbaren Text verwandeln. Gemini liefert {"error": {"message": ...}}."""
    if r.ok:
        return r
    try:
        err = r.json().get("error", r.text)
        if isinstance(err, dict):
            err = err.get("message", err)
    except Exception:  # noqa: BLE001
        err = r.text
    raise GemError(f"HTTP {r.status_code}: {err}")


def post(path: str, body: dict, *, timeout: int = 600, stream: bool = False) -> requests.Response:
    return check(requests.post(f"{BASE}{path}", headers=headers(), json=body, timeout=timeout, stream=stream))


def get(path: str, params: Optional[dict] = None, timeout: int = 60) -> requests.Response:
    return check(requests.get(f"{BASE}{path}", headers=headers(False), params=params, timeout=timeout))


def drop_none(d: dict) -> dict:
    """Entfernt alle None-Werte, damit wir nur Felder senden, die wirklich gesetzt sind."""
    return {k: v for k, v in d.items() if v is not None}


def out_path(name: str | Path) -> Path:
    p = Path(name)
    if not p.is_absolute() and p.parent == Path("."):
        OUT_DIR.mkdir(exist_ok=True)
        p = OUT_DIR / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def b64_file(path: str) -> str:
    """Datei lesen -> base64-Text (so schickt man Binaerdaten in JSON)."""
    return base64.b64encode(Path(path).read_bytes()).decode()


def mime_of(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def sse_events(r: requests.Response) -> Iterator[dict]:
    """
    Server-Sent Events lesen. Eine SSE-Antwort sieht so aus:
        event: step.delta
        data: {"event_type": "step.delta", "delta": {...}}
        <Leerzeile>
    Wir interessieren uns nur fuer die `data:`-Zeilen und parsen sie als JSON.
    """
    for raw in r.iter_lines(decode_unicode=True):
        if not raw or raw.startswith(":") or not raw.startswith("data:"):
            continue
        payload = raw[5:].strip()
        if payload == "[DONE]":
            return
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue


# ---------------------------------------------------------------------------
# Antwort-Auswertung: aus `steps` die Teile herausholen
# ---------------------------------------------------------------------------

def iter_content(interaction: dict, content_type: Optional[str] = None) -> Iterator[dict]:
    """Geht alle model_output-Steps durch und liefert deren content-Eintraege (optional gefiltert)."""
    for step in interaction.get("steps", []):
        if step.get("type") != "model_output":
            continue
        for c in step.get("content", []) or []:
            if content_type is None or c.get("type") == content_type:
                yield c


def output_text(interaction: dict) -> str:
    """Gesamte Textantwort (REST hat kein `output_text` - das gibt es nur im SDK)."""
    return "".join(c.get("text", "") for c in iter_content(interaction, "text"))


def thought_summaries(interaction: dict) -> list[str]:
    """thought-Steps enthalten `summary`: Liste von Text-/Bild-Inhalten (kann leer sein)."""
    out = []
    for step in interaction.get("steps", []):
        if step.get("type") == "thought":
            for s in step.get("summary") or []:
                if isinstance(s, dict) and s.get("text"):
                    out.append(s["text"])
    return out


def function_calls(interaction: dict) -> list[dict]:
    return [s for s in interaction.get("steps", []) if s.get("type") == "function_call"]


def first_media(interaction: dict, kind: str) -> Optional[dict]:
    """Letztes content-Element vom Typ image|audio|video (wie das jq-Beispiel der Doku)."""
    items = list(iter_content(interaction, kind))
    return items[-1] if items else None


# ---------------------------------------------------------------------------
# Modul 1: Models
# ---------------------------------------------------------------------------

def list_models(page_size: int = 200) -> list[dict]:
    """GET /models liefert {"models": [...], "nextPageToken": ...} - hier eine Seite."""
    return get("/models", {"pageSize": page_size}).json().get("models", [])


# ---------------------------------------------------------------------------
# Modul 2: Chat ueber die Interactions API
# ---------------------------------------------------------------------------

def make_input(prompt: str, *, images: Optional[list[str]] = None,
               audio: Optional[str] = None, video: Optional[str] = None) -> str | list[dict]:
    """
    `input` darf ein einfacher String ODER eine Liste von Content-Teilen sein.
    Multimodal = mehrere Teile: {"type":"text",...}, {"type":"image","mime_type":...,"data": <base64>}.
    """
    if not (images or audio or video):
        return prompt
    parts: list[dict] = [{"type": "text", "text": prompt}]
    for path in images or []:
        parts.append({"type": "image", "mime_type": mime_of(path), "data": b64_file(path)})
    if audio:
        parts.append({"type": "audio", "mime_type": mime_of(audio), "data": b64_file(audio)})
    if video:
        parts.append({"type": "video", "mime_type": mime_of(video), "data": b64_file(video)})
    return parts


def json_response_format(schema: dict) -> dict:
    """Structured Output: Modell MUSS JSON passend zum Schema liefern."""
    return {"type": "text", "mime_type": "application/json", "schema": schema}


def function_tool(name: str, description: str, parameters: dict) -> dict:
    """Flaches Tool-Format der Interactions API (kein `functionDeclarations`-Wrapper)."""
    return {"type": "function", "name": name, "description": description, "parameters": parameters}


GOOGLE_SEARCH = {"type": "google_search"}   # Built-in: Websuche (Grounding)
URL_CONTEXT = {"type": "url_context"}       # Built-in: URLs im Prompt automatisch lesen


def interact(
    input_: str | list[dict],
    *,
    model: Optional[str] = None,
    system_instruction: Optional[str] = None,      # siehe Unsicherheiten oben
    thinking_level: Optional[str] = None,          # minimal | low | medium | high (je nach Modell)
    thinking_summaries: Optional[str] = None,      # auto | none -> Denk-Zusammenfassungen anfordern
    max_output_tokens: Optional[int] = None,       # Thinking-Tokens zaehlen mit!
    seed: Optional[int] = None,
    stop_sequences: Optional[list[str]] = None,
    tool_choice: Optional[Any] = None,             # auto | any | none | validated
    tools: Optional[list[dict]] = None,
    response_format: Optional[dict | list[dict]] = None,
    previous_interaction_id: Optional[str] = None, # Gespraech serverseitig fortsetzen
    store: Optional[bool] = None,                  # False = Server merkt sich nichts (dann Verlauf selbst senden)
    service_tier: Optional[str] = None,            # flex | standard | priority
    generation_config_extra: Optional[dict] = None,
    extra: Optional[dict] = None,                  # Escape-Hatch fuer beliebige Zusatzfelder
    stream: bool = False,
):
    """
    Die Kernfunktion. Alles andere (chat, image, tts ...) baut nur andere Bodies
    und ruft diese Funktion auf.

    stream=False -> komplettes Interaction-Dict
    stream=True  -> Generator von SSE-Events (Dicts)
    """
    gen_cfg = drop_none(dict(
        thinking_level=thinking_level, thinking_summaries=thinking_summaries,
        max_output_tokens=max_output_tokens, seed=seed, stop_sequences=stop_sequences,
        tool_choice=tool_choice,
    ))
    gen_cfg.update(generation_config_extra or {})

    body = drop_none(dict(
        model=model or DEFAULTS["chat"], input=input_, system_instruction=system_instruction,
        generation_config=gen_cfg or None, tools=tools, response_format=response_format,
        previous_interaction_id=previous_interaction_id, store=store, service_tier=service_tier,
        stream=True if stream else None,
    ))
    body.update(extra or {})

    r = post("/interactions", body, stream=stream)
    return sse_events(r) if stream else r.json()


def stream_text(events: Iterator[dict]) -> Iterator[tuple[str, str]]:
    """
    Streaming-Events in (art, text) uebersetzen. Laut Doku: Events heissen
    interaction.created / step.start / step.delta / step.stop / interaction.completed.
    Text steckt in step.delta mit delta.type == "text". Alles andere geben wir
    best effort als 'other' aus, damit du beim Lernen siehst, was ankommt.
    """
    for ev in events:
        et = ev.get("event_type") or ev.get("type")
        if et == "step.delta":
            d = ev.get("delta") or {}
            if d.get("type") == "text" and d.get("text"):
                yield "text", d["text"]
            elif "thought" in str(d.get("type", "")) and (d.get("text") or d.get("summary")):
                yield "thought", d.get("text") or json.dumps(d.get("summary"), ensure_ascii=False)
            else:
                yield "other", json.dumps(d, ensure_ascii=False)
        elif et == "error":
            raise GemError(str(ev.get("error", ev)))
        elif ev.get("usage"):
            yield "usage", json.dumps(ev["usage"])


def run_tool_loop(prompt: str, fns: dict[str, Callable[..., Any]], tools: list[dict],
                  *, model: Optional[str] = None, max_rounds: int = 5, verbose: bool = True) -> str:
    """
    Function Calling Schritt fuer Schritt - das Herzstueck von "Agenten":
      1. Wir schicken Prompt + Tool-Beschreibungen.
      2. Das Modell antwortet NICHT mit Text, sondern mit einem `function_call`-Step
         (name + arguments). Das Modell fuehrt NICHTS selbst aus!
      3. WIR fuehren die Python-Funktion aus.
      4. Wir schicken das Ergebnis als `function_result`-Step zurueck
         (verknuepft per call_id + previous_interaction_id).
      5. Wiederholen, bis das Modell Text liefert.
    """
    inter = interact(prompt, model=model, tools=tools)
    for _ in range(max_rounds):
        calls = function_calls(inter)
        if not calls:
            return output_text(inter)
        results = []
        for c in calls:
            args = c.get("arguments") or {}
            if isinstance(args, str):
                args = json.loads(args or "{}")
            if verbose:
                print(f"  -> Modell ruft {c['name']}({args})")
            result = fns[c["name"]](**args)
            results.append({
                "type": "function_result", "name": c["name"], "call_id": c["id"],
                "result": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
            })
        # Tools muessen laut Doku-Beispiel in jeder Runde erneut mitgeschickt werden.
        inter = interact(results, model=model, tools=tools, previous_interaction_id=inter["id"])
    return "(max_rounds erreicht)"


# ---------------------------------------------------------------------------
# Modul 3: legacy generateContent (zum Vergleichen)
# ---------------------------------------------------------------------------

def generate_content(prompt: str, *, model: Optional[str] = None) -> dict:
    """
    Das ALTE Format: contents -> parts -> text; Antwort in candidates[].content.parts[].
    Die Doku zeigt nur noch dieses Minimal-Beispiel. Vergleiche mit `interact` oben:
    gleiches Ziel, andere Verpackung.
    """
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    return post(f"/models/{model or DEFAULTS['chat']}:generateContent", body).json()


def legacy_text(resp: dict) -> str:
    out = []
    for cand in resp.get("candidates", []):
        for part in (cand.get("content") or {}).get("parts", []):
            if part.get("text"):
                out.append(part["text"])
    return "".join(out)


# ---------------------------------------------------------------------------
# Modul 4: Bildgenerierung (Nano Banana)
# ---------------------------------------------------------------------------

def generate_image(
    prompt: str,
    *,
    model: Optional[str] = None,
    edit_images: Optional[list[str]] = None,   # Bild-Editing: Eingabebilder mitschicken
    aspect_ratio: Optional[str] = None,        # "1:1", "16:9", ...
    image_size: Optional[str] = None,          # "1K" | "2K" | "4K" (grosses K!)
    mime_type: Optional[str] = None,           # "image/png" | "image/jpeg"
    thinking_level: Optional[str] = None,
    search: bool = False,                      # Grounding mit Google Search
    previous_interaction_id: Optional[str] = None,  # mehrstufiges Editieren
) -> tuple[Optional[bytes], dict]:
    """Rueckgabe: (Bild-Bytes, komplette Interaction). Bild steckt base64 in content.data."""
    parts: list[dict] = [{"type": "text", "text": prompt}]
    for p in edit_images or []:
        parts.append({"type": "image", "mime_type": mime_of(p), "data": b64_file(p)})
    inter = interact(
        parts, model=model or DEFAULTS["image"], thinking_level=thinking_level,
        tools=[GOOGLE_SEARCH] if search else None,
        response_format=drop_none({"type": "image", "aspect_ratio": aspect_ratio,
                                   "image_size": image_size, "mime_type": mime_type}),
        previous_interaction_id=previous_interaction_id,
    )
    img = first_media(inter, "image")
    return (base64.b64decode(img["data"]) if img and img.get("data") else None), inter


# ---------------------------------------------------------------------------
# Modul 5: Video (Gemini Omni Flash)
# ---------------------------------------------------------------------------

def file_id_from_uri(uri: str) -> str:
    """'https://.../v1beta/files/abc123' -> 'abc123'"""
    return uri.rstrip("/").split("/files/")[-1].split(":")[0]


def wait_file_active(file_id: str, *, interval: int = 5, timeout: int = 600) -> dict:
    """Polling: Datei-Status abfragen, bis state == ACTIVE (FAILED -> Fehler)."""
    deadline = time.time() + timeout
    while True:
        f = get(f"/files/{file_id}").json()
        state = f.get("state")
        if state == "ACTIVE":
            return f
        if state == "FAILED":
            raise GemError(f"Datei {file_id} FAILED: {f.get('error')}")
        if time.time() > deadline:
            raise TimeoutError(f"Datei {file_id} nicht ACTIVE nach {timeout}s")
        time.sleep(interval)


def download_file(file_id: str) -> bytes:
    """GET /files/{id}:download?alt=media liefert die rohen Bytes."""
    return check(requests.get(f"{BASE}/files/{file_id}:download", params={"alt": "media"},
                              headers=headers(False), timeout=600)).content


def generate_video(
    prompt: str,
    *,
    model: Optional[str] = None,
    aspect_ratio: Optional[str] = None,    # "16:9" (Default) | "9:16"
    resolution: Optional[str] = None,      # 360p | 720p (Default) | 1080p | 4k (hochskaliert)
    delivery: str = "uri",                 # "uri" empfohlen ab ~4 MB; sonst base64 inline
    reference_images: Optional[list[str]] = None,  # image-to-video / reference-to-video
    previous_interaction_id: Optional[str] = None, # Video bearbeiten/verlaengern (+10s pro Runde, max 40s)
    task: Optional[str] = None,            # text_to_video|image_to_video|reference_to_video|edit|extend
) -> tuple[bytes, dict]:
    """
    Ablauf (siehe Lernhinweis 6):
      1. Ein POST - die Doku-Beispiele sind synchron und liefern das fertige Video.
      2. delivery="uri": Video liegt als Datei bei Google -> warten bis ACTIVE -> herunterladen.
         delivery="inline": Video kommt direkt als base64 in der Antwort.
    """
    parts: list[dict] = [{"type": "text", "text": prompt}]
    for p in reference_images or []:
        parts.append({"type": "image", "mime_type": mime_of(p), "data": b64_file(p)})
    inter = interact(
        parts if reference_images else prompt, model=model or DEFAULTS["video"],
        response_format=drop_none({"type": "video", "aspect_ratio": aspect_ratio,
                                   "resolution": resolution,
                                   "delivery": "uri" if delivery == "uri" else None}),
        generation_config_extra={"video_config": {"task": task}} if task else None,
        previous_interaction_id=previous_interaction_id,
    )
    vid = first_media(inter, "video")
    if not vid:
        raise GemError(f"Kein Video in der Antwort (status={inter.get('status')}): {json.dumps(inter)[:400]}")
    if vid.get("data"):
        return base64.b64decode(vid["data"]), inter
    fid = file_id_from_uri(vid["uri"])
    wait_file_active(fid)
    return download_file(fid), inter


# ---------------------------------------------------------------------------
# Modul 6: Text-to-Speech
# ---------------------------------------------------------------------------

VOICES = ["Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", "Callirrhoe",
          "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome", "Algenib",
          "Rasalgethi", "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima",
          "Achird", "Zubenelgenubi", "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat"]


def text_to_speech(
    text: str,
    *,
    model: Optional[str] = None,
    voice: str = "Kore",
    style: Optional[str] = None,                 # "cheerful and friendly" -> speech_metadata.style
    speakers: Optional[dict[str, str]] = None,   # Multi-Speaker: {"Joe": "Puck", "Jane": "Kore"} (max 2)
    turns: Optional[list[tuple[str, str]]] = None,  # [(speaker, text), ...] fuer Multi-Speaker
    sample_rate: Optional[int] = None,
    mime_type: Optional[str] = None,             # audio/wav (Default) | audio/l16 | audio/mulaw | audio/alaw
) -> tuple[bytes, dict]:
    """
    Wie die Doku: input = [{"type":"user_input","content":[...]}]. Stil/Sprecher haengen als
    `annotations` (type speech_metadata) am Text. Rueckgabe: WAV-Bytes (24 kHz, mono, 16 bit).
    """
    def item(t: str, speaker: Optional[str] = None) -> dict:
        meta = drop_none({"type": "speech_metadata", "style": style, "speaker": speaker})
        d: dict = {"type": "text", "text": t}
        if len(meta) > 1:
            d["annotations"] = [meta]
        return d

    if speakers:
        content = [item(t, s) for s, t in (turns or [])]
        speech_cfg: Any = {"speakers": [{"speaker": s, "voice": v} for s, v in speakers.items()]}
    else:
        content = [item(text)]
        speech_cfg = [{"voice": voice}]

    inter = interact(
        [{"type": "user_input", "content": content}], model=model or DEFAULTS["tts"],
        response_format=drop_none({"type": "audio", "mime_type": mime_type, "sample_rate": sample_rate}),
        generation_config_extra={"speech_config": speech_cfg},
    )
    aud = first_media(inter, "audio")
    if not aud or not aud.get("data"):
        raise GemError(f"Kein Audio in der Antwort: {json.dumps(inter)[:400]}")
    return base64.b64decode(aud["data"]), inter


# ---------------------------------------------------------------------------
# Modul 7: Files API + Speech-to-Text
# ---------------------------------------------------------------------------

def upload_file(path: str, display_name: Optional[str] = None) -> dict:
    """
    Files API per "resumable upload" (Standard-Protokoll von Google). ACHTUNG: Diesen Ablauf
    zeigt die gelesene Doku nur per SDK - die REST-Schritte hier stammen aus allgemeinem
    Wissen ueber die Google-Upload-Protokolle. Bei Fehlern: SDK `client.files.upload` nutzen.
      Schritt 1 (start):   kuendigt Dateigroesse/-typ an, Antwort-Header X-Goog-Upload-URL.
      Schritt 2 (upload, finalize): schickt die Bytes an diese URL.
    """
    data = Path(path).read_bytes()
    mime = mime_of(path)
    start = check(requests.post(
        f"{UPLOAD_BASE}/files",
        headers=headers(extra={
            "X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(len(data)),
            "X-Goog-Upload-Header-Content-Type": mime,
        }),
        json={"file": {"display_name": display_name or Path(path).name}}, timeout=120))
    url = start.headers.get("X-Goog-Upload-URL")
    if not url:
        raise GemError("Upload-URL fehlt in der Antwort")
    fin = check(requests.post(
        url,
        headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"], "X-Goog-Upload-Offset": "0",
                 "X-Goog-Upload-Command": "upload, finalize", "Content-Length": str(len(data))},
        data=data, timeout=600))
    f = fin.json()["file"]
    return wait_file_active(f["name"].split("/")[-1])  # Audio/Video muss erst ACTIVE werden


def speech_to_text(
    audio_path: str,
    *,
    model: Optional[str] = None,
    language_codes: Optional[list[str]] = None,  # ["de-DE"]; leer = automatisch
    speakers: bool = False,                      # Diarization (bis 8 Sprecher), nur im verbatim-Modus
    word_timestamps: bool = False,
    custom_vocabulary: Optional[list[str]] = None,  # nicht mit Diarization/Timestamps kombinierbar
    inline: bool = False,                        # EXPERIMENTELL: base64 statt Files API
) -> tuple[str, dict]:
    """
    Modi laut Doku: "smart" (sauber formatiert) oder verbatim (woertlich, mit Sprechern/Timestamps).
    Smart darf NICHT mit Timestamps/Diarization kombiniert werden.
    """
    if speakers or word_timestamps:
        mode: Any = drop_none({"type": "verbatim",
                               "diarization_mode": "speaker" if speakers else None,
                               "timestamp_granularities": ["word"] if word_timestamps else None})
    else:
        mode = "smart"
    cfg = drop_none({"language_codes": language_codes, "custom_vocabulary": custom_vocabulary, "mode": mode})

    if inline:
        audio_item = {"type": "audio", "mime_type": mime_of(audio_path), "data": b64_file(audio_path)}
    else:
        f = upload_file(audio_path)
        audio_item = {"type": "audio", "uri": f["uri"], "mime_type": f.get("mimeType") or mime_of(audio_path)}

    inter = interact([audio_item], model=model or DEFAULTS["stt"],
                     generation_config_extra={"transcription_config": cfg})
    return output_text(inter), inter


# ---------------------------------------------------------------------------
# Modul 8: Embeddings
# ---------------------------------------------------------------------------

def embed_content(
    inputs: str | list[str | dict],
    *,
    model: Optional[str] = None,
    dimensions: Optional[int] = None,   # 128..3072 (Default 3072; empfohlen 768/1536/3072)
    separate: bool = True,              # True -> je Eingabe ein Vektor (batch); False -> EIN aggregierter Vektor
) -> list[list[float]]:
    """
    gemini-embedding-2 kann auch Bilder/Audio/PDF einbetten: Eingabe als dict
    {"inline_data": {"mime_type":..., "data": <base64>}}. Mehrere parts in EINEM content
    ergeben EINEN gemeinsamen Vektor; getrennte contents ergeben getrennte Vektoren.
    Hinweis: `taskType` wird von gemini-embedding-2 NICHT unterstuetzt - schreib die Aufgabe
    in den Text, z.B. "task: classification | query: ...".
    """
    items = [inputs] if isinstance(inputs, str) else list(inputs)
    parts = [{"text": x} if isinstance(x, str) else x for x in items]
    mdl = model or DEFAULTS["embed"]
    if not separate:
        body = drop_none({"model": f"models/{mdl}", "content": {"parts": parts},
                          "output_dimensionality": dimensions})
        resp = post(f"/models/{mdl}:embedContent", body).json()
        emb = resp.get("embedding") or (resp.get("embeddings") or [{}])[0]
        return [emb["values"]]
    reqs = [drop_none({"model": f"models/{mdl}", "content": {"parts": [p]},
                       "output_dimensionality": dimensions}) for p in parts]
    resp = post(f"/models/{mdl}:batchEmbedContents", {"requests": reqs}).json()
    return [e["values"] for e in resp["embeddings"]]


def cosine(a: list[float], b: list[float]) -> float:
    """Aehnlichkeit zweier Vektoren: 1.0 = gleiche Richtung, 0 = unabhaengig."""
    dot = sum(x * y for x, y in zip(a, b))
    return dot / ((sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5))


# ---------------------------------------------------------------------------
# CLI (duenne Huelle - der eigentliche Lerninhalt steht oben)
# ---------------------------------------------------------------------------

def demo_weather(location: str, unit: str = "celsius") -> dict:
    """Fake-Wetterfunktion fuer den Tool-Test. Eine echte wuerde hier eine Wetter-API aufrufen."""
    return {"location": location, "temperature": 21, "unit": unit, "condition": "sonnig"}


WEATHER_TOOL = function_tool(
    "get_weather", "Aktuelles Wetter fuer einen Ort abrufen",
    {"type": "object", "properties": {"location": {"type": "string"},
                                      "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}},
     "required": ["location"]})


def cmd_models(a: argparse.Namespace) -> None:
    ms = list_models()
    if a.filter:
        ms = [m for m in ms if a.filter.lower() in m.get("name", "").lower()]
    print(f"{len(ms)} Modelle")
    for m in ms:
        print(f"{m.get('name', '?'):<55} {m.get('displayName', '')}")


def cmd_chat(a: argparse.Namespace) -> None:
    if a.tools:
        print(run_tool_loop(a.prompt, {"get_weather": demo_weather}, [WEATHER_TOOL], model=a.model))
        return
    rf = None
    if a.schema:
        rf = json_response_format({
            "type": "object",
            "properties": {"title": {"type": "string"}, "ingredients": {"type": "array", "items": {"type": "string"}},
                           "minutes": {"type": "integer"}},
            "required": ["title", "ingredients", "minutes"]})
    tools = []
    if a.search:
        tools.append(GOOGLE_SEARCH)
    if a.urls:
        tools.append(URL_CONTEXT)
    kw = dict(
        model=a.model or (DEFAULTS["pro"] if a.pro else None), system_instruction=a.system,
        thinking_level=a.thinking, thinking_summaries="auto" if a.show_thoughts else None,
        max_output_tokens=a.max_tokens, tools=tools or None, response_format=rf,
    )
    inp = make_input(a.prompt, images=a.image, audio=a.audio, video=a.video)
    if a.stream:
        for kind, txt in stream_text(interact(inp, stream=True, **kw)):
            if kind == "text":
                print(txt, end="", flush=True)
            elif kind == "thought":
                print(f"\033[2m{txt}\033[0m", end="", flush=True)
            elif kind == "usage":
                print(f"\n[usage] {txt}")
            elif a.debug:
                print(f"\n[{kind}] {txt}")
        print()
        return
    inter = interact(inp, **kw)
    for t in thought_summaries(inter):
        print(f"\033[2m[thought] {t}\033[0m")
    print(output_text(inter))
    u = inter.get("usage") or {}
    print(f"\n[model={inter.get('model')} status={inter.get('status')} in={u.get('total_input_tokens')} "
          f"out={u.get('total_output_tokens')} thought={u.get('total_thought_tokens')}]")


def cmd_legacy(a: argparse.Namespace) -> None:
    r = generate_content(a.prompt, model=a.model)
    print(legacy_text(r) or json.dumps(r, indent=2, ensure_ascii=False)[:1500])


def cmd_image(a: argparse.Namespace) -> None:
    img, inter = generate_image(a.prompt, model=a.model, edit_images=a.edit, aspect_ratio=a.aspect,
                                image_size=a.size, search=a.search)
    if not img:
        sys.exit(f"Kein Bild erhalten. Text: {output_text(inter)[:300] or inter.get('status')}")
    p = out_path(a.out)
    p.write_bytes(img)
    print("gespeichert:", p, "| interaction id:", inter.get("id"), "(fuer --previous weiteres Editieren)")


def cmd_video(a: argparse.Namespace) -> None:
    t0 = time.time()
    vid, inter = generate_video(a.prompt, model=a.model, aspect_ratio=a.aspect, resolution=a.resolution,
                                delivery="inline" if a.inline else "uri", reference_images=a.ref,
                                previous_interaction_id=a.previous)
    p = out_path(a.out)
    p.write_bytes(vid)
    print(f"gespeichert: {p} ({len(vid) / 1e6:.1f} MB, {time.time() - t0:.0f}s) | id: {inter.get('id')}")


def cmd_tts(a: argparse.Namespace) -> None:
    wav, _ = text_to_speech(a.text, model=a.model, voice=a.voice, style=a.style)
    p = out_path(a.out)
    p.write_bytes(wav)
    print("gespeichert:", p, f"({len(wav)} Bytes)")


def cmd_stt(a: argparse.Namespace) -> None:
    text, inter = speech_to_text(a.audio, model=a.model, language_codes=a.lang, speakers=a.speakers,
                                 word_timestamps=a.words, inline=a.inline)
    print(text)
    if a.debug:
        print(json.dumps(inter, indent=2, ensure_ascii=False)[:3000])


def cmd_embed(a: argparse.Namespace) -> None:
    vecs = embed_content(a.texts, model=a.model, dimensions=a.dim)
    print(f"{len(vecs)} Vektoren, dim={len(vecs[0])}")
    if len(vecs) >= 2:
        print(f"cosine(0,1) = {cosine(vecs[0], vecs[1]):.4f}")


def cmd_all(a: argparse.Namespace) -> None:
    tests: list[tuple[str, Callable[[], Any]]] = [
        ("models", lambda: len(list_models())),
        ("chat", lambda: output_text(interact("Sag 'ok'.", max_output_tokens=200))),
        ("chat-stream", lambda: "".join(t for k, t in stream_text(interact("Zaehle bis 3.", stream=True, max_output_tokens=200)) if k == "text")),
        ("legacy", lambda: legacy_text(generate_content("Sag 'ok'."))),
        ("json-schema", lambda: output_text(interact("Name: Max, Alter 31", response_format=json_response_format(
            {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
             "required": ["name", "age"]})))),
        ("tools", lambda: run_tool_loop("Wetter in Berlin?", {"get_weather": demo_weather}, [WEATHER_TOOL], verbose=False)[:50]),
        ("embed", lambda: len(embed_content(["Hallo", "Hi"])[0])),
        ("tts", lambda: len(text_to_speech("Hallo")[0])),
    ]
    if a.audio:
        tests.append(("stt", lambda: speech_to_text(a.audio)[0][:50]))
    if a.media:
        tests.append(("image", lambda: len(generate_image("a red apple")[0] or b"")))
    if a.video:
        tests.append(("video", lambda: len(generate_video("a cat walking", resolution="360p")[0])))
    rows = []
    for name, fn in tests:
        t0 = time.time()
        try:
            rows.append((name, "OK", f"{time.time() - t0:.1f}s", str(fn())[:60]))
        except Exception as e:  # noqa: BLE001
            rows.append((name, "FAIL", f"{time.time() - t0:.1f}s", f"{type(e).__name__}: {e}"[:110]))
            if a.verbose:
                traceback.print_exc()
    print(f"\n{'Modul':<13}{'Status':<8}{'Zeit':<8}Detail\n" + "-" * 80)
    for r in rows:
        print(f"{r[0]:<13}{r[1]:<8}{r[2]:<8}{r[3]}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Gemini - Lern-/Testdatei")
    sub = p.add_subparsers(dest="cmd", required=True)

    def mk(name: str, fn: Callable, **kw: Any) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, **kw)
        sp.set_defaults(func=fn)
        return sp

    sp = mk("models", cmd_models); sp.add_argument("--filter")

    sp = mk("chat", cmd_chat)
    sp.add_argument("prompt"); sp.add_argument("--model"); sp.add_argument("--pro", action="store_true")
    sp.add_argument("--system"); sp.add_argument("--stream", action="store_true")
    sp.add_argument("--thinking", choices=["minimal", "low", "medium", "high"])
    sp.add_argument("--show-thoughts", action="store_true")
    sp.add_argument("--image", nargs="+"); sp.add_argument("--audio"); sp.add_argument("--video")
    sp.add_argument("--schema", action="store_true"); sp.add_argument("--tools", action="store_true")
    sp.add_argument("--search", action="store_true", help="google_search"); sp.add_argument("--urls", action="store_true", help="url_context")
    sp.add_argument("--max-tokens", type=int); sp.add_argument("--debug", action="store_true")

    sp = mk("legacy", cmd_legacy, help="generateContent (altes Format)")
    sp.add_argument("prompt"); sp.add_argument("--model")

    sp = mk("image", cmd_image)
    sp.add_argument("prompt"); sp.add_argument("--model"); sp.add_argument("--out", default="image.png")
    sp.add_argument("--aspect"); sp.add_argument("--size", choices=["1K", "2K", "4K"])
    sp.add_argument("--edit", nargs="+", help="Eingabebilder zum Editieren"); sp.add_argument("--search", action="store_true")

    sp = mk("video", cmd_video)
    sp.add_argument("prompt"); sp.add_argument("--model"); sp.add_argument("--out", default="video.mp4")
    sp.add_argument("--aspect", choices=["16:9", "9:16"]); sp.add_argument("--resolution", choices=["360p", "720p", "1080p", "4k"])
    sp.add_argument("--inline", action="store_true", help="base64 statt URI-Delivery")
    sp.add_argument("--ref", nargs="+", help="Referenz-/Startbilder"); sp.add_argument("--previous", help="interaction id zum Weiterbearbeiten")

    sp = mk("tts", cmd_tts)
    sp.add_argument("text"); sp.add_argument("--model"); sp.add_argument("--voice", default="Kore", choices=VOICES)
    sp.add_argument("--style"); sp.add_argument("--out", default="speech.wav")

    sp = mk("stt", cmd_stt)
    sp.add_argument("audio"); sp.add_argument("--model"); sp.add_argument("--lang", nargs="+")
    sp.add_argument("--speakers", action="store_true"); sp.add_argument("--words", action="store_true")
    sp.add_argument("--inline", action="store_true", help="experimentell: base64 statt Files API"); sp.add_argument("--debug", action="store_true")

    sp = mk("embed", cmd_embed)
    sp.add_argument("texts", nargs="+"); sp.add_argument("--model"); sp.add_argument("--dim", type=int)

    sp = mk("all", cmd_all, help="Smoke-Test")
    sp.add_argument("--audio"); sp.add_argument("--media", action="store_true")
    sp.add_argument("--video", action="store_true"); sp.add_argument("--verbose", action="store_true")
    return p


def main() -> None:
    a = build_parser().parse_args()
    try:
        a.func(a)
    except GemError as e:
        sys.exit(f"Fehler: {e}")


if __name__ == "__main__":
    main()
