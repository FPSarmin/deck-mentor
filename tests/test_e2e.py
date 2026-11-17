"""End-to-end run of Plugin against the real `claude` CLI (costs a few cents).

Run: DECK_MENTOR_GST_SOURCE="videotestsrc pattern=smpte" python3.11 tests/test_e2e.py [deep]
"""
import asyncio
import glob
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = "/tmp/deck-mentor-test"
shutil.rmtree(ROOT, ignore_errors=True)
os.environ["DECK_MENTOR_TEST_ROOT"] = ROOT
sys.path.insert(0, os.path.join(HERE, "fake"))
sys.path.insert(0, os.path.join(HERE, ".."))

import decky  # noqa: E402  (the fake)
import main  # noqa: E402


async def wait(plugin):
    while plugin.task and not plugin.task.done():
        await asyncio.sleep(0.2)
    return plugin.current


async def run():
    p = main.Plugin()
    p._ensure()
    p.hotkey.start = lambda: None  # no controller here
    print("cli:", await p.check_cli())
    await p.set_settings({"model": "haiku", "web_search": "web" in sys.argv})

    r = await p.ask(42, "Test Game", "Что за картинка на скриншоте? Одним предложением.", True, "quick", "panel")
    assert r["ok"], r
    cur = await wait(p)
    print("Q1:", cur["ok"], repr(cur["answer"][:200]), cur["error"], cur["tools"])
    assert cur["ok"] and cur["answer"]
    kinds = [e[1][0]["type"] for e in decky.events if e[0] == main.EVENT]
    assert "text" in kinds and "done" in kinds and "captured" in kinds, kinds

    sid = p._game(42).data["session_id"]
    await p.ask(42, "Test Game", "А какой я задал прошлый вопрос?", False, "quick", "hotkey",
                {"source": "non-steam", "playtime_minutes": 90})
    cur = await wait(p)
    print("Q2:", cur["ok"], repr(cur["answer"][:200]), cur["error"])
    assert p._game(42).data["session_id"] == sid, "second turn should resume the same session"

    state = await p.get_state(42)
    print("history:", len(state["game"]["history"]), "storage MB:", round(state["storage_bytes"] / 1e6, 2))
    print("limits:", state["limits"])
    transcripts = glob.glob(os.path.join(p.projects_dir, "*", sid + ".jsonl"))
    assert transcripts, "transcript expected"

    if "deep" in sys.argv:
        home = os.path.join(ROOT, "home")
        os.environ["DECKY_USER_HOME"] = home
        user = os.path.join(home, ".local/share/Steam/steamapps/compatdata/77/pfx/drive_c/users/steamuser")
        os.makedirs(os.path.join(user, "AppData/Local/Quest/Saved"))
        with open(os.path.join(user, "AppData/Local/Quest/Saved/save.json"), "w") as fh:
            fh.write('{"chapter": 3, "area": "Black Wind Mountain", "bosses_defeated": ["Wandering Wight"], "level": 27}')
        await p.ask(77, "Quest", "", False, "deep", "panel")
        cur = await wait(p)
        print("DEEP:", cur["ok"], cur["tools"], repr(cur["answer"][:300]), cur["error"])
        guide = p._game(77).guide()
        print("guide.md chars:", len(guide))
        assert guide and not os.path.exists(os.path.join(p._game(77).dir, "saves"))

    res = await p.cleanup_now(True)
    print("cleanup all:", res)
    assert not glob.glob(os.path.join(p.projects_dir, "*", sid + ".jsonl"))


asyncio.run(run())
print("e2e ok")
