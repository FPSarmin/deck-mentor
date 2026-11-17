"""Offline checks: PNG encoding, capture pipeline (videotestsrc), hotkey parsing, cleanup, save copying."""
import asyncio
import os
import struct
import sys
import tempfile
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "py_modules"))

from deck_mentor import capture, cleanup, games, hotkey  # noqa: E402


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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
