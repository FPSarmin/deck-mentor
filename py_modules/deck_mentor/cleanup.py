"""Keep the plugin's disk footprint small.

What grows:
- Claude Code transcripts (~/.claude/projects/<cwd>/<session>.jsonl). Each
  screenshot is stored inside them as base64, about 1-3 MB per question.
- ~/.claude/file-history/<session>/ backups made when Claude writes guide.md.
- Save copies and scratch scripts from a deep analysis.
"""

from __future__ import annotations

import glob
import os
import re
import time

from .util import dir_size, remove_path, user_home


def _encode(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def project_dirs(projects_dir: str, games_dir: str) -> list[str]:
    prefix = _encode(games_dir)
    return [p for p in glob.glob(os.path.join(projects_dir, "*"))
            if os.path.basename(p).startswith(prefix) and os.path.isdir(p)]


def default_projects_dir() -> str:
    return os.path.join(user_home(), ".claude", "projects")


def _session_items(project_dir: str) -> list[tuple[float, str, list[str]]]:
    """(mtime, session_id, paths) for every transcript in a project folder."""
    items = []
    for jsonl in glob.glob(os.path.join(project_dir, "*.jsonl")):
        sid = os.path.basename(jsonl)[:-6]
        paths = [jsonl]
        side_dir = os.path.join(project_dir, sid)
        if os.path.isdir(side_dir):
            paths.append(side_dir)
        try:
            mtime = os.path.getmtime(jsonl)
        except OSError:
            continue
        items.append((mtime, sid, paths))
    return items


def usage(projects_dir: str, games_dir: str) -> int:
    total = dir_size(games_dir) if os.path.isdir(games_dir) else 0
    for proj in project_dirs(projects_dir, games_dir):
        total += dir_size(proj)
    return total


def clean(projects_dir: str, games_dir: str, keep_days: float, max_bytes: int,
          active_sessions: set[str], known_sessions: set[str], everything: bool = False) -> int:
    """Delete scratch data and backups; transcripts only if a retention limit is set.

    keep_days <= 0 and max_bytes <= 0 mean "keep transcripts forever". Returns bytes freed.
    """
    freed = 0
    cutoff = time.time() - keep_days * 86400 if keep_days > 0 else 0.0
    file_history = os.path.join(os.path.dirname(projects_dir), "file-history")

    items = []
    for proj in project_dirs(projects_dir, games_dir):
        items.extend(_session_items(proj))
    items.sort()  # oldest first

    keep = []
    for mtime, sid, paths in items:
        if sid in active_sessions and not everything:
            keep.append((mtime, sid, paths))
            continue
        if everything or mtime < cutoff:
            for path in paths:
                freed += remove_path(path)
        else:
            keep.append((mtime, sid, paths))

    # Enforce the size cap, oldest transcripts first.
    sizes = {sid: sum(dir_size(p) if os.path.isdir(p) else os.path.getsize(p) for p in paths if os.path.exists(p))
             for _m, sid, paths in keep}
    total = sum(sizes.values())
    for _mtime, sid, paths in keep:
        if max_bytes <= 0 or total <= max_bytes:
            break
        if sid in active_sessions:
            continue
        for path in paths:
            freed += remove_path(path)
        total -= sizes[sid]

    # Rewind backups are useless to us; keep only the running session's.
    for sid in known_sessions | {sid for _m, sid, _p in items}:
        if sid and (everything or sid not in active_sessions):
            freed += remove_path(os.path.join(file_history, sid))

    # Leftovers of deep analyses.
    for game_dir in glob.glob(os.path.join(games_dir, "*")):
        for name in ("saves", "scratch"):
            freed += remove_path(os.path.join(game_dir, name))

    for proj in project_dirs(projects_dir, games_dir):
        try:
            if not os.listdir(proj):
                os.rmdir(proj)
        except OSError:
            pass
    return freed
