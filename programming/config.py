"""Where the mod keeps its files, and its settings and per-user preferences.

Everything lives in MOD_DATA_DIR (the core gives each mod one, kept across
updates; standalone it defaults to ./data):

    settings.json   the Terminal's limits (Settings page)
    prefs.json      per user: font size, the extra keys row, the last open
                    terminal, copy on select, the Claude sessions project filter
    .secret         standalone only: signs the login cookie
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict

from programming import terminal

_lock = threading.Lock()

# What the Settings page can change, with bounds. The Terminal used to read
# these from Odysseus's .env (ODYSSEUS_TERMINAL_*); a mod does not see that
# file, so they are settings now.
SETTINGS = {
    "grace_minutes": (1, 24 * 60, 10),        # a detached terminal is ended after this
    "max_sessions": (1, 32, 8),               # per user
    "scrollback_kb": (16, 4096, 200),         # replayed when a page reattaches
}
PREF_KEYS = {"fontSize": (int, 9, 28), "keys": (bool,), "active": (str,), "copyOnSelect": (bool,),
             "sessionsProject": (str,)}


def data_dir() -> str:
    d = os.environ.get("MOD_DATA_DIR") or os.path.join(os.getcwd(), "data")
    os.makedirs(d, exist_ok=True)
    return d


def _path(name: str) -> str:
    return os.path.join(data_dir(), name)


def _read(name: str) -> Dict[str, Any]:
    try:
        with open(_path(name), encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(name: str, value: Dict[str, Any]) -> None:
    tmp = _path(name) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, _path(name))


def _clamp(v, lo, hi, default) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def settings() -> Dict[str, int]:
    raw = _read("settings.json")
    return {k: _clamp(raw.get(k, d), lo, hi, d) for k, (lo, hi, d) in SETTINGS.items()}


def save_settings(new: Dict[str, Any]) -> Dict[str, int]:
    with _lock:
        cur = settings()
        for k, (lo, hi, d) in SETTINGS.items():
            if k in new:
                cur[k] = _clamp(new[k], lo, hi, cur[k])
        _write("settings.json", cur)
    apply()
    return cur


def apply() -> None:
    """Put the settings into the terminal module (read there at use time).
    Environment variables (PROGRAMMING_TERMINAL_*) win, for tests and for
    running it standalone."""
    s = settings()
    if not os.environ.get("PROGRAMMING_TERMINAL_GRACE_S"):
        terminal.GRACE_S = s["grace_minutes"] * 60
    if not os.environ.get("PROGRAMMING_TERMINAL_MAX_SESSIONS"):
        terminal.MAX_SESSIONS = s["max_sessions"]
    if not os.environ.get("PROGRAMMING_TERMINAL_SCROLLBACK"):
        terminal.SCROLLBACK_BYTES = s["scrollback_kb"] * 1000


def prefs(user: str) -> Dict[str, Any]:
    v = _read("prefs.json").get(user or "")
    return v if isinstance(v, dict) else {}


def save_prefs(user: str, new: Dict[str, Any]) -> Dict[str, Any]:
    with _lock:
        allp = _read("prefs.json")
        mine = dict(allp.get(user or "") or {})
        for k, spec in PREF_KEYS.items():
            if k not in new:
                continue
            v = new[k]
            if v is None:
                mine.pop(k, None)
            elif spec[0] is bool and isinstance(v, bool):
                mine[k] = v
            elif spec[0] is int and isinstance(v, int) and not isinstance(v, bool):
                mine[k] = max(spec[1], min(spec[2], v))
            elif spec[0] is str and isinstance(v, str):
                mine[k] = v[:200]
        allp[user or ""] = mine
        if len(allp) > 1000:      # a bound, not a real limit
            allp = dict(list(allp.items())[-1000:])
        _write("prefs.json", allp)
        return mine
