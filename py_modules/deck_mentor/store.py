"""Settings and per-game state, stored as small JSON files."""

from __future__ import annotations

import json
import os
import tempfile
import time

DEFAULT_SETTINGS: dict = {
    "auth_mode": "subscription",   # subscription | api_key
    "api_key": "",
    "claude_path": "",
    "model": "opus",                # opus | sonnet | haiku
    "effort": "low",                # low | medium | high
    "spoiler": "balanced",          # hint | balanced | full
    "web_search": True,
    "hotkey_enabled": True,
    "hotkey_combo": "L4+R4",
    "hotkey_question": "Что мне сейчас делать? Подскажи по скриншоту.",
    "keep_days": 0,                 # 0 = keep chat transcripts forever
    "max_storage_mb": 0,            # 0 = no size cap on transcripts
    "deep_max_turns": 40,
}

HISTORY_LIMIT = 2000  # Q&A text per game; tiny, kept for good
# Every resumed turn re-sends earlier screenshots, so start a fresh session now and then.
SESSION_MAX_TURNS = 16
SESSION_MAX_SHOTS = 6


def _write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


class Settings:
    def __init__(self, settings_dir: str) -> None:
        self.path = os.path.join(settings_dir, "settings.json")
        self.data = {**DEFAULT_SETTINGS, **_read_json(self.path)}

    def update(self, patch: dict) -> dict:
        for key, value in patch.items():
            if key in DEFAULT_SETTINGS:
                self.data[key] = value
        _write_json(self.path, self.data)
        return self.data

    def public(self) -> dict:
        data = dict(self.data)
        data["api_key"] = ("••••" + data["api_key"][-4:]) if data.get("api_key") else ""
        return data

    def __getitem__(self, key: str):
        return self.data.get(key, DEFAULT_SETTINGS.get(key))


class GameState:
    def __init__(self, games_dir: str, appid: int) -> None:
        self.appid = appid
        self.dir = os.path.join(games_dir, str(appid))
        self.path = os.path.join(self.dir, "state.json")
        self.data = {"session_id": None, "turns": 0, "shots": 0, "notes": "", "history": [],
                     "sessions": [], **_read_json(self.path)}

    @property
    def guide_path(self) -> str:
        return os.path.join(self.dir, "guide.md")

    def guide(self) -> str:
        try:
            with open(self.guide_path, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    def save(self) -> None:
        _write_json(self.path, self.data)

    def needs_new_session(self) -> bool:
        return (not self.data.get("session_id")
                or self.data.get("turns", 0) >= SESSION_MAX_TURNS
                or self.data.get("shots", 0) >= SESSION_MAX_SHOTS)

    def start_session(self, session_id: str) -> None:
        self.data.update(session_id=session_id, turns=0, shots=0)
        sessions = self.data.setdefault("sessions", [])
        sessions.append(session_id)
        del sessions[:-50]

    def record(self, question: str, answer: str, shot: bool, mode: str, ok: bool) -> dict:
        entry = {"q": question, "a": answer, "shot": shot, "mode": mode, "ok": ok, "ts": int(time.time())}
        history = self.data.setdefault("history", [])
        history.append(entry)
        del history[:-HISTORY_LIMIT]
        return entry

    def reset_chat(self) -> None:
        """Start a fresh conversation for Claude; the visible history is kept."""
        self.data.update(session_id=None, turns=0, shots=0)
