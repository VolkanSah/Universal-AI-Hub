# HF get own file openrouter , too

#!/usr/bin/env python3
"""
hfbrige.py - modulare Testdatei fuer Hugging Face Inference Providers.

Jeder Task ist ein eigenes Modul (eine Funktion + ein CLI-Subcommand), damit du
sie einzeln testen kannst. Basis sind die Doku-Seiten:
  chat-completion, feature-extraction, text-to-image, text-to-video,
  image-to-image, zero-shot-classification

Setup:
    pip install -U huggingface_hub requests pillow numpy
    export HF_TOKEN=hf_xxx        # Token mit "Inference Providers"-Berechtigung

Benutzung:
    python hfbrige.py chat "Was ist die Hauptstadt von Frankreich?"
    python hfbrige.py chat "Erzaehl was" --stream
    python hfbrige.py chat "Beschreibe das Bild" --image https://example.com/bild.jpg
    python hfbrige.py chat "Gib mir JSON" --json
    python hfbrige.py embed "Hallo Welt" "Noch ein Satz" --normalize
    python hfbrige.py t2i "Astronaut riding a horse" --out astronaut.png
    python hfbrige.py t2v "A young man walking on the street" --out video.mp4
    python hfbrige.py i2i cat.png "Turn the cat into a tiger." --out tiger.png
    python hfbrige.py zeroshot "Ich will mein Geld zurueck" --labels refund legal faq
    python hfbrige.py all          # Smoke-Test aller Module (ohne i2i, braucht Bild)
    python hfbrige.py all --image cat.png   # inkl. i2i

Modelle/Provider lassen sich unten in DEFAULTS aendern oder per --model / --provider
pro Aufruf ueberschreiben.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

# provider=None -> HF waehlt automatisch (Routing nach deinen Settings).
# Modelle stammen aus den "Recommended models" bzw. Snippets der Doku.
DEFAULTS: dict[str, dict[str, Optional[str]]] = {
    "chat": {"model": "openai/gpt-oss-120b", "provider": None},
    "chat_vlm": {"model": "Qwen/Qwen2.5-VL-3B-Instruct", "provider": None},
    "embed": {"model": "thenlper/gte-large", "provider": "hf-inference"},
    "t2i": {"model": "black-forest-labs/FLUX.1-dev", "provider": "fal-ai"},
    "t2v": {"model": "Wan-AI/Wan2.2-TI2V-5B", "provider": "fal-ai"},
    "i2i": {"model": "black-forest-labs/FLUX.2-klein-9B", "provider": "fal-ai"},
    "zeroshot": {"model": "facebook/bart-large-mnli", "provider": "hf-inference"},
}

ROUTER_BASE = "https://router.huggingface.co"
OUT_DIR = Path("hf_out")


# ---------------------------------------------------------------------------
# Gemeinsame Helfer
# ---------------------------------------------------------------------------

def get_token() -> str:
    token = os.environ.get("HF_TOKEN")
    if not token:
        sys.exit("HF_TOKEN fehlt. Setze: export HF_TOKEN=hf_xxx")
    return token


def get_client(provider: Optional[str] = None):
    """InferenceClient mit optionalem Provider (z.B. 'fal-ai', 'hf-inference')."""
    from huggingface_hub import InferenceClient

    kwargs: dict[str, Any] = {"api_key": get_token()}
    if provider:
        kwargs["provider"] = provider
    return InferenceClient(**kwargs)


def resolve(task: str, model: Optional[str], provider: Optional[str]) -> tuple[str, Optional[str]]:
    d = DEFAULTS[task]
    return model or d["model"], (provider if provider is not None else d["provider"])  # type: ignore[return-value]


def save_bytes(data: bytes, out: str | Path) -> Path:
    path = Path(out)
    if not path.is_absolute() and path.parent == Path("."):
        OUT_DIR.mkdir(exist_ok=True)
        path = OUT_DIR / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def save_image(image: Any, out: str | Path) -> Path:
    """Speichert ein PIL.Image (Rueckgabe von text_to_image / image_to_image)."""
    path = Path(out)
    if not path.is_absolute() and path.parent == Path("."):
        OUT_DIR.mkdir(exist_ok=True)
        path = OUT_DIR / path
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


def drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def image_to_data_url(path: str) -> str:
    """Lokale Datei -> data:-URL (fuer VLM image_url). URLs werden unveraendert gelassen."""
    if path.startswith(("http://", "https://", "data:")):
        return path
    ext = Path(path).suffix.lower().lstrip(".") or "png"
    mime = "jpeg" if ext in ("jpg", "jpeg") else ext
    b64 = base64.b64encode(Path(path).read_bytes()).decode()
    return f"data:image/{mime};base64,{b64}"


# ---------------------------------------------------------------------------
# Modul 1: Chat Completion (LLM + VLM, Streaming, JSON-Schema, Tools)
# ---------------------------------------------------------------------------

def chat_completion(
    prompt: str,
    *,
    system: Optional[str] = None,
    image: Optional[str] = None,           # URL oder lokaler Pfad -> VLM
    model: Optional[str] = None,
    provider: Optional[str] = None,
    stream: bool = False,
    max_tokens: Optional[int] = 512,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    seed: Optional[int] = None,
    stop: Optional[list[str]] = None,
    reasoning_effort: Optional[str] = None,  # none/minimal/low/medium/high/xhigh
    response_format: Optional[dict] = None,  # {"type":"json_object"} oder json_schema
    tools: Optional[list[dict]] = None,
    tool_choice: Optional[Any] = None,       # "auto"/"none"/"required"/{"function":{"name":..}}
    history: Optional[list[dict]] = None,
) -> Any:
    """
    Nutzt den OpenAI-kompatiblen Endpunkt von HF (huggingface_hub InferenceClient).
    Gibt bei stream=False die Message zurueck, bei stream=True einen Generator
    mit Text-Deltas.
    """
    task = "chat_vlm" if image else "chat"
    model, provider = resolve(task, model, provider)
    client = get_client(provider)

    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    if history:
        messages.extend(history)

    if image:
        content: Any = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_to_data_url(image)}},
        ]
    else:
        content = prompt
    messages.append({"role": "user", "content": content})

    params = drop_none(
        dict(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            seed=seed,
            stop=stop,
            reasoning_effort=reasoning_effort,
            response_format=response_format,
            tools=tools,
            tool_choice=tool_choice,
            stream=stream or None,
        )
    )

    if stream:
        def _gen():
            for chunk in client.chat.completions.create(**params):
                if chunk.choices and chunk.choices[0].delta:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        yield delta
        return _gen()

    completion = client.chat.completions.create(**params)
    return completion.choices[0].message


def json_schema_format(name: str, schema: dict, strict: bool = True) -> dict:
    """Baut response_format fuer Structured Outputs (type=json_schema)."""
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "schema": schema, "strict": strict},
    }


def demo_tool() -> list[dict]:
    """Kleines Beispiel-Tool zum Testen von tools/tool_choice."""
    return [
        {
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
        }
    ]


# ---------------------------------------------------------------------------
# Modul 2: Feature Extraction (Embeddings)
# ---------------------------------------------------------------------------

def feature_extraction(
    texts: str | list[str],
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    normalize: Optional[bool] = None,
    truncate: Optional[bool] = None,
    truncation_direction: Optional[str] = None,  # "left" | "right"
    prompt_name: Optional[str] = None,           # sentence-transformers prompt key
):
    """Text(e) -> Vektor(en). Rueckgabe: numpy-Array (HF liefert Array of arrays)."""
    model, provider = resolve("embed", model, provider)
    client = get_client(provider)
    kwargs = drop_none(
        dict(
            normalize=normalize,
            truncate=truncate,
            truncation_direction=truncation_direction,
            prompt_name=prompt_name,
        )
    )
    return client.feature_extraction(texts, model=model, **kwargs)


def cosine_similarity(a, b) -> float:
    import numpy as np

    a, b = np.asarray(a, dtype=float).ravel(), np.asarray(b, dtype=float).ravel()
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


# ---------------------------------------------------------------------------
# Modul 3: Text to Image
# ---------------------------------------------------------------------------

def text_to_image(
    prompt: str,
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    negative_prompt: Optional[str] = None,
    num_inference_steps: Optional[int] = None,
    guidance_scale: Optional[float] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    seed: Optional[int] = None,
    scheduler: Optional[str] = None,
):
    """Rueckgabe: PIL.Image."""
    model, provider = resolve("t2i", model, provider)
    client = get_client(provider)
    kwargs = drop_none(
        dict(
            negative_prompt=negative_prompt,
            num_inference_steps=num_inference_steps,
            guidance_scale=guidance_scale,
            width=width,
            height=height,
            seed=seed,
            scheduler=scheduler,
        )
    )
    return client.text_to_image(prompt, model=model, **kwargs)


# ---------------------------------------------------------------------------
# Modul 4: Text to Video
# ---------------------------------------------------------------------------

def text_to_video(
    prompt: str,
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    num_frames: Optional[float] = None,
    guidance_scale: Optional[float] = None,
    negative_prompt: Optional[list[str]] = None,
    num_inference_steps: Optional[int] = None,
    seed: Optional[int] = None,
) -> bytes:
    """Rueckgabe: rohe Video-Bytes (meist mp4)."""
    model, provider = resolve("t2v", model, provider)
    client = get_client(provider)
    kwargs = drop_none(
        dict(
            num_frames=num_frames,
            guidance_scale=guidance_scale,
            negative_prompt=negative_prompt,
            num_inference_steps=num_inference_steps,
            seed=seed,
        )
    )
    return client.text_to_video(prompt, model=model, **kwargs)


# ---------------------------------------------------------------------------
# Modul 5: Image to Image
# ---------------------------------------------------------------------------

def image_to_image(
    image_path: str,
    prompt: Optional[str] = None,
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    negative_prompt: Optional[str] = None,
    num_inference_steps: Optional[int] = None,
    guidance_scale: Optional[float] = None,
    target_size: Optional[tuple[int, int]] = None,  # (width, height), nur manche Provider
):
    """Rueckgabe: PIL.Image."""
    model, provider = resolve("i2i", model, provider)
    client = get_client(provider)
    input_image = Path(image_path).read_bytes()
    kwargs = drop_none(
        dict(
            prompt=prompt,
            negative_prompt=negative_prompt,
            num_inference_steps=num_inference_steps,
            guidance_scale=guidance_scale,
        )
    )
    if target_size:
        from huggingface_hub import ImageToImageTargetSize

        kwargs["target_size"] = ImageToImageTargetSize(width=target_size[0], height=target_size[1])
    return client.image_to_image(input_image, model=model, **kwargs)


# ---------------------------------------------------------------------------
# Modul 6: Zero-Shot Classification (rohe HTTP-Variante wie in der Doku)
# ---------------------------------------------------------------------------

def zero_shot_classification(
    text: str,
    candidate_labels: list[str],
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,  # nur 'hf-inference' laut Doku; Param fuer Symmetrie
    multi_label: Optional[bool] = None,
    hypothesis_template: Optional[str] = None,
) -> list[dict]:
    """Rueckgabe: Liste {label, score}. Direkter POST wie im Doku-Snippet."""
    import requests

    model, _ = resolve("zeroshot", model, provider)
    url = f"{ROUTER_BASE}/hf-inference/models/{model}"
    headers = {"Authorization": f"Bearer {get_token()}"}
    parameters = drop_none(
        dict(
            candidate_labels=candidate_labels,
            multi_label=multi_label,
            hypothesis_template=hypothesis_template,
        )
    )
    r = requests.post(url, headers=headers, json={"inputs": text, "parameters": parameters}, timeout=120)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# CLI-Handler (duenne Schicht ueber den Modulen)
# ---------------------------------------------------------------------------

def cmd_chat(a: argparse.Namespace) -> None:
    response_format = {"type": "json_object"} if a.json else None
    tools = demo_tool() if a.tools else None
    out = chat_completion(
        a.prompt,
        system=a.system,
        image=a.image,
        model=a.model,
        provider=a.provider,
        stream=a.stream,
        max_tokens=a.max_tokens,
        temperature=a.temperature,
        reasoning_effort=a.reasoning,
        response_format=response_format,
        tools=tools,
        tool_choice="auto" if tools else None,
    )
    if a.stream:
        for piece in out:
            print(piece, end="", flush=True)
        print()
        return
    if getattr(out, "tool_calls", None):
        for tc in out.tool_calls:
            print(f"TOOL CALL: {tc.function.name}({tc.function.arguments})")
    print(out.content)


def cmd_embed(a: argparse.Namespace) -> None:
    vecs = feature_extraction(
        a.texts if len(a.texts) > 1 else a.texts[0],
        model=a.model,
        provider=a.provider,
        normalize=True if a.normalize else None,
    )
    import numpy as np

    arr = np.asarray(vecs)
    print(f"shape={arr.shape} dtype={arr.dtype}")
    if arr.ndim == 2 and arr.shape[0] >= 2:
        print(f"cosine(text0, text1) = {cosine_similarity(arr[0], arr[1]):.4f}")
    print("erste 5 Werte:", arr.ravel()[:5])


def cmd_t2i(a: argparse.Namespace) -> None:
    img = text_to_image(
        a.prompt,
        model=a.model,
        provider=a.provider,
        negative_prompt=a.negative,
        num_inference_steps=a.steps,
        guidance_scale=a.guidance,
        width=a.width,
        height=a.height,
        seed=a.seed,
    )
    print("gespeichert:", save_image(img, a.out))


def cmd_t2v(a: argparse.Namespace) -> None:
    video = text_to_video(
        a.prompt,
        model=a.model,
        provider=a.provider,
        num_frames=a.frames,
        guidance_scale=a.guidance,
        negative_prompt=[a.negative] if a.negative else None,
        num_inference_steps=a.steps,
        seed=a.seed,
    )
    print("gespeichert:", save_bytes(video, a.out))


def cmd_i2i(a: argparse.Namespace) -> None:
    img = image_to_image(
        a.image,
        a.prompt,
        model=a.model,
        provider=a.provider,
        negative_prompt=a.negative,
        num_inference_steps=a.steps,
        guidance_scale=a.guidance,
        target_size=(a.width, a.height) if a.width and a.height else None,
    )
    print("gespeichert:", save_image(img, a.out))


def cmd_zeroshot(a: argparse.Namespace) -> None:
    res = zero_shot_classification(
        a.text,
        a.labels,
        model=a.model,
        multi_label=True if a.multi else None,
        hypothesis_template=a.template,
    )
    for item in res:
        print(f"{item['label']:<20} {item['score']:.4f}")


def cmd_all(a: argparse.Namespace) -> None:
    """Smoke-Test: ruft jedes Modul mit Minimal-Input auf und meldet OK/FAIL."""
    tests: list[tuple[str, Callable[[], Any]]] = [
        ("chat", lambda: chat_completion("Sag 'ok'.", max_tokens=20).content),
        ("chat-stream", lambda: "".join(chat_completion("Zaehle bis 3.", stream=True, max_tokens=30))),
        ("embed", lambda: feature_extraction(["Hallo", "Hi"]).shape),
        ("zeroshot", lambda: zero_shot_classification("Ich will Geld zurueck", ["refund", "legal", "faq"])[0]),
        ("t2i", lambda: save_image(text_to_image("a red apple"), "smoke_t2i.png")),
        ("t2v", lambda: save_bytes(text_to_video("a cat walking"), "smoke_t2v.mp4")),
    ]
    if a.image:
        tests.append(("i2i", lambda: save_image(image_to_image(a.image, "make it watercolor"), "smoke_i2i.png")))

    results = []
    for name, fn in tests:
        t0 = time.time()
        try:
            res = fn()
            results.append((name, "OK", f"{time.time() - t0:.1f}s", str(res)[:60]))
        except Exception as e:  # noqa: BLE001 - Testtool, alles melden
            results.append((name, "FAIL", f"{time.time() - t0:.1f}s", f"{type(e).__name__}: {e}"[:100]))
            if a.verbose:
                traceback.print_exc()

    print(f"\n{'Modul':<12}{'Status':<8}{'Zeit':<8}Detail")
    print("-" * 70)
    for r in results:
        print(f"{r[0]:<12}{r[1]:<8}{r[2]:<8}{r[3]}")


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="HF Inference Providers - modulare Tests")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--model", help="Modell ueberschreiben")
        sp.add_argument("--provider", help="Provider ueberschreiben (z.B. fal-ai, hf-inference)")

    sp = sub.add_parser("chat", help="Chat Completion")
    sp.add_argument("prompt")
    sp.add_argument("--system")
    sp.add_argument("--image", help="URL oder Datei -> VLM")
    sp.add_argument("--stream", action="store_true")
    sp.add_argument("--json", action="store_true", help="response_format=json_object")
    sp.add_argument("--tools", action="store_true", help="Demo-Tool get_weather mitsenden")
    sp.add_argument("--max-tokens", type=int, default=512)
    sp.add_argument("--temperature", type=float)
    sp.add_argument("--reasoning", help="reasoning_effort (low/medium/high ...)")
    common(sp)
    sp.set_defaults(func=cmd_chat)

    sp = sub.add_parser("embed", help="Feature Extraction")
    sp.add_argument("texts", nargs="+")
    sp.add_argument("--normalize", action="store_true")
    common(sp)
    sp.set_defaults(func=cmd_embed)

    sp = sub.add_parser("t2i", help="Text to Image")
    sp.add_argument("prompt")
    sp.add_argument("--out", default="t2i.png")
    sp.add_argument("--negative")
    sp.add_argument("--steps", type=int)
    sp.add_argument("--guidance", type=float)
    sp.add_argument("--width", type=int)
    sp.add_argument("--height", type=int)
    sp.add_argument("--seed", type=int)
    common(sp)
    sp.set_defaults(func=cmd_t2i)

    sp = sub.add_parser("t2v", help="Text to Video")
    sp.add_argument("prompt")
    sp.add_argument("--out", default="t2v.mp4")
    sp.add_argument("--negative")
    sp.add_argument("--frames", type=float)
    sp.add_argument("--steps", type=int)
    sp.add_argument("--guidance", type=float)
    sp.add_argument("--seed", type=int)
    common(sp)
    sp.set_defaults(func=cmd_t2v)

    sp = sub.add_parser("i2i", help="Image to Image")
    sp.add_argument("image", help="Eingabebild (Datei)")
    sp.add_argument("prompt", nargs="?")
    sp.add_argument("--out", default="i2i.png")
    sp.add_argument("--negative")
    sp.add_argument("--steps", type=int)
    sp.add_argument("--guidance", type=float)
    sp.add_argument("--width", type=int, help="target_size (mit --height)")
    sp.add_argument("--height", type=int)
    common(sp)
    sp.set_defaults(func=cmd_i2i)

    sp = sub.add_parser("zeroshot", help="Zero-Shot Classification")
    sp.add_argument("text")
    sp.add_argument("--labels", nargs="+", required=True)
    sp.add_argument("--multi", action="store_true", help="multi_label=True")
    sp.add_argument("--template", help="hypothesis_template, z.B. 'Das Thema ist {}.'")
    common(sp)
    sp.set_defaults(func=cmd_zeroshot)

    sp = sub.add_parser("all", help="Smoke-Test aller Module")
    sp.add_argument("--image", help="Bild fuer i2i-Test (optional)")
    sp.add_argument("--verbose", action="store_true")
    sp.set_defaults(func=cmd_all)

    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
