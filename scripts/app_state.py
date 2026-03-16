#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path


APP_STATE_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "codex-any-node-fork"
GUI_STATE_PATH = APP_STATE_DIR / "gui-state.json"
TRANSFER_STATE_PATH = APP_STATE_DIR / "account-session-map.json"


def load_gui_state(
    *,
    normalize_workdir,
    max_remembered_workdirs: int,
) -> dict[str, object]:
    if not GUI_STATE_PATH.exists():
        return {"last_workdir": "", "recent_workdirs": [], "minimize_to_tray_on_close": False}
    try:
        data = json.loads(GUI_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"last_workdir": "", "recent_workdirs": [], "minimize_to_tray_on_close": False}
    if not isinstance(data, dict):
        return {"last_workdir": "", "recent_workdirs": [], "minimize_to_tray_on_close": False}

    recent = data.get("recent_workdirs")
    workdir_candidates: list[object] = []
    last_workdir = data.get("last_workdir")
    if isinstance(last_workdir, str):
        workdir_candidates.append(last_workdir)
    if isinstance(recent, list):
        workdir_candidates.extend(recent)

    seen: set[str] = set()
    remembered: list[str] = []
    for value in workdir_candidates:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text:
            continue
        normalized = normalize_workdir(text)
        path_value = Path(normalized)
        if not path_value.exists() or not path_value.is_dir():
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        remembered.append(normalized)
        if len(remembered) >= max_remembered_workdirs:
            break

    return {
        "last_workdir": remembered[0] if remembered else "",
        "recent_workdirs": remembered,
        "minimize_to_tray_on_close": bool(data.get("minimize_to_tray_on_close")),
    }


def save_gui_state(
    *,
    last_workdir: str,
    recent_workdirs: list[str],
    minimize_to_tray_on_close: bool,
    max_remembered_workdirs: int,
) -> None:
    payload = {
        "last_workdir": last_workdir,
        "recent_workdirs": recent_workdirs[:max_remembered_workdirs],
        "minimize_to_tray_on_close": minimize_to_tray_on_close,
    }
    APP_STATE_DIR.mkdir(parents=True, exist_ok=True)
    GUI_STATE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_transfer_state(state_path: Path | None = None) -> dict[str, object]:
    path = state_path or TRANSFER_STATE_PATH
    if not path.exists():
        return {"version": 1, "thread_assignments": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "thread_assignments": {}}
    if not isinstance(data, dict):
        return {"version": 1, "thread_assignments": {}}
    assignments = data.get("thread_assignments")
    if not isinstance(assignments, dict):
        assignments = {}
    return {"version": 1, "thread_assignments": assignments}


def save_transfer_state(state: dict[str, object], state_path: Path | None = None) -> None:
    path = state_path or TRANSFER_STATE_PATH
    payload = {
        "version": 1,
        "thread_assignments": state.get("thread_assignments", {}),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
