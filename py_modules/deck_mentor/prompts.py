"""Prompt text sent to Claude."""

from __future__ import annotations

SYSTEM_PROMPT = """You are Deck Mentor, a gaming companion running on the player's Steam Deck. \
The player is in the middle of a game and reads your reply in a narrow side panel \
(about 40 characters per line) or in a short pop-up notification, so every line counts.

How to answer:
- Reply in the language of the player's question (Russian unless they write in another language).
- Lead with the direct answer in one sentence. Then, only if needed, up to 5 short steps or bullet points.
- Plain text only: no tables, headings or code blocks. Use "- " for bullets and **bold** only for names \
of items, places, bosses or buttons.
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
location, boss strategy, puzzle solution, quest steps). Prefer well-known wikis and guides. Never invent \
names, locations or numbers; if you could not verify something, say so in a few words.
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
    if facts.get("source") == "non-steam":
        lines.append("this is a non-Steam game (added as a shortcut)")
    if not lines:
        return ""
    return "\n".join(["<game_facts>", *lines, "</game_facts>"])


def context_block(game_name: str, appid: int | None, spoiler: str, notes: str, guide: str, quick: bool,
                  facts: dict | None = None) -> str:
    parts = [
        "<context>",
        f"game: {game_name or 'unknown'}" + (f" (Steam appid {appid})" if appid else ""),
        f"spoiler_level: {SPOILER_LABELS.get(spoiler, 'balanced')}",
    ]
    if quick:
        parts.append("format: pop-up notification. Put at most 2 short sentences (under 220 characters) "
                     "before any spoiler marker.")
    parts.append("</context>")
    fb = facts_block(facts)
    if fb:
        parts.append(fb)
    if notes.strip():
        parts += ["<player_notes>", notes.strip()[:2000], "</player_notes>"]
    if guide.strip():
        parts += ["<game_guide>", guide.strip()[:8000], "</game_guide>"]
    return "\n".join(parts)


def deep_task(game_name: str, install_dir: str | None, spoiler: str, has_saves: bool, focus: str) -> str:
    lines = [
        f"Deep analysis for {game_name or 'this game'}.",
        "",
        "Your working directory contains:",
    ]
    if has_saves:
        lines.append("- ./saves/ : a COPY of this game's save files and settings (Proton prefix and Steam Cloud). "
                     "./saves/SOURCES.txt says where each folder came from.")
    else:
        lines.append("- No save files were found for this game (it may be a native Linux game or store saves elsewhere).")
    if install_dir:
        lines.append(f"- The game's install folder, read-only: {install_dir}")
    lines += [
        "- ./guide.md : your guide from earlier analyses, if one exists. Update it rather than starting over.",
        "",
        "Goal: work out exactly where the player is right now (story progress, current area, level and stats, "
        "key items and equipment, bosses beaten, side content done or missed) and what they should do next.",
        "Use the saves first. They are often binary: write small Python 3 scripts in ./scratch/ to parse them "
        "(Unreal Engine games usually use the GVAS format; look for readable strings, names of areas, bosses and "
        "items). Cross-check names against the game's data files and the web (wikis, guides).",
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
    if focus.strip():
        lines += ["", f"The player especially wants to know: {focus.strip()}"]
    return "\n".join(lines)
