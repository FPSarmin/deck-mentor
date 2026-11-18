"""Find the game Steam is running right now by looking at its processes (read-only /proc).

Unofficial ports, emulators and other non-Steam games keep saves in folders named
after their executable ("soh", "2s2h") rather than after the Steam shortcut title,
or next to the executable or AppImage. Steam starts every game under a `reaper`
process with `AppId=<id>`, so the game's own processes are that reaper's children.
"""

from __future__ import annotations

import os
import re

from .util import user_home

# Launch plumbing, not the game itself.
_INFRA = re.compile(
    r"^(reaper|steam|steamwebhelper|steam-launch-wrapper|gameoverlayui|pressure-vessel.*|pv-.*|srt-.*|"
    r"bwrap|steam-runtime.*|python[0-9.]*|wine.*|wineserver|proton|gamescope.*|mangoapp|mangohud|"
    r"sh|bash|dash|env|timeout|xdg-.*|zenity|fossilize.*|ld-linux.*)$"
)
_STEM_SUFFIX = re.compile(r"(\.(appimage|elf|x86_64|x86-64|x64|bin|sh|exe|run|py))+$", re.IGNORECASE)
_ARCH_SUFFIX = re.compile(r"[-_. ]?(linux|x86[-_]?64|amd64|x64|v?\d+(\.\d+)+)$", re.IGNORECASE)


def _read(path: str) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return b""


def _link(path: str) -> str:
    try:
        return os.readlink(path)
    except OSError:
        return ""


def _processes(proc: str = "/proc") -> dict[int, tuple[int, int, list[str]]]:
    """pid -> (parent pid, start time in clock ticks, argv)."""
    out = {}
    for name in os.listdir(proc):
        if not name.isdigit():
            continue
        stat = _read(os.path.join(proc, name, "stat")).decode(errors="ignore")
        tail = stat[stat.rfind(")") + 2:].split() if ")" in stat else []
        if len(tail) < 20:
            continue
        argv = [a for a in _read(os.path.join(proc, name, "cmdline")).decode(errors="ignore").split("\0") if a]
        out[int(name)] = (int(tail[1]), int(tail[19]), argv)
    return out


def _env(pid: int, proc: str) -> dict[str, str]:
    env = {}
    for item in _read(os.path.join(proc, str(pid), "environ")).decode(errors="ignore").split("\0"):
        key, sep, value = item.partition("=")
        if sep and key in ("APPIMAGE", "OWD", "ARGV0"):
            env[key] = value
    return env


def stem(path: str) -> str:
    """'~/Applications/Ship-of-Harkinian-x86_64.AppImage' -> 'Ship-of-Harkinian'."""
    base = os.path.basename(path.rstrip("/"))
    base = _STEM_SUFFIX.sub("", base)
    for _ in range(3):
        base = _ARCH_SUFFIX.sub("", base)
    return base


def game_processes(appid: int, proc: str = "/proc") -> list[dict]:
    """The running game's processes: [{pid, exe, cwd, argv, appimage, owd, infra}]."""
    try:
        table = _processes(proc)
    except OSError:
        return []
    reapers = [pid for pid, (_pp, _st, argv) in table.items()
               if argv and "SteamLaunch" in argv and os.path.basename(argv[0]) == "reaper"]
    roots = [pid for pid in reapers if appid and f"AppId={appid}" in table[pid][2]]
    if not roots and reapers:
        roots = [max(reapers, key=lambda p: table[p][1])]  # the game started last
    children: dict[int, list[int]] = {}
    for pid, (ppid, _st, _argv) in table.items():
        children.setdefault(ppid, []).append(pid)
    found, queue = [], list(roots)
    while queue and len(found) < 64:
        pid = queue.pop(0)
        queue.extend(children.get(pid, []))
        exe = _link(os.path.join(proc, str(pid), "exe"))
        env = _env(pid, proc)
        found.append({
            "pid": pid,
            "exe": exe,
            "cwd": _link(os.path.join(proc, str(pid), "cwd")),
            "argv": table[pid][2][:6],
            "appimage": env.get("APPIMAGE", ""),
            "owd": env.get("OWD", ""),
            "infra": bool(_INFRA.match(os.path.basename(exe or (table[pid][2] or [""])[0]))),
        })
    return found


def _usable_dir(path: str, home: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    real = os.path.realpath(path)
    if real in (home, "/", os.path.realpath(home)):
        return False
    if not (real.startswith(home + os.sep) or real.startswith("/run/media/")):
        return False
    low = real.lower()
    return not any(part in low for part in (
        "/.local/share/steam", "/.steam/", "/steamapps/common/proton", "/steamapps/common/steamlinuxruntime",
        "/compatibilitytools.d/", "/emulation/tools/launchers",
    ))


def hints(appid: int, proc: str = "/proc") -> dict:
    """Names and folders that point to the running game's files, plus a short process summary."""
    home = os.path.realpath(user_home())
    names: list[str] = []
    dirs: list[str] = []
    summary: list[str] = []

    def add_name(value: str) -> None:
        s = stem(value)
        if s and s not in names and not _INFRA.match(s.lower()):
            names.append(s)

    def add_dir(path: str) -> None:
        if _usable_dir(path, home):
            real = os.path.realpath(path)
            if real not in dirs:
                dirs.append(real)

    for p in game_processes(appid, proc):
        if p["appimage"]:
            add_name(p["appimage"])
            add_dir(os.path.dirname(p["appimage"]))
        argv = p["argv"]
        # Launch scripts (EmuDeck and similar): `bash ~/Emulation/tools/launchers/soh.sh`
        for arg in argv[1:3]:
            if arg.endswith(".sh") or arg.lower().endswith(".appimage"):
                add_name(arg)
                if arg.lower().endswith(".appimage"):
                    add_dir(os.path.dirname(arg))
        if p["infra"]:
            continue
        exe = p["exe"]
        if exe and not exe.startswith(("/tmp/.mount_", "/usr/", "/bin/", "/lib", "/opt/")):
            add_name(exe)
            add_dir(os.path.dirname(exe))
        elif exe.startswith("/tmp/.mount_"):
            add_name(exe)  # inside a mounted AppImage the binary name is still telling
        add_dir(p["cwd"])
        summary.append(f"pid {p['pid']}: exe={exe or '?'} cwd={p['cwd'] or '?'} argv={' '.join(argv)[:200]}")
    return {"names": names, "dirs": dirs, "processes": summary[:12]}
