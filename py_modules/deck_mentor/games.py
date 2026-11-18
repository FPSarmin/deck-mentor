"""Locate a game's install folder and save files, and copy saves for analysis."""

from __future__ import annotations

import glob
import os
import re
import shutil
import time

from . import running
from .util import user_home

_VDF_PAIR = re.compile(r'"([^"]+)"\s+"([^"]*)"')

# Folders inside a Proton prefix that never hold saves but can be huge.
_SKIP_DIRS = re.compile(
    r"^(temp|tmp|cache|caches|.*cache|shadercache|d3dscache|nvidia|amd|microsoft|"
    r"crashes|crashreport.*|crashdumps|logs?|webcache|cef|gpucache|htmlcache|"
    r"steam|unity|epic.*|ubisoft game launcher|origin|ea desktop|package cache|"
    r"mods?|textures?|texture_packs|shaders?|assets|screenshots|videos|baseroms?|roms|"
    r"extracted|romfs|exefs|dump|fonts|imported_?fonts|resourcepacks)$",
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
    if len(d) < 3 or d in _NOT_GAMES:
        return False
    for n in names:
        if not n or len(n) < 3:
            continue
        if d == n:  # short names like "soh" or "pd" only count when they match exactly
            return True
        if len(n) < 4 or len(d) < 4:
            continue
        if (n in d and len(n) * 2 >= len(d)) or (d in n and len(d) >= 6 and len(d) * 2 >= len(n)):
            return True
    return False


# Unofficial PC ports keep saves under the port's own name, not the game's title.
_PORT_ALIASES = [
    (("ocarinaoftime", "shipofharkinian", "harkinian"), ("soh", "shipofharkinian")),
    (("majorasmask", "2ship", "2s2h", "zelda64"), ("2ship", "2ship2harkinian", "2s2h", "zelda64recompiled", "zelda64")),
    (("linktothepast", "zelda3"), ("zelda3",)),
    (("starfox64", "starship"), ("starship",)),
    (("supermario64", "sm64"), ("sm64ex", "sm64", "sm64coopdx", "render96ex", "sm64plus")),
    (("perfectdark",), ("perfectdark", "pd")),
    (("breathofthewild", "botw", "savagewind"), ("savagewind",)),
    (("jakanddaxter", "jak2", "jakii", "opengoal"), ("opengoal",)),
    (("supermetroid",), ("sm", "supermetroid")),
    (("supermarioworld",), ("smw",)),
]


def _children(path: str) -> list[str]:
    try:
        return [os.path.join(path, e) for e in os.listdir(path) if os.path.isdir(os.path.join(path, e))]
    except OSError:
        return []


def _names(game_name: str, appid: int, extra: list[str] | tuple = ()) -> list[str]:
    names = [_norm(game_name)]
    # Shortcut titles often carry a launcher prefix, a ROM extension or ".AppImage".
    cleaned = re.sub(r"^(heroic|lutris|epic|gog)\s*[-:]\s*", "", game_name, flags=re.IGNORECASE)
    cleaned = re.sub(r"\.(iso|chd|nsp|xci|rvz|cso|zip|7z)$", "", cleaned, flags=re.IGNORECASE)
    names.append(_norm(cleaned))
    names.append(_norm(running.stem(cleaned)))
    names += [_norm(e) for e in extra]
    joined = " ".join(names)
    for keys, ports in _PORT_ALIASES:
        if any(k in joined for k in keys):
            names += list(ports)
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


def other_save_sources(appid: int, game_name: str, extra_names: list[str] | tuple = ()) -> list[tuple[str, str, str]]:
    """Save locations outside Steam's Proton prefix: (label, path, kind).

    kind "prefix" = a Windows user folder (copy AppData/Documents...), "dir" = copy as is,
    "files" = a folder from which only files whose names match the game are copied,
    "gamedir" = a game's own folder (copy save folders and small settings files only).
    """
    home = user_home()
    names = _names(game_name, appid, extra_names)
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

    # Ports and portable games: AppImages in ~/Applications (EmuDeck) or wherever they were unpacked.
    for root in (os.path.join(home, "Applications"), os.path.join(home, "Applications", "Distrobox"),
                 os.path.join(home, "Desktop"), os.path.join(home, "Games"), os.path.join(home, "Emulation", "roms", "ports")):
        for child in _children(root):
            if _matches(os.path.basename(child), names) and not any(child == f[1] for f in found):
                found.append(("port", child, "gamedir"))

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
        dirs[:] = sorted((d for d in dirs if not _SKIP_DIRS.match(d) and not os.path.islink(os.path.join(root, d))),
                         key=lambda d: (not _SAVE_DIR.match(d), d.lower()))  # saves first, in case the budget runs out
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


_SAVE_DIR = re.compile(r"^(saves?|savedata|save ?games?|savefiles?|sram|states?|user|usr|userdata|profiles?|memcards?|"
                       r"player|slot ?\d+)$", re.IGNORECASE)
_SAVE_EXT = {".sav", ".save", ".srm", ".sram", ".eep", ".fla", ".mpk", ".state", ".dsv", ".json", ".ini", ".cfg",
             ".toml", ".yaml", ".yml", ".dat", ".txt", ".xml"}
_STRICT_SAVE_EXT = {".sav", ".save", ".srm", ".sram", ".eep", ".fla", ".mpk", ".state", ".dsv"}


def _copy_game_dir(src: str, dst: str, budget: list[int], max_file: int, log: list[str]) -> None:
    """A game's own folder: save-like subfolders (two levels deep) and small settings files at the top."""
    for root, dirs, files in os.walk(src):
        depth = root[len(src):].count(os.sep)
        dirs[:] = [d for d in dirs if not _SKIP_DIRS.match(d) and not os.path.islink(os.path.join(root, d))]
        for d in list(dirs):
            if _SAVE_DIR.match(d):
                _copy_tree(os.path.join(root, d), os.path.join(dst, os.path.relpath(root, src), d), budget, max_file, log)
                dirs.remove(d)
        if depth >= 1:
            dirs[:] = []
        for name in files:
            path = os.path.join(root, name)
            if os.path.splitext(name)[1].lower() not in _SAVE_EXT or os.path.islink(path):
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size > 4_000_000 or size > budget[0]:
                continue
            target_dir = os.path.join(dst, os.path.relpath(root, src))
            os.makedirs(target_dir, exist_ok=True)
            try:
                shutil.copy2(path, os.path.join(target_dir, name))
                budget[0] -= size
            except OSError as exc:
                log.append(f"copy failed: {path}: {exc}")


_RECENT_SKIP = _NOT_GAMES | {"googlechrome", "bravesoftware", "discord", "vesktop", "code", "kwalletd", "cache",
                             "mesashadercache", "nvidia", "qtshadercache", "tracker3", "gnomeshell", "user-places"}


def recent_save_files(since_seconds: float = 7 * 86400, limit: int = 40, time_budget: float = 5.0) -> list[str]:
    """Last resort: save-looking files changed recently anywhere a game might keep them."""
    home = user_home()
    roots = [os.path.join(home, p) for p in (".local/share", ".config", "Emulation/saves", "Applications", "Games",
                                             "Desktop", ".var/app")]
    now, started = time.time(), time.monotonic()
    found: list[tuple[float, str]] = []
    for top in roots:
        for root, dirs, files in os.walk(top):
            if time.monotonic() - started > time_budget:
                break
            depth = root[len(top):].count(os.sep)
            dirs[:] = [] if depth >= 6 else [
                d for d in dirs if _norm(d) not in _RECENT_SKIP and not _SKIP_DIRS.match(d) and not d.startswith(".")
                and not os.path.islink(os.path.join(root, d))]
            in_save_dir = bool(_SAVE_DIR.match(os.path.basename(root)))
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                if ext not in _STRICT_SAVE_EXT and not (in_save_dir and ext in _SAVE_EXT | {"", ".bin"}):
                    continue
                path = os.path.join(root, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                if now - st.st_mtime <= since_seconds and st.st_size <= 16_000_000:
                    found.append((st.st_mtime, path))
    found.sort(reverse=True)
    return [p for _m, p in found[:limit]]


SOURCE_CAP = 100_000_000


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

    # What is actually running: the executable's and AppImage's folders and their names.
    try:
        hint = running.hints(appid)
    except OSError:
        hint = {"names": [], "dirs": [], "processes": []}
    for i, src in enumerate(hint["dirs"]):
        target = os.path.join(dest, f"running_game_{i}")
        _copy_game_dir(src, target, budget, max_file, log)
        if os.path.isdir(target):
            sources.append((target, src))

    if game_name or hint["names"]:
        names = _names(game_name, appid, hint["names"])
        for i, (label, src, kind) in enumerate(other_save_sources(appid, game_name, hint["names"])):
            if any(src == s for _t, s in sources):
                continue
            target = os.path.join(dest, f"{label}_{i}")
            if kind == "gamedir":
                _copy_game_dir(src, target, budget, max_file, log)
            elif kind == "dir":
                # A data folder can also hold extracted game files or caches: cap what one source may take.
                share = [min(budget[0], SOURCE_CAP)]
                _copy_tree(src, target, share, min(max_file, 16_000_000), log)
                budget[0] -= min(budget[0], SOURCE_CAP) - share[0]
            elif kind == "prefix":
                for sub in _PREFIX_ROOTS:
                    if os.path.isdir(os.path.join(src, sub)):
                        _copy_tree(os.path.join(src, sub), os.path.join(target, sub.replace("/", "_")), budget, max_file, log)
            elif kind == "files":
                _copy_matching_files(src, target, names, budget, log)
            else:
                _copy_tree(src, target, budget, max_file, log)
            if os.path.isdir(target):
                sources.append((target, src))

    # Nothing by name or by process: offer recently changed save-looking files instead.
    recent: list[str] = []
    if not _has_files(dest):
        home = user_home()
        for path in recent_save_files():
            size = os.path.getsize(path)
            if size > budget[0]:
                continue
            target = os.path.join(dest, "recent", os.path.relpath(path, home))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                shutil.copy2(path, target)
                budget[0] -= size
                recent.append(path)
            except OSError as exc:
                log.append(f"copy failed: {path}: {exc}")

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
            fh.write("  (no save folders found: not in Proton, Steam Cloud, Heroic, Lutris, Bottles, ports, native "
                     "or emulator folders)\n")
        if recent:
            fh.write("\nGUESSES: save-looking files changed in the last 7 days anywhere in the home folder "
                     "(copied to ./recent/ by their path). Some may belong to other games:\n")
            for path in recent:
                fh.write(f"  {path}\n")
        if hint["processes"]:
            fh.write("\nThe game's running processes (what Steam launched):\n")
            for line in hint["processes"]:
                fh.write(f"  {line}\n")
        for line in log[:50]:
            fh.write(line + "\n")

    return {
        "copied_bytes": max_total - budget[0],
        "sources": [src for _t, src in sources] + recent,
        "prefix": prefix,
        "game_dir": hint["dirs"][0] if hint["dirs"] else None,
    }


def _has_files(path: str) -> bool:
    return any(files for _r, _d, files in os.walk(path))
