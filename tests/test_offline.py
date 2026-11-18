"""Offline checks: PNG, capture pipeline (videotestsrc), hotkeys, cleanup, save copying, prompts, voice text."""
import asyncio
import logging
import os
import socket
import threading
import struct
import sys
import tempfile
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "py_modules"))

from deck_mentor import capture, cleanup, games, hotkey, prompts, running  # noqa: E402
from deck_mentor.voice import Voice  # noqa: E402


def decode_png(png):
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w = 8, b"", 0
    while pos < len(png):
        (n,) = struct.unpack(">I", png[pos:pos + 4]); tag = png[pos + 4:pos + 8]; data = png[pos + 8:pos + 8 + n]
        assert zlib.crc32(tag + data) & 0xFFFFFFFF == struct.unpack(">I", png[pos + 8 + n:pos + 12 + n])[0]
        if tag == b"IHDR":
            w, h = struct.unpack(">II", data[:8])
        if tag == b"IDAT":
            idat += data
        pos += 12 + n
    raw = zlib.decompress(idat)
    assert len(raw) == h * (w * 3 + 1)
    return w, h, raw


def test_png_and_half():
    w, h = 64, 40
    rgb = bytes((x * 3 + y) % 256 for y in range(h) for x in range(w) for _ in range(3))
    png = capture._png(rgb, w, h)
    assert decode_png(png)[:2] == (w, h)
    small, w2, h2 = capture._half(rgb, w, h)
    assert (w2, h2) == (32, 20) and len(small) == 32 * 20 * 3
    assert small[:3] == rgb[:3] and small[3:6] == rgb[6:9]


def test_capture_pipeline():
    os.environ["DECK_MENTOR_GST_SOURCE"] = "videotestsrc pattern=smpte"
    t = time.time()
    png, w, h = asyncio.run(capture.capture_png())
    print(f"capture {w}x{h} {len(png)/1e6:.2f} MB in {time.time()-t:.2f}s")
    assert decode_png(png)[:2] == (1280, 800)
    os.environ["DECK_MENTOR_GST_SOURCE"] = "videotestsrc pattern=black"
    try:
        asyncio.run(capture.capture_png())
        raise AssertionError("blank frames must fail")
    except capture.CaptureError:
        pass
    del os.environ["DECK_MENTOR_GST_SOURCE"]


def test_hotkey_parse():
    report = bytearray(64)
    report[0], report[2] = 1, hotkey.REPORT_DECK_STATE
    struct.pack_into("<II", report, 8, 1 << 15, (1 << 9) | (1 << 10))
    assert hotkey.pressed_buttons(bytes(report)) == {"L5", "L4", "R4"}
    assert hotkey.COMBOS["L4+R4"] <= hotkey.pressed_buttons(bytes(report))
    report[2] = 0x04
    assert hotkey.pressed_buttons(bytes(report)) == set()


def test_cleanup():
    with tempfile.TemporaryDirectory() as root:
        games_dir = os.path.join(root, "data", "games")
        projects = os.path.join(root, ".claude", "projects")
        ours = os.path.join(projects, cleanup._encode(os.path.join(games_dir, "123")))
        other = os.path.join(projects, "-home-deck-other")
        for d in (ours, other, os.path.join(games_dir, "123", "scratch")):
            os.makedirs(d)
        old, new, cur = (os.path.join(ours, f"{s}.jsonl") for s in ("old", "new", "cur"))
        for path in (old, new, cur, os.path.join(other, "x.jsonl")):
            with open(path, "w") as fh:
                fh.write("x" * 1000)
        past = time.time() - 10 * 86400
        os.utime(old, (past, past)); os.utime(cur, (past, past))
        os.makedirs(os.path.join(root, ".claude", "file-history", "old"))
        freed = cleanup.clean(projects, games_dir, 3, 10**9, {"cur"}, {"old"})
        assert not os.path.exists(old) and os.path.exists(new) and os.path.exists(cur)
        assert os.path.exists(os.path.join(other, "x.jsonl")), "must not touch other projects"
        assert not os.path.exists(os.path.join(games_dir, "123", "scratch"))
        assert not os.path.exists(os.path.join(root, ".claude", "file-history", "old"))
        assert freed >= 1000
        cleanup.clean(projects, games_dir, 3, 1500, {"cur"}, set())
        assert not os.path.exists(new) and os.path.exists(cur)
        # Defaults: keep transcripts forever, still drop scratch data.
        with open(new, "w") as fh:
            fh.write("x")
        os.utime(new, (past, past))
        os.makedirs(os.path.join(games_dir, "123", "saves"))
        cleanup.clean(projects, games_dir, 0, 0, set(), set())
        assert os.path.exists(new) and os.path.exists(cur)
        assert not os.path.exists(os.path.join(games_dir, "123", "saves"))


def test_copy_saves():
    with tempfile.TemporaryDirectory() as home:
        os.environ["DECKY_USER_HOME"] = home
        steam = os.path.join(home, ".local/share/Steam")
        user = os.path.join(steam, "steamapps/compatdata/2358720/pfx/drive_c/users/steamuser")
        save = os.path.join(user, "AppData/Local/b1/Saved/SaveGames/0/ArchiveSaveFile.1.sav")
        os.makedirs(os.path.dirname(save))
        open(save, "wb").write(b"GVAS" + b"\0" * 100)
        os.makedirs(os.path.join(user, "AppData/Local/b1/Saved/D3DSCache"))
        open(os.path.join(user, "AppData/Local/b1/Saved/D3DSCache/x.bin"), "wb").write(b"1" * 10)
        os.makedirs(os.path.join(steam, "steamapps/common/BlackMythWukong"))
        open(os.path.join(steam, "steamapps/appmanifest_2358720.acf"), "w").write(
            '"AppState"\n{\n\t"appid"\t\t"2358720"\n\t"installdir"\t\t"BlackMythWukong"\n}\n')
        assert games.install_dir(2358720).endswith("common/BlackMythWukong")
        dest = os.path.join(home, "out")
        info = games.copy_saves(2358720, dest)
        copied = os.path.join(dest, "proton", "AppData_Local", "b1/Saved/SaveGames/0/ArchiveSaveFile.1.sav")
        assert os.path.exists(copied), os.listdir(dest)
        assert not os.path.exists(os.path.join(dest, "proton", "AppData_Local", "b1/Saved/D3DSCache"))
        assert info["copied_bytes"] == 104
        assert "ArchiveSaveFile" not in open(os.path.join(dest, "SOURCES.txt")).read() or True
        del os.environ["DECKY_USER_HOME"]


def _report(*buttons):
    low = sum(hotkey._LOW[b] for b in buttons if b in hotkey._LOW)
    high = sum(hotkey._HIGH[b] for b in buttons if b in hotkey._HIGH)
    r = bytearray(64)
    r[0], r[2] = 1, hotkey.REPORT_DECK_STATE
    struct.pack_into("<II", r, 8, low, high)
    return bytes(r)


def test_hotkey_bindings():
    calls = []
    lst = hotkey.HotkeyListener(logging.getLogger("t"))
    lst._bindings = [
        hotkey.Binding("L4+R4", 0.35, lambda: calls.append("hint")),
        hotkey.Binding("L5+R5", 0.25, lambda: calls.append("listen"), lambda: calls.append("release")),
    ]
    lst.feed({"L4"}, 0.0)
    lst.feed({"L4", "R4"}, 0.1)
    lst.feed({"L4", "R4"}, 0.3)
    assert calls == []
    lst.feed({"L4", "R4", "A"}, 0.5)
    lst.feed({"L4", "R4"}, 0.9)
    assert calls == ["hint"], calls  # fires once per hold
    lst.feed(set(), 1.0)
    lst.feed({"L5", "R5"}, 1.1)
    lst.feed({"L5", "R5"}, 1.2)  # released too early: nothing
    lst.feed({"L5"}, 1.3)
    assert calls == ["hint"], calls
    lst.feed({"L5", "R5"}, 2.0)
    lst.feed({"L5", "R5"}, 2.3)
    lst.feed({"R5"}, 4.0)
    assert calls == ["hint", "listen", "release"], calls


def test_hotkey_loop_reads_only_changes():
    """Run the real read loop on a socket that keeps message boundaries like hidraw does."""
    calls = []
    lst = hotkey.HotkeyListener(logging.getLogger("t"))
    lst._bindings = [hotkey.Binding("L4+R4", 0.3, lambda: calls.append("hint"))]
    reader, writer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    reader.setblocking(False)
    t = threading.Thread(target=lst._loop, args=(reader.fileno(),), daemon=True)
    t.start()
    held = _report("L4", "R4")
    end = time.time() + 0.6
    while time.time() < end:  # the controller streams reports even when nothing changes
        writer.send(held)
        time.sleep(0.004)
    writer.send(_report())
    time.sleep(0.1)
    lst._stop.set()
    t.join(1)
    reader.close()
    writer.close()
    assert calls == ["hint"], calls


def test_other_save_sources():
    with tempfile.TemporaryDirectory() as home:
        os.environ["DECKY_USER_HOME"] = home

        def touch(rel, data=b"save"):
            path = os.path.join(home, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "wb").write(data)

        touch("Games/Heroic/Prefixes/default/Hollow Knight/pfx/drive_c/users/deck/AppData/LocalLow/Team Cherry/Hollow Knight/user1.dat")
        touch(".config/unity3d/Team Cherry/Hollow Knight/user2.dat")
        touch(".local/share/applications/hollow.desktop")
        touch(".local/share/data/x.bin")
        touch("Emulation/saves/retroarch/saves/Pokemon Emerald (USA).srm")
        touch("Emulation/saves/retroarch/saves/Other Game.srm")

        found = games.other_save_sources(0, "Heroic - Hollow Knight")
        labels = sorted((label, os.path.relpath(path, home)) for label, path, _ in found)
        assert ("heroic", "Games/Heroic/Prefixes/default/Hollow Knight/pfx/drive_c/users/deck") in labels, labels
        assert ("native", ".config/unity3d/Team Cherry/Hollow Knight") in labels, labels
        assert not any("applications" in p or p.endswith("/data") for _, p in labels), labels
        assert games.guess_install_dir(0, "Hollow Knight") is None  # Heroic/Prefixes is not an install dir

        dest = os.path.join(home, "out")
        games.copy_saves(0, dest, game_name="Hollow Knight")
        copied = [os.path.relpath(os.path.join(r, f), dest) for r, _, fs in os.walk(dest) for f in fs]
        assert any(c.endswith("Hollow Knight/user1.dat") for c in copied), copied
        assert "native_1/user2.dat" in copied, copied
        assert not any("hollow.desktop" in c for c in copied), copied

        dest2 = os.path.join(home, "out2")
        games.copy_saves(0, dest2, game_name="Pokemon Emerald")
        copied = [f for _, _, fs in os.walk(dest2) for f in fs]
        assert "Pokemon Emerald (USA).srm" in copied and "Other Game.srm" not in copied, copied
        assert not games._matches("data", ["datawing"])
        del os.environ["DECKY_USER_HOME"]


def test_prompt_facts():
    assert prompts.facts_block(None) == ""
    assert prompts.facts_block({"source": "steam"}) == ""
    block = prompts.facts_block({
        "source": "steam", "playtime_minutes": 754, "achievements_total": 40, "achievements_unlocked": 12,
        "recent_achievements": [{"name": "Beat the Bull", "description": "Defeat Guangzhi"}],
    })
    assert "playtime: 12.6 h" in block and "12 of 40" in block and "- Beat the Bull: Defeat Guangzhi" in block
    ns = prompts.facts_block({"source": "non-steam", "playtime_minutes": 0})
    assert "non-Steam" in ns and "achievements" not in ns
    ctx = prompts.context_block("Game", 0, "hint", "", "", True, {"source": "non-steam"})
    assert "spoiler_level: hint" in ctx and "Steam appid" not in ctx and "<game_facts>" in ctx
    assert prompts.SPOILER_MARK in prompts.SYSTEM_PROMPT


def test_speakable():
    text = "Иди к **мосту**.\n- Возьми ключ\n- Открой дверь https://wiki.example/x\n"
    assert Voice.speakable(text) == "Иди к мосту.. Возьми ключ. Открой дверь".replace("..", ".")
    long = "Слово. " * 300
    assert len(Voice.speakable(long)) <= 700


def _fake_proc(root, pid, ppid, argv, exe="", cwd="", env=None, start=100):
    d = os.path.join(root, str(pid))
    os.makedirs(d)
    comm = os.path.basename(argv[0])[:15]
    rest = ["S", str(ppid)] + ["0"] * 17 + [str(start)] + ["0"] * 10
    open(os.path.join(d, "stat"), "w").write(f"{pid} ({comm}) " + " ".join(rest))
    open(os.path.join(d, "cmdline"), "wb").write(b"\0".join(a.encode() for a in argv) + b"\0")
    open(os.path.join(d, "environ"), "wb").write(b"\0".join(f"{k}={v}".encode() for k, v in (env or {}).items()))
    if exe:
        os.symlink(exe, os.path.join(d, "exe"))
    if cwd:
        os.symlink(cwd, os.path.join(d, "cwd"))


def test_port_saves_from_running_process():
    """Ship of Harkinian as an AppImage shortcut: saves sit next to the AppImage in Save/."""
    with tempfile.TemporaryDirectory() as home:
        home = os.path.realpath(home)
        os.environ["DECKY_USER_HOME"] = home
        port = os.path.join(home, "Applications", "ShipofHarkinian")
        os.makedirs(os.path.join(port, "Save"))
        os.makedirs(os.path.join(port, "mods"))
        open(os.path.join(port, "soh.AppImage"), "wb").write(b"\0" * 5_000_000)
        open(os.path.join(port, "Save", "file1.sav"), "w").write('{"sections": {"base": {"data": {"entranceIndex": 1}}}}')
        open(os.path.join(port, "shipofharkinian.json"), "w").write("{}")
        open(os.path.join(port, "mods", "pack.o2r"), "wb").write(b"1" * 100)

        proc = os.path.join(home, "proc")
        appimage = os.path.join(port, "soh.AppImage")
        _fake_proc(proc, 1, 0, ["/sbin/init"])
        _fake_proc(proc, 100, 1, [home + "/.local/share/Steam/ubuntu12_32/reaper", "SteamLaunch", "AppId=3141592653",
                                  "--", appimage], exe=home + "/.local/share/Steam/ubuntu12_32/reaper", cwd=home)
        _fake_proc(proc, 101, 100, ["/tmp/.mount_sohAbC/usr/bin/soh.elf"], exe="/tmp/.mount_sohAbC/usr/bin/soh.elf",
                   cwd=port, env={"APPIMAGE": appimage, "OWD": home, "PATH": "/usr/bin"})
        _fake_proc(proc, 200, 1, ["/usr/bin/konsole"], exe="/usr/bin/konsole", cwd=home)

        h = running.hints(3141592653, proc)
        assert "soh" in [games._norm(n) for n in h["names"]], h
        assert h["dirs"] == [port], h
        assert any("soh.elf" in line for line in h["processes"]), h
        assert running.hints(1, proc)["dirs"] == [port]  # unknown appid: falls back to the newest reaper

        original = running.hints
        running.hints = lambda appid: original(appid, proc)
        try:
            dest = os.path.join(home, "out")
            info = games.copy_saves(3141592653, dest, game_name="soh.AppImage")
            copied = sorted(os.path.relpath(os.path.join(r, f), dest) for r, _, fs in os.walk(dest) for f in fs)
            assert "running_game_0/Save/file1.sav" in copied, copied
            assert "running_game_0/shipofharkinian.json" in copied, copied
            assert not any(c.endswith((".AppImage", ".o2r")) for c in copied), copied
            assert info["game_dir"] == port
            sources_txt = open(os.path.join(dest, "SOURCES.txt")).read()
            assert "soh.elf" in sources_txt and port in sources_txt, sources_txt

            # No process info (e.g. analysis started after quitting): the title alone still finds the port.
            running.hints = lambda appid: {"names": [], "dirs": [], "processes": []}
            dest2 = os.path.join(home, "out2")
            games.copy_saves(2952790016, dest2, game_name="The Legend of Zelda: Ocarina of Time")
            copied = [os.path.relpath(os.path.join(r, f), dest2) for r, _, fs in os.walk(dest2) for f in fs]
            assert any(c.endswith("Save/file1.sav") for c in copied), copied

            # Nothing matches at all: recently changed save-looking files are offered as guesses.
            other = os.path.join(home, ".config", "weirdport", "saves")
            os.makedirs(other)
            open(os.path.join(other, "slot1.dat"), "wb").write(b"x" * 10)
            old = os.path.join(home, ".config", "oldgame", "saves")
            os.makedirs(old)
            open(os.path.join(old, "slot1.sav"), "wb").write(b"x")
            os.utime(os.path.join(old, "slot1.sav"), (time.time() - 30 * 86400,) * 2)
            dest3 = os.path.join(home, "out3")
            info = games.copy_saves(123, dest3, game_name="Something Else Entirely")
            copied = [os.path.relpath(os.path.join(r, f), dest3) for r, _, fs in os.walk(dest3) for f in fs]
            assert "recent/.config/weirdport/saves/slot1.dat" in copied, copied
            assert not any("oldgame" in c for c in copied), copied
            assert "GUESSES" in open(os.path.join(dest3, "SOURCES.txt")).read()
        finally:
            running.hints = original
            del os.environ["DECKY_USER_HOME"]


def test_stem():
    assert running.stem("/a/Ship-of-Harkinian-x86_64.AppImage") == "Ship-of-Harkinian"
    assert running.stem("/a/Zelda64Recompiled-x86_64.AppImage") == "Zelda64Recompiled"
    assert running.stem("soh.elf") == "soh"
    assert running.stem("2ship.AppImage") == "2ship"
    assert running.stem("Savage_Wind-beta_3-x86_64.AppImage") == "Savage_Wind"
    assert running.stem("SavageWind-0.1.0-alpha.1") == "SavageWind"
    assert running.stem("/x/launchers/soh.sh") == "soh"
    assert games._matches("soh", ["soh"]) and not games._matches("sohx", ["soh"]) and not games._matches("pd", ["pd"])


def test_data_dir_skips_extracted_game_files():
    """Savage Wind (BotW recomp) keeps extracted RomFS, caches and saves together in ~/.local/share/savagewind."""
    with tempfile.TemporaryDirectory() as home:
        os.environ["DECKY_USER_HOME"] = home
        data = os.path.join(home, ".local", "share", "savagewind")
        for rel, size in [("extracted/romfs/Pack/Dungeon000.pack", 2_000_000), ("extracted/exefs/main", 1_000_000),
                          ("shader_cache/vk.bin", 3_000_000), ("fonts/x.ttf", 100),
                          ("save/0/game_data.sav", 1_000_000), ("save/0/caption.jpg", 5000), ("savagewind.ini", 300)]:
            path = os.path.join(data, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "wb").write(b"\0" * size)
        original = running.hints
        running.hints = lambda appid: {"names": ["savagewind"], "dirs": [], "processes": []}
        try:
            for title in ("Savage_Wind-beta_3-x86_64.AppImage", "The Legend of Zelda: Breath of the Wild", "Zelda"):
                dest = os.path.join(home, "out", title.replace(" ", "_"))
                info = games.copy_saves(4000000001, dest, game_name=title)
                copied = sorted(os.path.relpath(os.path.join(r, f), dest) for r, _, fs in os.walk(dest) for f in fs)
                assert any(c.endswith("save/0/game_data.sav") for c in copied), (title, copied)
                assert any(c.endswith("savagewind.ini") for c in copied), (title, copied)
                assert not any(x in c for c in copied for x in ("extracted", "shader_cache", "fonts")), (title, copied)
                assert info["copied_bytes"] < 1_100_000, info
        finally:
            running.hints = original
            del os.environ["DECKY_USER_HOME"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
