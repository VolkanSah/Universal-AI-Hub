# =============================================================================
# app/healer.py
# Self-Healing Runtime Debugger — Hub-native Tool
# Universal MCP Hub (Sandboxed) - based on PyFundaments Architecture
# Copyright 2026 - Volkan Kücükbudak
# Apache License V. 2 + ESOL 1.1
# =============================================================================
# ARCHITECTURE NOTE:
#   Lives in app/ like all other tool modules.
#   Called exclusively by tools.py via provider_type = "heal" / "heal_generate".
#   Never calls providers.py directly — goes through tools.run() → here.
#   Uses the hub's own provider/fallback chain for LLM calls.
#
# HOW IT FITS:
#   .pyfun [TOOL.heal_code]     → provider_type = "heal"
#   .pyfun [TOOL.heal_task]     → provider_type = "heal_generate"
#   tools.py run()              → dispatches to healer.heal() / healer.generate_and_heal()
#   providers.py llm_complete() → used internally for LLM calls (full fallback chain!)
#
# SECURITY:
#   Code runs in subprocess — isolated from hub process.
#   No network access in subprocess (inherits OS sandbox if present).
#   Stdout/stderr captured, never executed as hub code.
# =============================================================================

import asyncio
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

logger = logging.getLogger("healer")

# ── Config from ENV (set in .env, referenced in .pyfun) ──────────────────────
TIMEOUT     = int(os.getenv("HEALER_TIMEOUT",     "10"))
MAX_REPAIRS = int(os.getenv("HEALER_MAX_REPAIRS",  "5"))

# System prompts — keep them short, hub already has context limits
_SYSTEM_REPAIR = (
    "You are a Python debugger. Fix the broken code. "
    "Return ONLY a python code block, nothing else."
)
_SYSTEM_GENERATE = (
    "You are a Python developer. Write clean, runnable Python code for the task. "
    "Return ONLY a python code block, no explanation."
)


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class RunResult:
    stdout:      str
    stderr:      str
    returncode:  int
    duration_ms: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.stderr.strip()


@dataclass
class RepairAttempt:
    attempt:     int
    provider:    str   # which provider actually answered (from hub response prefix)
    error:       str
    fixed_code:  str
    success:     bool


@dataclass
class HealResult:
    original_code: str
    final_code:    str
    success:       bool
    attempts:      list[RepairAttempt] = field(default_factory=list)
    final_stdout:  str = ""
    final_stderr:  str = ""
    total_repairs: int = 0

    def to_text(self) -> str:
        """Human-readable summary — returned as tool response to MCP client."""
        status = "✓ healed" if self.success else "✗ failed"
        lines = [f"{status} | {self.total_repairs} repair(s)"]
        for a in self.attempts:
            ok = "✓" if a.success else "✗"
            lines.append(f"  attempt {a.attempt} [{a.provider}] {ok}  {a.error[:100]}")
        if self.final_stdout:
            lines.append(f"stdout: {self.final_stdout[:300]}")
        if not self.success and self.final_stderr:
            lines.append(f"stderr: {self.final_stderr[:300]}")
        lines.append("--- final code ---")
        lines.append(self.final_code)
        return "\n".join(lines)


# ── Subprocess execution ──────────────────────────────────────────────────────

def _run_python(code: str) -> RunResult:
    """Write to tempfile, execute in subprocess, capture output."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        path = f.name
    try:
        t0 = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, path],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        return RunResult(proc.stdout, proc.stderr, proc.returncode, elapsed)
    except subprocess.TimeoutExpired:
        return RunResult("", f"TimeoutExpired: exceeded {TIMEOUT}s", 1, TIMEOUT * 1000.0)
    finally:
        os.unlink(path)


def _extract_code(text: str) -> str:
    """Pull code from markdown block, fall back to raw text."""
    m = re.search(r"```(?:python)?\n?([\s\S]*?)```", text)
    return m.group(1).strip() if m else text.strip()


def _provider_from_response(text: str) -> str:
    """Extract provider name from hub response prefix like '[anthropic] ...'"""
    m = re.match(r"^\[(\w+)\]", text.strip())
    return m.group(1) if m else "unknown"


# ── Core heal logic (sync — called via asyncio.to_thread) ─────────────────────

def _heal_sync(code: str, llm_fn) -> HealResult:
    """
    Run code → repair loop → return HealResult.
    llm_fn: callable(prompt, system) → str  (wraps providers.llm_complete)
    Sync because subprocess.run is blocking — called via asyncio.to_thread.
    """
    result = HealResult(original_code=code, final_code=code, success=False)
    run = _run_python(code)

    if run.ok:
        result.success = True
        result.final_stdout = run.stdout
        logger.info("healer: code ran clean on first try")
        return result

    current = code
    for attempt in range(1, MAX_REPAIRS + 1):
        error = run.stderr or f"exit code {run.returncode}"
        logger.info(f"healer: repair attempt {attempt}/{MAX_REPAIRS} — {error[:80]}")

        prompt = (
            f"Fix this Python code.\n\nERROR:\n{error}\n\n"
            f"CODE:\n```python\n{current}\n```"
        )
        try:
            raw = llm_fn(prompt, _SYSTEM_REPAIR)
        except Exception as e:
            logger.warning(f"healer: LLM call failed on attempt {attempt}: {e}")
            result.attempts.append(RepairAttempt(attempt, "error", error, current, False))
            break

        provider = _provider_from_response(raw)
        # strip provider prefix before extracting code
        fixed = _extract_code(re.sub(r"^\[\w+\]\s*", "", raw.strip()))
        current = fixed
        run = _run_python(fixed)

        repair = RepairAttempt(
            attempt=attempt,
            provider=provider,
            error=error,
            fixed_code=fixed,
            success=run.ok,
        )
        result.attempts.append(repair)
        result.total_repairs = attempt

        if run.ok:
            result.success = True
            result.final_code = fixed
            result.final_stdout = run.stdout
            logger.info(f"healer: ✓ healed after {attempt} attempt(s) via {provider}")
            return result

    result.final_code = current
    result.final_stderr = run.stderr
    logger.warning(f"healer: gave up after {MAX_REPAIRS} attempts")
    return result


# ── Public async API — called by tools.py ────────────────────────────────────

async def heal(code: str, llm_fn) -> HealResult:
    """
    Async entry point: heal existing Python code.
    llm_fn: async callable(prompt, system_prompt) → str
            Wrap providers.llm_complete here in tools.py.
    """
    # wrap async llm_fn into sync for subprocess thread
    def sync_llm(prompt, system):
        return asyncio.get_event_loop().run_until_complete(
            llm_fn(prompt, system)
        )

    return await asyncio.to_thread(_heal_sync, code, sync_llm)


async def generate_and_heal(task: str, llm_fn) -> HealResult:
    """
    Async entry point: generate Python code for task, then heal it.
    llm_fn: async callable(prompt, system_prompt) → str
    """
    logger.info(f"healer: generating code for task: {task!r}")
    raw = await llm_fn(
        f"Write Python code for this task: {task}",
        _SYSTEM_GENERATE,
    )
    code = _extract_code(re.sub(r"^\[\w+\]\s*", "", raw.strip()))
    logger.info(f"healer: generated {len(code)} chars, now healing...")
    return await heal(code, llm_fn)
