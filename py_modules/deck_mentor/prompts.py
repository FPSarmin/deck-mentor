"""Prompt text sent to Claude."""

from __future__ import annotations

import time

SYSTEM_PROMPT = """You are Deck Mentor, a gaming companion running on the player's Steam Deck. \
The player is in the middle of a game and reads your reply in a narrow side panel \
(about 40 characters per line) or in a short pop-up notification, so every line counts.

How to answer:
- Reply in the language of the player's question (Russian unless they write in another language).
- Lead with the direct answer in one sentence. Then, only if needed, up to 5 short steps or bullet points.
- Plain text only: no tables, headings or code blocks. Use "- " for bullets and **bold** only for names \
of items, places, bosses or buttons.
- Write names of items, places, characters, quests and menu entries exactly as the game shows them \
(its interface language, as seen on the screenshot or in the game's official localization), so the player \
can find them in menus. If that language differs from your reply, add the translation in parentheses \
once, e.g. **Master Sword** (Меч Мастера).
- When a screenshot is attached, start from it: work out what is on screen (location, HUD, quest text, \
map, enemy, menu) and tie the advice to it. If you can't tell which moment of the game this is, say what \
you see and ask one short question.
- Follow the spoiler level in <context>. The panel hides everything after a line that is exactly \
<<<SPOILER>>> until the player presses "show solution":
  - "hint": first only a gentle nudge (what to look at, which mechanic or item to think about). Then the \
<<<SPOILER>>> line, then the full solution.
  - "balanced": clear help with the current problem. If a complete answer would also reveal story events, \
boss mechanics or a puzzle solution, put that part after the <<<SPOILER>>> line.
  - "full": the complete solution, no marker.
- <game_facts> (playtime, unlocked achievements) comes from the launcher when available. Use it to judge \
how far the player has got; it may be missing or incomplete, especially for non-Steam games.
- Use web search when it is available and you are not certain about a specific fact (item or NPC \
location, boss strategy, puzzle solution, quest steps). Prefer well-known wikis and guides; when \
<context> names a preferred_source, search it first (for example with "site:" plus that address). \
Never invent names, locations or numbers; if you could not verify something, say so in a few words.
- <earlier_questions> lists what the player asked before in this game, oldest first. Use it for \
continuity. When asked where they left off, summarize from it, the guide, the notes and the screenshot: \
what they were doing last time, what they planned next, and the one next step, in 3-5 short lines.
- <game_facts> may list achievements that are not unlocked yet, possibly only part of them; check the \
full list on the web if you need it. Point out which ones fit the player's current progress and which \
are missable (can be lost forever after some point), and say before which moment. Do not reveal what \
hidden achievements are about unless spoiler_level is "full".
- <player_notes> and <game_guide> hold what is already known about this playthrough. Treat them as \
background, and when the screenshot or question contradicts them, trust the screenshot and question.
"""

SPOILER_LABELS = {"hint": "hint", "balanced": "balanced", "full": "full"}


SPOILER_MARK = "<<<SPOILER>>>"


def facts_block(facts: dict | None) -> str:
    if not facts:
        return ""
    lines = []
    minutes = facts.get("playtime_minutes")
    if isinstance(minutes, (int, float)) and minutes > 0:
        lines.append(f"playtime: {minutes / 60:.1f} h")
    total, done = facts.get("achievements_total"), facts.get("achievements_unlocked")
    if isinstance(total, int) and total > 0:
        lines.append(f"achievements: {done or 0} of {total} unlocked")
        recent = [a for a in facts.get("recent_achievements") or [] if isinstance(a, dict)][:15]
        if recent:
            lines.append("most recently unlocked (newest first):")
            for a in recent:
                name = str(a.get("name", ""))[:80]
                desc = str(a.get("description", ""))[:160]
                lines.append(f"- {name}" + (f": {desc}" if desc else ""))
        missing = [a for a in facts.get("missing_achievements") or [] if isinstance(a, dict)][:80]
        if missing:
            lines.append(f"not unlocked yet ({len(missing)} listed):")
            for a in missing:
                name = str(a.get("name", ""))[:80]
                desc = "" if a.get("hidden") else str(a.get("description", ""))[:160]
                pct = a.get("global_percent")
                extra = " [hidden]" if a.get("hidden") else ""
                if isinstance(pct, (int, float)) and pct > 0:
                    extra += f" ({pct:.1f}% of players have it)"
                lines.append(f"- {name}{extra}" + (f": {desc}" if desc else ""))
    if facts.get("source") == "non-steam":
        lines.append("this is a non-Steam game (added as a shortcut)")
    if not lines:
        return ""
    return "\n".join(["<game_facts>", *lines, "</game_facts>"])


def _ago(seconds: float) -> str:
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} min ago"
    if seconds < 2 * 86400:
        return f"{int(seconds // 3600)} h ago"
    return f"{int(seconds // 86400)} days ago"


def history_block(history: list[dict], limit: int, now: float | None = None) -> str:
    """Earlier questions and short answers, so a fresh session still knows the story so far."""
    now = now or time.time()
    items = [h for h in history if isinstance(h, dict) and h.get("ok") and h.get("a")][-limit:]
    if not items:
        return ""
    lines = ["<earlier_questions>"]
    for h in items:
        answer = str(h["a"]).split(SPOILER_MARK)[0].strip().replace("\n", " ")
        answer = answer if len(answer) <= 400 else answer[:399] + "…"
        question = str(h.get("q", "")).replace("\n", " ")[:200]
        lines.append(f"[{_ago(now - float(h.get('ts', now)))}] Q: {question}\nA: {answer}")
    lines.append("</earlier_questions>")
    return "\n".join(lines)


def context_block(game_name: str, appid: int | None, spoiler: str, notes: str, guide: str, quick: bool,
                  facts: dict | None = None, history: str = "", wiki: str = "") -> str:
    parts = [
        "<context>",
        f"game: {game_name or 'unknown'}" + (f" (Steam appid {appid})" if appid else ""),
        f"spoiler_level: {SPOILER_LABELS.get(spoiler, 'balanced')}",
    ]
    if wiki.strip():
        parts.append(f"preferred_source: {wiki.strip()[:200]}")
    if quick:
        parts.append("format: pop-up notification. Put at most 2 short sentences (under 220 characters) "
                     "before any spoiler marker.")
    parts.append("</context>")
    fb = facts_block(facts)
    if fb:
        parts.append(fb)
    if history:
        parts.append(history)
    if notes.strip():
        parts += ["<player_notes>", notes.strip()[:2000], "</player_notes>"]
    if guide.strip():
        parts += ["<game_guide>", guide.strip()[:8000], "</game_guide>"]
    return "\n".join(parts)


def deep_task(game_name: str, install_dir: str | None, spoiler: str, has_saves: bool, focus: str,
              wiki: str = "") -> str:
    lines = [
        f"Deep analysis for {game_name or 'this game'}.",
        "",
        "Your working directory contains:",
    ]
    if has_saves:
        lines.append("- ./saves/ : a COPY of what looks like this game's saves and settings (Proton prefix, Steam Cloud, "
                     "other launchers, the running game's own folder, ports and emulators). ./saves/SOURCES.txt says "
                     "where each folder came from and which processes the game runs as. Files under ./saves/recent/ "
                     "are only guesses (recently changed save-looking files): first work out by path and content "
                     "which of them belong to this game, and ignore the rest.")
    else:
        lines.append("- No save files were found for this game. ./saves/SOURCES.txt lists the game's running "
                     "processes, which may still tell you what it is. Say in the guide that the saves were not "
                     "found and work from the player's notes and question and from the web.")
    if install_dir:
        lines.append(f"- The game's install folder, read-only: {install_dir}")
    lines += [
        "- ./guide.md : your guide from earlier analyses, if one exists. Update it rather than starting over.",
        "",
        "Goal: work out exactly where the player is right now (story progress, current area, level and stats, "
        "key items and equipment, bosses beaten, side content done or missed) and what they should do next.",
        "Use the saves first. They are often binary: write small Python 3 scripts in ./scratch/ to parse them "
        "(Unreal Engine games usually use the GVAS format; look for readable strings, names of areas, bosses and "
        "items). Cross-check names against the game's data files and the web (wikis, guides). Write names as the "
        "game shows them, with a translation in parentheses when the game's language differs from the guide's.",
        "",
        "Then write ./guide.md in the player's language (Russian by default) with these sections:",
        "1. Где ты сейчас",
        "2. Что делать дальше (3-5 конкретных шагов)",
        "3. Что ты пропустил поблизости",
        "4. Билд и снаряжение",
        "5. Источники (ссылки)",
        "Keep it under about 1200 words, short lines, no tables.",
        f"Spoiler level: {SPOILER_LABELS.get(spoiler, 'balanced')}. In 'hint' and 'balanced' mode, write sections "
        f"2 and 3 as nudges and put the exact solutions at the very end of the file after a line {SPOILER_MARK}.",
        "",
        "Rules: never modify, move or delete anything outside your working directory, including the original "
        "saves and the game folder. Don't install packages. Delete ./scratch/ when you are done.",
        "Finish with a 4-6 line summary of the most useful findings.",
    ]
    if wiki.strip():
        lines += ["", f"The player prefers this source; check it first: {wiki.strip()[:200]}"]
    if focus.strip():
        lines += ["", f"The player especially wants to know: {focus.strip()}"]
    return "\n".join(lines)
