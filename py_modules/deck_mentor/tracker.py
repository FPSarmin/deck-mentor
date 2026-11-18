"""Progress tracker: a per-game script Claude writes once, run by the plugin on a fresh save copy.

Claude writes ./tools/tracker.py in the game's workspace during a "tracker" request. After that,
pressing the tracker button copies the saves, runs the script with the system python3 (as the same
user Claude's own scripts run as) and shows its JSON: what is collected nearby, what is missed,
what must not be missed. No Claude request, no subscription quota.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time

from .util import clean_env

SCRIPT = os.path.join("tools", "tracker.py")
DATA_FILE = "tracker.json"
STATUSES = ("done", "missing", "missable", "lost", "unknown")
RUN_TIMEOUT = 60
MAX_OUTPUT = 2_000_000
MAX_GROUPS = 60
MAX_ITEMS = 600


class TrackerError(RuntimeError):
    pass


def script_path(workdir: str) -> str:
    return os.path.join(workdir, SCRIPT)


def has_script(workdir: str) -> bool:
    return os.path.isfile(script_path(workdir))


def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def sanitize(raw) -> dict:
    """Keep only the fields the pages show, with sane sizes, whatever the script printed."""
    if not isinstance(raw, dict):
        raise TrackerError("Трекер вывел не то: ожидался объект JSON.")
    groups, total = [], 0
    for g in raw.get("groups") or []:
        if not isinstance(g, dict) or len(groups) >= MAX_GROUPS:
            continue
        items = []
        for it in g.get("items") or []:
            if not isinstance(it, dict) or total >= MAX_ITEMS:
                continue
            name = _text(it.get("name"), 120)
            if not name:
                continue
            status = str(it.get("status") or "unknown").lower()
            items.append({
                "name": name,
                "kind": _text(it.get("kind"), 40),
                "status": status if status in STATUSES else "unknown",
                "where": _text(it.get("where"), 300),
                "note": _text(it.get("note"), 200),
            })
            total += 1
        title = _text(g.get("title"), 120)
        if title or items:
            groups.append({"title": title or "Без названия", "current": bool(g.get("current")), "items": items})
    groups.sort(key=lambda g: not g["current"])  # where the player is now goes first
    return {
        "location": _text(raw.get("location"), 160),
        "summary": _text(raw.get("summary"), 240),
        "groups": groups,
        "uncovered": [_text(a, 80) for a in (raw.get("uncovered") or []) if _text(a, 80)][:20],
    }


def counts(data: dict) -> dict:
    out = {s: 0 for s in STATUSES}
    for g in data.get("groups", []):
        for it in g["items"]:
            out[it["status"]] += 1
    return out


async def run(workdir: str, saves_dir: str, python: str | None = None) -> dict:
    """Run the game's tracker script on a save copy and return its sanitized result."""
    script = script_path(workdir)
    if not os.path.isfile(script):
        raise TrackerError("Для этой игры ещё нет трекера.")
    env = clean_env()
    python = python or shutil.which("python3", path=env.get("PATH"))
    if not python:
        raise TrackerError("На Деке не найден python3.")
    proc = await asyncio.create_subprocess_exec(
        python, "-E", "-s", script, saves_dir, cwd=workdir, env=env,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=RUN_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TrackerError(f"Трекер работал дольше {RUN_TIMEOUT} с и был остановлен.")
    if proc.returncode != 0:
        tail = err.decode(errors="replace").strip()[-600:]
        raise TrackerError(f"Трекер завершился с ошибкой: {tail or proc.returncode}")
    if len(out) > MAX_OUTPUT:
        raise TrackerError("Трекер вывел слишком много данных.")
    text = out.decode(errors="replace").strip()
    start = text.find("{")  # tolerate a stray print before the JSON
    try:
        raw = json.loads(text[start:] if start > 0 else text)
    except ValueError as exc:
        raise TrackerError(f"Трекер вывел не JSON: {exc}") from None
    data = sanitize(raw)
    data["updated"] = time.time()
    return data


def save(workdir: str, data: dict) -> None:
    path = os.path.join(workdir, DATA_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


def load(workdir: str) -> dict | None:
    try:
        with open(os.path.join(workdir, DATA_FILE), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) and "groups" in data else None
    except (OSError, ValueError):
        return None
