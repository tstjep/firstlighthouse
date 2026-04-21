"""
Background run manager for salesintel agents.

Launches agent subprocesses, writes output to a per-run log file,
and tracks run state in data/<campaign_id>/run_state.json.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR    = Path(__file__).resolve().parent / "data"
PROJECT_DIR = Path(__file__).resolve().parent
VENV_PYTHON = PROJECT_DIR / "venv" / "bin" / "python"
AGENTS_DIR  = PROJECT_DIR / "agents"

# One lock per campaign_id for state file writes.
_state_locks: dict[str, threading.Lock] = {}
_state_locks_mu = threading.Lock()


def _state_lock(campaign_id: str) -> threading.Lock:
    with _state_locks_mu:
        if campaign_id not in _state_locks:
            _state_locks[campaign_id] = threading.Lock()
        return _state_locks[campaign_id]


# ── Run steps ────────────────────────────────────────────────────────────────

STEPS: dict[str, str] = {
    "search":   "search_agent.py",
    "enrich":   "enrich_agent.py",
    "signals":  "signal_agent.py",
    "rating":   "rating_agent.py",
    "contacts": "contact_agent.py",
}

MODES: dict[str, dict] = {
    "test": {
        "label":       "Quick test",
        "description": "Search only — finds up to 5 companies to verify your config.",
        "steps":       ["search"],
        "extra_args":  {"search": ["--max-rows", "5"]},
    },
    "search": {
        "label":       "Search only",
        "description": "Discover companies and save them to results.",
        "steps":       ["search"],
        "extra_args":  {},
    },
    "enrich": {
        "label":       "Search + Enrich",
        "description": "Find companies and fill in website, size, HQ, and notes.",
        "steps":       ["search", "enrich"],
        "extra_args":  {},
    },
    "score": {
        "label":       "Search + Enrich + Signals + Rating",
        "description": "Full discovery and scoring pipeline. Stops before contact finding.",
        "steps":       ["search", "enrich", "signals", "rating"],
        "extra_args":  {},
    },
    "full": {
        "label":       "Full run",
        "description": "Everything: discover, enrich, score, then find contacts for top-rated companies.",
        "steps":       ["search", "enrich", "signals", "rating", "contacts"],
        "extra_args":  {},
    },
}


# ── State ─────────────────────────────────────────────────────────────────────

@dataclass
class RunState:
    run_id:       str
    campaign_id:  str
    segment:      str
    mode:         str
    steps:        list[str]
    status:       str          # "running" | "done" | "error"
    current_step: str = ""
    started_at:   str = ""
    finished_at:  str = ""
    error:        str = ""
    log_path:     str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _state_path(campaign_id: str) -> Path:
    return DATA_DIR / campaign_id / "run_state.json"


def _log_path(campaign_id: str, run_id: str) -> Path:
    return DATA_DIR / campaign_id / "logs" / f"{run_id}.log"


def load_state(campaign_id: str) -> RunState | None:
    path = _state_path(campaign_id)
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        # Validate required keys exist before constructing
        return RunState(**{k: d[k] for k in RunState.__dataclass_fields__ if k in d})
    except Exception as exc:
        logger.warning("Failed to load run state for %s: %s", campaign_id, exc)
        return None


def _save_state(state: RunState) -> None:
    path = _state_path(state.campaign_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _state_lock(state.campaign_id):
        path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")


def tail_log(campaign_id: str, run_id: str, lines: int = 200) -> list[str]:
    path = _log_path(campaign_id, run_id)
    if not path.exists():
        return []
    text = path.read_text(errors="replace", encoding="utf-8")
    return text.splitlines()[-lines:]


def is_running(campaign_id: str) -> bool:
    state = load_state(campaign_id)
    return state is not None and state.status == "running"


# ── Validation ────────────────────────────────────────────────────────────────

def validate_run_request(campaign_id: str, segment: str, mode: str,
                          valid_segments: list[str]) -> list[str]:
    errors: list[str] = []
    if mode not in MODES:
        errors.append(f"Unknown mode '{mode}'. Valid: {', '.join(MODES)}")
    if segment not in valid_segments:
        errors.append(f"Segment '{segment}' not found in campaign. Valid: {', '.join(valid_segments)}")
    if is_running(campaign_id):
        errors.append("A run is already in progress for this campaign.")
    return errors


# ── Launch ────────────────────────────────────────────────────────────────────

def start_run(campaign_id: str, segment: str, mode: str) -> RunState:
    """Launch a background run. Caller must validate inputs first via validate_run_request."""
    mode_cfg = MODES[mode]
    steps    = mode_cfg["steps"]

    if not steps:
        raise ValueError(f"Mode '{mode}' has no steps configured.")

    run_id   = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_file = _log_path(campaign_id, run_id)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    state = RunState(
        run_id=run_id,
        campaign_id=campaign_id,
        segment=segment,
        mode=mode,
        steps=list(steps),
        status="running",
        current_step=steps[0],
        started_at=datetime.now().isoformat(timespec="seconds"),
        log_path=str(log_file),
    )
    _save_state(state)

    thread = threading.Thread(target=_run_steps, args=(state, mode_cfg), daemon=True)
    thread.start()
    return state


def _python_executable() -> str:
    return str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


def _run_steps(state: RunState, mode_cfg: dict) -> None:
    log_file = Path(state.log_path)
    python   = _python_executable()
    env      = {**os.environ, "PYTHONPATH": str(PROJECT_DIR)}

    def _finish(status: str, error: str = "") -> None:
        state.status      = status
        state.error       = error
        state.current_step = ""
        state.finished_at = datetime.now().isoformat(timespec="seconds")
        _save_state(state)

    def write(msg: str) -> None:
        ts   = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        # avoid double newlines: strip trailing whitespace then add one newline
        log.write(line.rstrip() + "\n")
        log.flush()

    with log_file.open("w", encoding="utf-8") as log:
        write(f"=== Run {state.run_id} | campaign={state.campaign_id} "
              f"segment={state.segment} mode={state.mode} ===")
        write("")

        for step in mode_cfg["steps"]:
            script_name = STEPS.get(step)
            if not script_name:
                write(f"⚠  Unknown step '{step}', skipping.")
                continue

            script = AGENTS_DIR / script_name
            if not script.exists():
                write(f"⚠  Agent script not found: {script}")
                write(f"   Step '{step}' skipped — run 'git pull' or check agents/ directory.")
                continue

            state.current_step = step
            _save_state(state)
            write(f"▶ Step: {step}")

            extra = mode_cfg.get("extra_args", {}).get(step, [])
            cmd = [python, str(script),
                   "--campaign", state.campaign_id,
                   "--tab",      state.segment,
                   *extra]
            write(f"  $ {' '.join(cmd)}")

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    cwd=str(PROJECT_DIR),
                )
                try:
                    for line in proc.stdout:
                        log.write(line)
                        log.flush()
                finally:
                    proc.stdout.close()
                    proc.wait()

            except OSError as exc:
                write(f"✗ Failed to launch '{step}': {exc}")
                _finish("error", f"Could not launch step '{step}': {exc}")
                return

            if proc.returncode != 0:
                write(f"✗ Step '{step}' exited with code {proc.returncode}.")
                _finish("error", f"Step '{step}' failed (exit {proc.returncode})")
                return

            write(f"✓ Step '{step}' complete.")
            write("")

        write("=== All steps complete ===")
        _finish("done")
