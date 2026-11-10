# =============================================================================
# app/shellmaster.py
# ShellMaster 2.0 — Modular Shell Tool
# Universal MCP Hub (Sandboxed) - based on PyFundaments Architecture
# Copyright 2026 - Volkan Kücükbudak
# Apache License V. 2 + ESOL 1.1
# Repo: https://github.com/VolkanSah/Multi-LLM-API-Gateway
# =============================================================================
# ARCHITECTURE NOTE:
#   Modular tool living in app/* only.
#   NO low-level access, NO os.environ, NO fundaments access.
#   Config comes from app/.pyfun [TOOL.shellmaster] via app/config.py.
#   LLM (SmolLM2) generates commands — shellmaster validates + confirms + executes.
#
# FLOW:
#   tools.py calls shellmaster.run(user_input)
#       ↓
#   LLM generates JSON {command, backup, recovery, risk}
#       ↓
#   validate against JSONL registry (local file or HF Dataset)
#       ↓
#   return confirmation request to user (NEVER auto-execute!)
#       ↓
#   user confirms → send to agent → return output
#       ↓
#   store recovery plan in db_sync
#
# CONFIRMATION FLOW:
#   Every command requires explicit user confirmation.
#   High-risk commands require backup + recovery plan from LLM.
#   Recovery plan is stored in db_sync hub_state for later use.
# =============================================================================

import json
import logging
import os
import httpx
from pathlib import Path
from typing import Optional

from . import config
from . import db_sync

logger = logging.getLogger("shellmaster")

# =============================================================================
# HARDCODED BLOCKS — cannot be overridden by registry or LLM
# Both Unix and Windows covered
# =============================================================================
_HARDCODED_BLOCKED = {
    # ── Unix destructive ──────────────────────────────────────────────────────
    "rm -rf", "rm -r /", "rm -f /",
    "mkfs", "dd if=", "> /dev/sd", "> /dev/hd",
    "shred /dev/", "wipefs",
    # ── Windows destructive ───────────────────────────────────────────────────
    "format c:", "format d:", "format /fs",
    "rmdir /s", "rd /s", "del /f /s /q c:\\",
    # ── Unix system destruction ───────────────────────────────────────────────
    ":(){ :|:& };:",
    "chmod -r 777 /", "chmod -r 000 /", "chmod 000 /",
    "chown -r root /", "chown -r 0:0 /",
    # ── Windows system destruction ────────────────────────────────────────────
    "icacls c:\\ /grant", "icacls c:\\ /deny",
    "takeown /f c:\\",
    "reg delete hklm", "reg delete hkcu",
    # ── Unix privilege escalation ─────────────────────────────────────────────
    "sudo su", "sudo -i", "sudo -s",
    "su root", "su -", "passwd root", "visudo",
    # ── Windows privilege escalation ──────────────────────────────────────────
    "runas /user:administrator",
    "net user administrator",
    "net localgroup administrators",
    "psexec -s",
    # ── Critical system files — Unix ─────────────────────────────────────────
    "/etc/passwd", "/etc/shadow", "/etc/sudoers",
    "/etc/crontab", "/boot/", "/proc/sysrq",
    # ── Critical system files — Windows ──────────────────────────────────────
    "c:\\windows\\system32\\", "c:\\windows\\system\\",
    "hklm\\system", "hklm\\security",
    # ── Shutdown/reboot — Unix ────────────────────────────────────────────────
    "reboot", "halt", "poweroff",
    "init 0", "init 6",
    "systemctl poweroff", "systemctl reboot", "systemctl halt",
    # ── Shutdown/reboot — Windows ─────────────────────────────────────────────
    "shutdown /s", "shutdown /r", "shutdown /h",
    # ── Network destruction ───────────────────────────────────────────────────
    "iptables -f", "iptables --flush", "ufw disable",
    "netsh firewall set opmode disable",
    "netsh advfirewall set allprofiles state off",
}


def _is_hardblocked(cmd_str: str) -> bool:
    """Check if command contains any hardcoded blocked pattern."""
    cmd_lower = cmd_str.lower().strip()
    return any(blocked in cmd_lower for blocked in _HARDCODED_BLOCKED)


# =============================================================================
# Module State — populated by initialize()
# =============================================================================
_cfg         = {}
_agent_url   = ""
_token       = ""
_cmd_file    = Path("shellmaster_commands.jsonl")
_timeout     = 30
_initialized = False


# =============================================================================
# Initialization — called by app/app.py
# =============================================================================

async def initialize() -> None:
    """
    Initialize ShellMaster from app/.pyfun [TOOL.shellmaster].
    Called by app/app.py — never reads os.environ directly.
    """
    global _cfg, _agent_url, _token, _cmd_file, _timeout, _initialized

    tools_cfg = config.get_active_tools()
    _cfg      = tools_cfg.get("shellmaster", {})

    if not _cfg or _cfg.get("active", "false").lower() != "true":
        logger.info("ShellMaster not active in .pyfun — skipped")
        return

    _agent_url = _cfg.get("shellmaster_agent_url", "http://localhost:5004")
    # Token: ENV zuerst (shellmaster_env_key → .env, Guardian lädt ENV) —
    # das .pyfun-Feld ist nur noch Fallback. Secrets gehören NICHT in .pyfun
    # (gleiche Regel wie bei allen Providern: env_key = NAME, Wert in .env).
    env_key  = _cfg.get("shellmaster_env_key", "")
    _token   = os.getenv(env_key, "") if env_key else ""
    if not _token:
        _token = _cfg.get("shellmaster_token", "")
    _timeout   = int(_cfg.get("timeout_sec", "30"))

    # Command registry — local file first
    cmd_file_path = _cfg.get("shellmaster_commands_file", "shellmaster_commands.jsonl")
    _cmd_file     = Path(cmd_file_path)

    if not _token:
        logger.warning("ShellMaster: shellmaster_token not set in .pyfun — disabled")
        return

    if not _cmd_file.exists():
        logger.warning(f"ShellMaster: commands file not found: {_cmd_file} — disabled")
        return

    allowed = sum(
        1 for line in _cmd_file.open()
        if line.strip() and json.loads(line).get("allowed", False)
    )
    logger.info(f"ShellMaster initialized | agent: {_agent_url} | allowed commands: {allowed}")
    _initialized = True


# =============================================================================
# Command Registry
# =============================================================================

def _load_registry() -> dict:
    """
    Load command registry from local JSONL.
    Hot-reloaded on each call — no restart needed for registry changes.
    TODO: merge with HF Dataset registry if shellmaster_commands_dataset is set.
    """
    registry = {}
    try:
        with open(_cmd_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                cmd = json.loads(line)
                registry[cmd["id"]] = cmd
    except Exception as e:
        logger.error(f"Registry load failed: {type(e).__name__}: {e}")
    return registry


def _validate_command(cmd_str: str) -> tuple[bool, str]:
    """
    Validate a command string against registry + hardblock list.

    Returns:
        (True, command_id) if valid and allowed
        (False, reason)    if blocked
    """
    # Hardblock first — no exceptions
    if _is_hardblocked(cmd_str):
        return False, "blocked by system policy"

    # Match against registry
    registry = _load_registry()
    cmd_lower = cmd_str.lower().strip()

    for cmd_id, cmd in registry.items():
        # Check both unix and win templates
        for os_key in ("unix", "win"):
            template_base = cmd.get(os_key, "").split()[0].lower()
            if template_base and cmd_lower.startswith(template_base):
                if not cmd.get("allowed", False):
                    return False, f"command '{cmd_id}' is blocked in registry"
                return True, cmd_id

    return False, f"command not found in registry: {cmd_str[:50]}"


def list_commands(category: str = None) -> list:
    """List allowed commands, optionally filtered by category."""
    cmds = [c for c in _load_registry().values() if c.get("allowed", False)]
    if category:
        cmds = [c for c in cmds if c.get("category") == category]
    return cmds


# =============================================================================
# Confirmation Flow
# =============================================================================

def build_confirmation(llm_result: dict) -> str:
    """
    Build confirmation message from LLM-generated command plan.
    Returns formatted string for user to confirm/deny.
    """
    command  = llm_result.get("command", "")
    backup   = llm_result.get("backup", "")
    recovery = llm_result.get("recovery", "")
    risk     = llm_result.get("risk", "unknown").upper()

    lines = [
        f"⚠️  ShellMaster — Confirmation Required",
        f"",
        f"Command:  {command}",
        f"Risk:     {risk}",
    ]

    if backup:
        lines.append(f"Backup:   {backup}")
    if recovery:
        lines.append(f"Recovery: {recovery}")

    lines += [
        f"",
        f"Type 'confirm shellmaster' to execute or 'cancel' to abort.",
    ]
    return "\n".join(lines)


async def store_recovery_plan(command: str, backup: str, recovery: str) -> None:
    """Store recovery plan in db_sync hub_state for later use."""
    try:
        await db_sync.write("shellmaster.last_command", {
            "command":  command,
            "backup":   backup,
            "recovery": recovery,
        })
        logger.info("ShellMaster: recovery plan stored in db_sync")
    except Exception as e:
        logger.warning(f"ShellMaster: recovery plan store failed: {type(e).__name__}")


async def get_recovery_plan() -> dict:
    """Retrieve last recovery plan from db_sync."""
    return await db_sync.read("shellmaster.last_command", default={})


# =============================================================================
# Agent Execution — only called after explicit user confirmation
# =============================================================================

async def execute_confirmed(command: str) -> str:
    """
    Execute a command on the local agent.
    ONLY called after explicit user confirmation — never auto-execute!

    Args:
        command: Raw shell command string (already validated).

    Returns:
        Command output or error message.
    """
    if not _initialized:
        return "ShellMaster not initialized."

    # Final hardblock check before sending to agent
    if _is_hardblocked(command):
        logger.warning(f"HARDBLOCK on confirmed command: {command[:60]}")
        return "Blocked by system policy — cannot execute."

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{_agent_url}/command",
                json={"command": command},
                headers={
                    "Authorization": f"Bearer {_token}",
                    "Content-Type":  "application/json",
                },
                timeout=_timeout,
            )
            r.raise_for_status()
            data = r.json()
            output = data.get("result", data.get("output", ""))
            logger.info(f"ShellMaster executed: {command[:60]}")
            return output

    except httpx.ConnectError:
        return f"ShellMaster Agent not reachable at {_agent_url} — is it running?"
    except httpx.HTTPStatusError as e:
        return f"Agent error: HTTP {e.response.status_code}"
    except Exception as e:
        logger.warning(f"ShellMaster execute failed: {type(e).__name__}: {e}")
        return f"Error: {type(e).__name__}"


def is_ready() -> bool:
    return _initialized


def get_config() -> dict:
    return _cfg