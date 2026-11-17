"""Small helpers shared by the backend modules (stdlib only, Python 3.11)."""

from __future__ import annotations

import os
import shutil

# Decky Loader is a PyInstaller bundle: it injects its own libraries into the
# environment, which breaks system binaries such as gst-launch-1.0 or claude.
_BUNDLE_VARS = ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH", "_MEIPASS", "_MEIPASS2")


def user_home() -> str:
    return os.environ.get("DECKY_USER_HOME") or os.path.expanduser("~")


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for running system binaries from inside Decky."""
    env = dict(os.environ)
    for key in _BUNDLE_VARS:
        env.pop(key, None)
    orig = os.environ.get("LD_LIBRARY_PATH_ORIG")
    if orig:
        env["LD_LIBRARY_PATH"] = orig

    home = user_home()
    env["HOME"] = home
    run_dir = f"/run/user/{os.getuid()}"
    env.setdefault("XDG_RUNTIME_DIR", run_dir)
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={run_dir}/bus")

    path_parts = [os.path.join(home, ".local", "bin"), "/usr/local/bin", "/usr/bin", "/bin"]
    for part in env.get("PATH", "").split(":"):
        if part and part not in path_parts:
            path_parts.append(part)
    env["PATH"] = ":".join(path_parts)

    if extra:
        env.update(extra)
    return env


def which(name: str, env: dict[str, str] | None = None) -> str | None:
    env = env or clean_env()
    return shutil.which(name, path=env.get("PATH"))


def dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def remove_path(path: str) -> int:
    """Delete a file or directory tree, returning the number of bytes freed."""
    if not os.path.lexists(path):
        return 0
    if os.path.isdir(path) and not os.path.islink(path):
        size = dir_size(path)
        shutil.rmtree(path, ignore_errors=True)
        return size
    try:
        size = os.lstat(path).st_size
        os.remove(path)
        return size
    except OSError:
        return 0
