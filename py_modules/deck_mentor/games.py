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


def _norm(text: str) -> str:
    return re.sub(r"[^0-9a-zа-яё]", "", text.lower())


# Folders in ~/.local/share and ~/.config that belong to the system or to launchers, never to a game.
_NOT_GAMES = {
    "steam", "heroic", "lutris", "bottles", "applications", "fonts", "icons", "trash", "flatpak", "plasma",
    "konsole", "dolphin", "systemd", "vulkan", "mime", "keyrings", "recentlyused", "gvfsmetadata", "baloo",
    "kscreen", "kwin", "pulse", "pipewire", "wireplumber", "autostart", "gtk30", "gtk40", "dbus1", "decky",
    "homebrew", "mozilla", "chromium", "kdeconnect", "kxmlgui5", "akonadi", "emudeck", "steamrommanager",
}


def _matches(dirname: str, names: list[str]) -> bool:
    """Folder or file name vs. game name. Loose enough for "Game (USA)", strict enough to skip "Data"."""
    d = _norm(dirname)
    if len(d) < 4 or d in _NOT_GAMES:
        return False
    for n in names:
        if not n or len(n) < 4:
            continue
        if d == n or (n in d and len(n) * 2 >= len(d)) or (d in n and len(d) >= 6 and len(d) * 2 >= len(n)):
            return True
    return False


def _children(path: str) -> list[str]:
    try:
        return [os.path.join(path, e) for e in os.listdir(path) if os.path.isdir(os.path.join(path, e))]
    except OSError:
        return []


def _names(game_name: str, appid: int) -> list[str]:
    names = [_norm(game_name)]
    # Shortcut titles often carry a launcher prefix or a ROM extension.
    cleaned = re.sub(r"^(heroic|lutris|epic|gog)\s*[-:]\s*", "", game_name, flags=re.IGNORECASE)
    cleaned = re.sub(r"\.(iso|chd|nsp|xci|rvz|cso|zip|7z)$", "", cleaned, flags=re.IGNORECASE)
    names.append(_norm(cleaned))
    folder = None
    if appid:
        for lib in library_paths():
            try:
                with open(os.path.join(lib, "steamapps", f"appmanifest_{appid}.acf"), encoding="utf-8", errors="ignore") as fh:
                    folder = dict(_VDF_PAIR.findall(fh.read())).get("installdir")
            except OSError:
                continue
            if folder:
                names.append(_norm(folder))
                break
    return [n for n in dict.fromkeys(names) if n]


def guess_install_dir(appid: int, game_name: str) -> str | None:
    """Steam games via the manifest; other launchers by folder name."""
    if appid:
        found = install_dir(appid)
        if found:
            return found
    names = _names(game_name, 0)
    home = user_home()
    for root in (os.path.join(home, "Games", "Heroic"), os.path.join(home, "Games")):
        for child in _children(root):
            if _matches(os.path.basename(child), names) and os.path.basename(child) != "Prefixes":
                return child
    return None


def other_save_sources(appid: int, game_name: str) -> list[tuple[str, str, str]]:
    """Save locations outside Steam's Proton prefix: (label, path, kind).

    kind "prefix" = a Windows user folder (copy AppData/Documents...), "dir" = copy as is,
    "files" = a folder from which only files whose names match the game are copied.
    """
    home = user_home()
    names = _names(game_name, appid)
    if not names:
        return []
    found: list[tuple[str, str, str]] = []

    def add_prefix(label: str, pfx_users: str) -> None:
        for user in _children(pfx_users):
            if os.path.basename(user).lower() not in ("public", "default"):
                found.append((label, user, "prefix"))

    # Heroic (native and flatpak use ~/Games/Heroic/Prefixes/<title>)
    for root in (os.path.join(home, "Games", "Heroic", "Prefixes", "default"), os.path.join(home, "Games", "Heroic", "Prefixes")):
        for prefix in _children(root):
            if _matches(os.path.basename(prefix), names):
                add_prefix("heroic", os.path.join(prefix, "pfx", "drive_c", "users"))
                add_prefix("heroic", os.path.join(prefix, "drive_c", "users"))
    # Lutris (~/Games/<slug>/drive_c) and Bottles
    for prefix in _children(os.path.join(home, "Games")):
        if _matches(os.path.basename(prefix), names):
            add_prefix("lutris", os.path.join(prefix, "drive_c", "users"))
    bottles = os.path.join(home, ".var", "app", "com.usebottles.bottles", "data", "bottles", "bottles")
    for bottle in _children(bottles):
        if _matches(os.path.basename(bottle), names):
            add_prefix("bottles", os.path.join(bottle, "drive_c", "users"))

    # Native Linux games
    native_roots = [os.path.join(home, ".local", "share"), os.path.join(home, ".config")]
    native_roots += _children(os.path.join(home, ".config", "unity3d"))
    native_roots.append(os.path.join(home, ".local", "share", "godot", "app_userdata"))
    for app in _children(os.path.join(home, ".var", "app")):
        native_roots += [os.path.join(app, "data"), os.path.join(app, "config")]
    for root in native_roots:
        for child in _children(root):
            base = os.path.basename(child)
            if not _matches(base, names):
                continue
            found.append(("native", child, "dir"))

    # Emulators (EmuDeck layout): saves are named after the ROM.
    for root in (os.path.join(home, "Emulation", "saves"), os.path.join(home, "Emulation", "states")):
        if os.path.isdir(root):
            found.append(("emulation", root, "files"))
    return found


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


def _copy_matching_files(src: str, dst: str, names: list[str], budget: list[int], log: list[str]) -> None:
    for root, dirs, files in os.walk(src):
        if root[len(src):].count(os.sep) >= 4:
            dirs[:] = []
        for name in files:
            if not _matches(os.path.splitext(name)[0], names):
                continue
            path = os.path.join(root, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size > budget[0]:
                continue
            target_dir = os.path.join(dst, os.path.relpath(root, src))
            os.makedirs(target_dir, exist_ok=True)
            try:
                shutil.copy2(path, os.path.join(target_dir, name))
                budget[0] -= size
            except OSError as exc:
                log.append(f"copy failed: {path}: {exc}")


def copy_saves(appid: int, dest: str, max_total: int = 300_000_000, max_file: int = 64_000_000,
               game_name: str = "") -> dict:
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

    for i, cloud in enumerate(cloud_dirs(appid) if appid else []):
        target = os.path.join(dest, f"steam_cloud_{i}")
        _copy_tree(cloud, target, budget, max_file, log)
        sources.append((target, cloud))

    if game_name:
        names = _names(game_name, appid)
        for i, (label, src, kind) in enumerate(other_save_sources(appid, game_name)):
            target = os.path.join(dest, f"{label}_{i}")
            if kind == "prefix":
                for sub in _PREFIX_ROOTS:
                    if os.path.isdir(os.path.join(src, sub)):
                        _copy_tree(os.path.join(src, sub), os.path.join(target, sub.replace("/", "_")), budget, max_file, log)
            elif kind == "files":
                _copy_matching_files(src, target, names, budget, log)
            else:
                _copy_tree(src, target, budget, max_file, log)
            if os.path.isdir(target):
                sources.append((target, src))

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
            fh.write("  (no save folders found: not in Proton, Steam Cloud, Heroic, Lutris, Bottles, native or emulator folders)\n")
        for line in log[:50]:
            fh.write(line + "\n")

    return {
        "copied_bytes": max_total - budget[0],
        "sources": [src for _t, src in sources],
        "prefix": prefix,
    }
