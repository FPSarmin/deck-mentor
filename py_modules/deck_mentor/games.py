"""Locate a game's install folder and save files, and copy saves for analysis."""

from __future__ import annotations

import glob
import os
import re
import shutil
import time

from .util import user_home

_VDF_PAIR = re.compile(r'"([^"]+)"\s+"([^"]*)"')

# Folders inside a Proton prefix that never hold saves but can be huge.
_SKIP_DIRS = re.compile(
    r"^(temp|tmp|cache|caches|.*cache|shadercache|d3dscache|nvidia|amd|microsoft|"
    r"crashes|crashreport.*|crashdumps|logs?|webcache|cef|gpucache|htmlcache|"
    r"steam|unity|epic.*|ubisoft game launcher|origin|ea desktop|package cache)$",
    re.IGNORECASE,
)
# Places inside steamuser where games keep saves and settings.
_PREFIX_ROOTS = [
    "AppData/Local", "AppData/LocalLow", "AppData/Roaming",
    "Documents", "Saved Games", "My Documents",
]


def steam_roots() -> list[str]:
    home = user_home()
    roots = []
    for cand in (os.path.join(home, ".local/share/Steam"), os.path.join(home, ".steam/steam")):
        real = os.path.realpath(cand)
        if os.path.isdir(os.path.join(real, "steamapps")) and real not in roots:
            roots.append(real)
    return roots


def library_paths() -> list[str]:
    libs: list[str] = []
    for root in steam_roots():
        if root not in libs:
            libs.append(root)
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, encoding="utf-8", errors="ignore") as fh:
                for key, value in _VDF_PAIR.findall(fh.read()):
                    if key == "path":
                        real = os.path.realpath(value)
                        if os.path.isdir(os.path.join(real, "steamapps")) and real not in libs:
                            libs.append(real)
        except OSError:
            pass
    return libs


def install_dir(appid: int) -> str | None:
    for lib in library_paths():
        manifest = os.path.join(lib, "steamapps", f"appmanifest_{appid}.acf")
        try:
            with open(manifest, encoding="utf-8", errors="ignore") as fh:
                pairs = dict(_VDF_PAIR.findall(fh.read()))
        except OSError:
            continue
        folder = pairs.get("installdir")
        if folder:
            path = os.path.join(lib, "steamapps", "common", folder)
            if os.path.isdir(path):
                return path
    return None


def prefix_user_dir(appid: int) -> str | None:
    for lib in library_paths():
        path = os.path.join(lib, "steamapps", "compatdata", str(appid), "pfx", "drive_c", "users", "steamuser")
        if os.path.isdir(path):
            return path
    return None


def cloud_dirs(appid: int) -> list[str]:
    dirs = []
    for root in steam_roots():
        dirs.extend(d for d in glob.glob(os.path.join(root, "userdata", "*", str(appid))) if os.path.isdir(d))
    return dirs


def _copy_tree(src: str, dst: str, budget: list[int], max_file: int, log: list[str]) -> None:
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if not _SKIP_DIRS.match(d) and not os.path.islink(os.path.join(root, d))]
        rel = os.path.relpath(root, src)
        for name in files:
            path = os.path.join(root, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size > max_file or size > budget[0] or os.path.islink(path):
                if size > max_file:
                    log.append(f"skipped (too big, {size // 1_000_000} MB): {path}")
                continue
            target_dir = os.path.join(dst, rel)
            os.makedirs(target_dir, exist_ok=True)
            try:
                shutil.copy2(path, os.path.join(target_dir, name))
                budget[0] -= size
            except OSError as exc:
                log.append(f"copy failed: {path}: {exc}")


def copy_saves(appid: int, dest: str, max_total: int = 300_000_000, max_file: int = 64_000_000) -> dict:
    """Copy everything that looks like saves/settings for `appid` into `dest`.

    Returns a summary with the source folders, so the model can refer to them.
    The originals are never handed to the model.
    """
    os.makedirs(dest, exist_ok=True)
    budget = [max_total]
    log: list[str] = []
    sources: list[tuple[str, str]] = []

    prefix = prefix_user_dir(appid)
    if prefix:
        for sub in _PREFIX_ROOTS:
            src = os.path.join(prefix, sub)
            if os.path.isdir(src):
                target = os.path.join(dest, "proton", sub.replace("/", "_"))
                _copy_tree(src, target, budget, max_file, log)
                sources.append((target, src))

    for i, cloud in enumerate(cloud_dirs(appid)):
        target = os.path.join(dest, f"steam_cloud_{i}")
        _copy_tree(cloud, target, budget, max_file, log)
        sources.append((target, cloud))

    # Drop empty folders so the model is not distracted by them.
    for root, dirs, files in os.walk(dest, topdown=False):
        if root != dest and not dirs and not files:
            try:
                os.rmdir(root)
            except OSError:
                pass

    with open(os.path.join(dest, "SOURCES.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"Copied {time.strftime('%Y-%m-%d %H:%M')} for Steam appid {appid}.\n")
        fh.write("These are COPIES. Originals (do not touch):\n")
        for target, src in sources:
            fh.write(f"  {os.path.relpath(target, dest)}  <-  {src}\n")
        if not sources:
            fh.write("  (no Proton prefix or Steam Cloud folder found; this may be a native Linux game)\n")
        for line in log[:50]:
            fh.write(line + "\n")

    return {
        "copied_bytes": max_total - budget[0],
        "sources": [src for _t, src in sources],
        "prefix": prefix,
    }
