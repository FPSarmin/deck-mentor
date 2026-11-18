"""Read the guide and answers on a phone, and type questions there: a tiny web page on the local network.

Off until the player turns it on in the panel. The address carries a random token,
the page only shows this plugin's guide and answers for the current game, the only
thing it can do is ask a question (like the panel's own field), and the server stops
by itself after a few hours.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import secrets
import socket
import time
from typing import Awaitable, Callable

from .prompts import SPOILER_MARK, split_followups

PORTS = (8765, 8766, 8767, 18765, 28765)
LIFETIME = 3 * 3600
MAX_BODY = 8192
MAX_QUESTION = 1000


class PhoneError(RuntimeError):
    pass


def local_ip() -> str | None:
    """The Deck's address on the Wi-Fi network (a UDP connect sends no packets)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            ip = s.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith("127.") else ip


_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)|(https?://[^\s<]+)")
_BULLET = re.compile(r"^([-*•]|\d+[.)])\s+")


def _inline(text: str) -> str:
    out, pos = [], 0
    for m in _LINK.finditer(text):
        out.append(_bold(html.escape(text[pos:m.start()])))
        end = m.end()
        if m.group(2):
            label, url = m.group(1), m.group(2)
        else:
            url = m.group(3).rstrip(".,;:!?)»")  # "see https://x.org/a." -> the dot stays text
            label, end = url, m.start() + len(url)
        out.append(f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">{html.escape(label)}</a>')
        pos = end
    out.append(_bold(html.escape(text[pos:])))
    return "".join(out)


def _bold(escaped: str) -> str:
    return re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)


def _blocks(text: str) -> str:
    out: list[str] = []
    list_tag = ""
    for raw in text.splitlines():
        line = raw.strip()
        bullet = _BULLET.match(line)
        tag = ("ol" if bullet and bullet.group(1)[0].isdigit() else "ul") if bullet else ""
        if list_tag and tag != list_tag:
            out.append(f"</{list_tag}>")
            list_tag = ""
        if not line:
            continue
        if tag:
            if not list_tag:
                out.append(f"<{tag}>")
                list_tag = tag
            out.append(f"<li>{_inline(line[bullet.end():])}</li>")
        elif line.startswith("#"):
            out.append(f"<h3>{_inline(line.lstrip('#').strip())}</h3>")
        else:
            out.append(f"<p>{_inline(line)}</p>")
    if list_tag:
        out.append(f"</{list_tag}>")
    return "\n".join(out)


def to_html(text: str) -> str:
    """Plain-text answer or guide -> safe HTML; the part after the spoiler line goes under a shutter."""
    text = str(text or "")
    visible, mark, hidden = text.partition(SPOILER_MARK)
    out = _blocks(visible)
    if mark and hidden.strip():
        out += f'\n<details class="spoiler"><summary>🙈 Показать решение</summary>{_blocks(hidden)}</details>'
    return out


ICONS = {"done": "✅", "missing": "⭕", "missable": "⚠️", "lost": "❌", "unknown": "❔"}
_ORDER = {"missable": 0, "missing": 1, "unknown": 2, "lost": 3, "done": 4}


def tracker_html(data: dict | None, spoiler: str = "") -> str:
    """The tracker result as safe HTML: the area the player is in first, what can still be lost on top."""
    if not data or not data.get("groups"):
        return ""
    esc = html.escape
    out = []
    head = [f"<b>{esc(data['location'])}</b>"] if data.get("location") else []
    if data.get("summary"):
        head.append(esc(data["summary"]))
    if head:
        out.append('<div class="card">' + "<br>".join(head) + "</div>")
    out.append('<p class="legend">✅ есть · ⭕ ещё нет · ⚠️ можно упустить · ❌ упущено · ❔ неясно</p>')
    for g in data["groups"]:
        items = sorted(g.get("items") or [], key=lambda it: _ORDER.get(it.get("status"), 2))
        done = sum(1 for it in items if it.get("status") == "done")
        rows = []
        for it in items:
            where = esc(it.get("where") or "")
            if where and spoiler == "hint":
                where = f"<details><summary>где</summary>{where}</details>"
            elif where:
                where = f'<div class="dim">{where}</div>'
            note = f'<div class="note">{esc(it["note"])}</div>' if it.get("note") else ""
            kind = f' <span class="dim">· {esc(it["kind"])}</span>' if it.get("kind") else ""
            rows.append(f'<li class="st-{esc(it.get("status", "unknown"))}"><span class="ic">'
                        f'{ICONS.get(it.get("status"), "❔")}</span><div>{esc(it["name"])}{kind}{note}{where}</div></li>')
        title = f'{esc(g["title"])} <span class="dim">{done}/{len(items)}</span>'
        body = f'<ul class="tr">{"".join(rows)}</ul>'
        if g.get("current"):
            out.append(f"<h3>{title}</h3>{body}")
        else:
            out.append(f'<details class="area"><summary>{title}</summary>{body}</details>')
    if data.get("uncovered"):
        out.append('<p class="dim">Трекер пока не знает: ' + esc(", ".join(data["uncovered"]))
                   + ". Дополнить его можно кнопкой в панели на Деке.</p>")
    return "\n".join(out)


class PhoneServer:
    def __init__(self, logger, page: Callable[[], dict],
                 ask: Callable[[str, bool], Awaitable[dict]] | None = None,
                 refresh_tracker: Callable[[], Awaitable[dict]] | None = None) -> None:
        self.log = logger
        self.page = page
        self.ask = ask
        self.refresh_tracker = refresh_tracker
        self.server: asyncio.base_events.Server | None = None
        self.url = ""
        self.token = ""
        self.expires = 0.0
        self._timer: asyncio.TimerHandle | None = None

    def status(self) -> dict:
        running = self.server is not None
        return {"running": running, "url": self.url if running else "", "expires": int(self.expires) if running else 0}

    async def start(self) -> dict:
        loop = asyncio.get_running_loop()
        if self.server is None:
            ip = local_ip()
            if not ip:
                raise PhoneError("Дека не подключена к сети. Подключитесь к Wi-Fi и попробуйте снова.")
            self.token = secrets.token_urlsafe(9)
            for port in PORTS:
                try:
                    self.server = await asyncio.start_server(self._handle, host="0.0.0.0", port=port)
                    break
                except OSError:
                    continue
            else:
                raise PhoneError("Не удалось открыть порт для телефона.")
            self.url = f"http://{ip}:{port}/{self.token}"
            self.log.info("phone page on port %s", port)
        self.expires = time.time() + LIFETIME
        if self._timer:
            self._timer.cancel()
        self._timer = loop.call_later(LIFETIME, lambda: asyncio.ensure_future(self.stop()))
        return self.status()

    async def stop(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None
        server, self.server = self.server, None
        if server:
            server.close()
            try:
                await asyncio.wait_for(server.wait_closed(), timeout=2)
            except asyncio.TimeoutError:
                pass
        self.url = self.token = ""

    def data(self) -> dict:
        page = self.page()
        cur = page.get("current")
        history = [
            {"q": h.get("q", ""), "html": to_html(h.get("a", "")), "ts": h.get("ts", 0), "mode": h.get("mode", "")}
            for h in reversed(page.get("history") or []) if isinstance(h, dict)
        ]
        return {
            "game": page.get("game") or "Deck Mentor",
            "guide": to_html(page.get("guide") or ""),
            "current": cur and {
                "question": ("📋 Трекер" if cur.get("mode") == "tracker" else cur.get("question"))
                or ("Глубокий разбор" if cur.get("mode") == "deep" else ""),
                "html": to_html(split_followups(cur.get("answer") or cur.get("text") or "")[0]),
                "done": bool(cur.get("done")),
                "next": [str(q) for q in cur.get("followups") or []][:3],
                "status": cur.get("status") or "",
            },
            "history": history,
            "tracker_html": tracker_html(page.get("tracker"), page.get("spoiler", "")),
            "tracker_updated": (page.get("tracker") or {}).get("updated", 0),
        }

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(reader.readline(), timeout=10)
            headers = {}
            for _ in range(100):
                line = await asyncio.wait_for(reader.readline(), timeout=10)
                if line in (b"\r\n", b"\n", b""):
                    break
                key, _, value = line.decode("latin-1").partition(":")
                headers[key.strip().lower()] = value.strip()
            parts = request.decode("latin-1").split()
            method, path = (parts[0], parts[1].split("?")[0]) if len(parts) >= 2 else ("", "")
            base = f"/{self.token}" if self.token else "/\0"
            if method == "POST" and path == base + "/ask" and self.ask:
                status, ctype, body = await self._ask(reader, headers)
            elif method == "POST" and path == base + "/tracker" and self.refresh_tracker:
                status, ctype, body = await self._tracker(reader, headers)
            elif method != "GET":
                status, ctype, body = "405 Method Not Allowed", "text/plain", b"Not allowed"
            elif path in (base, base + "/"):
                status, ctype, body = "200 OK", "text/html; charset=utf-8", PAGE.encode()
            elif path == base + "/data":
                status, ctype = "200 OK", "application/json; charset=utf-8"
                body = json.dumps(self.data(), ensure_ascii=False).encode()
            else:
                status, ctype, body = "404 Not Found", "text/plain", b"Not found"
            head = (f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
                    "Cache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\n"
                    "Connection: close\r\n\r\n")
            writer.write(head.encode() + body)
            await asyncio.wait_for(writer.drain(), timeout=10)
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
        except Exception:
            self.log.exception("phone page request failed")
        finally:
            writer.close()

    async def _tracker(self, reader: asyncio.StreamReader, headers: dict) -> tuple[str, str, bytes]:
        """POST /<token>/tracker: re-read a fresh save with the game's tracker script."""
        if not headers.get("content-type", "").startswith("application/json"):
            return "415 Unsupported Media Type", "application/json", b'{"ok": false, "error": "JSON only"}'
        try:
            length = int(headers.get("content-length", "0"))
        except ValueError:
            length = 0
        if 0 < length <= MAX_BODY:
            await asyncio.wait_for(reader.readexactly(length), timeout=10)
        result = await self.refresh_tracker()
        body = json.dumps({"ok": bool(result.get("ok")), "error": str(result.get("error") or "")}, ensure_ascii=False)
        return "200 OK", "application/json; charset=utf-8", body.encode()

    async def _ask(self, reader: asyncio.StreamReader, headers: dict) -> tuple[str, str, bytes]:
        """POST /<token>/ask {"q": "...", "shot": true}. JSON only, so another site can't post a plain form here."""
        def reply(code: str, ok: bool, error: str = "") -> tuple[str, str, bytes]:
            return code, "application/json; charset=utf-8", json.dumps({"ok": ok, "error": error}, ensure_ascii=False).encode()

        if not headers.get("content-type", "").startswith("application/json"):
            return reply("415 Unsupported Media Type", False, "JSON only")
        try:
            length = int(headers.get("content-length", "0"))
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY:
            return reply("413 Payload Too Large", False, "Слишком длинный вопрос.")
        try:
            data = json.loads(await asyncio.wait_for(reader.readexactly(length), timeout=10))
        except (ValueError, asyncio.IncompleteReadError):
            return reply("400 Bad Request", False, "Не понял запрос.")
        question = str(data.get("q", "") if isinstance(data, dict) else "").strip()[:MAX_QUESTION]
        if not question:
            return reply("400 Bad Request", False, "Напишите вопрос.")
        result = await self.ask(question, bool(data.get("shot")))
        return reply("200 OK", bool(result.get("ok")), str(result.get("error") or ""))


PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Deck Mentor</title>
<style>
:root{color-scheme:dark;--bg:#14181f;--card:#1e242d;--text:#e6e9ee;--dim:#9aa4b2;--accent:#5fa8ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:17px/1.55 system-ui,sans-serif}
header{position:sticky;top:0;background:var(--bg);padding:12px 16px 0;border-bottom:1px solid #2a313c}
h1{font-size:19px;margin:0 0 8px}nav{display:flex;gap:4px}
nav button{flex:1;background:none;border:0;border-bottom:3px solid transparent;color:var(--dim);font:inherit;padding:8px 0}
nav button.on{color:var(--text);border-color:var(--accent)}
main{padding:12px 16px 48px;max-width:760px;margin:0 auto}
.card{background:var(--card);border-radius:10px;padding:12px 14px;margin:0 0 12px}
.q{color:var(--dim);font-size:14px;margin-bottom:6px}.status{color:var(--dim);font-size:14px}
h3{font-size:18px;margin:18px 0 6px}p{margin:6px 0}ul,ol{margin:6px 0;padding-left:22px}
a{color:var(--accent)}details.spoiler{margin-top:10px;border-top:1px dashed #3a4350;padding-top:8px}
summary{color:var(--accent);cursor:pointer}.empty{color:var(--dim)}
textarea{width:100%;background:var(--bg);color:var(--text);border:1px solid #3a4350;border-radius:8px;padding:10px;font:inherit;resize:vertical}
.ask{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:8px}
.ask label{color:var(--dim);font-size:15px}.err{color:#ff8080;font-size:14px}
.dim{color:var(--dim);font-size:14px}.note{color:#e8b14d;font-size:14px}.legend{color:var(--dim);font-size:13px}
ul.tr{list-style:none;padding:0}ul.tr li{display:flex;gap:10px;padding:7px 0;border-bottom:1px solid #2a313c}
ul.tr li.st-done{opacity:.6}.ic{flex:0 0 auto}details.area{margin:10px 0}details.area summary{font-weight:600;color:var(--text)}
button.send{background:var(--accent);color:#06121f;border:0;border-radius:8px;font:inherit;font-weight:600;padding:9px 18px}
button.send:disabled,button.chip:disabled{opacity:.5}
.chips{display:flex;flex-direction:column;gap:6px;margin-top:10px}
button.chip{text-align:left;background:none;color:var(--accent);border:1px solid #3a4350;border-radius:8px;font:inherit;padding:8px 10px}
</style></head><body>
<header><h1 id="game">Deck Mentor</h1>
<nav><button data-tab="now" class="on">Сейчас</button><button data-tab="tracker">Трекер</button><button data-tab="guide">Гайд</button><button data-tab="history">История</button></nav></header>
<main><section id="now"><form id="askf" class="card">
<textarea id="q" rows="3" maxlength="1000" placeholder="Спросите об игре: ответ появится здесь и на Деке"></textarea>
<div class="ask"><label><input type="checkbox" id="shot" checked> 📷 Со скриншотом Деки</label>
<button class="send" id="send">Спросить</button></div><div class="err" id="askerr"></div></form>
<div id="nowbody"></div></section><section id="tracker" hidden><div class="ask"><span class="dim" id="trackwhen"></span>
<button class="send" id="trackbtn">🔄 Обновить по сейву</button></div><div class="err" id="trackerr"></div>
<div id="trackbody"></div></section><section id="guide" hidden></section><section id="history" hidden></section></main>
<script>
const shown = {};
const base = location.pathname.replace(/\\/$/, "");
let timer = null, busyNow = false, sending = false;
function put(id, html) { if (shown[id] !== html) { document.getElementById(id).innerHTML = html; shown[id] = html; } }
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function when(ts) {
  const d = new Date(ts * 1000), p = (n) => String(n).padStart(2, "0");
  return p(d.getDate()) + "." + p(d.getMonth() + 1) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
}
document.querySelectorAll("nav button").forEach(b => b.onclick = () => {
  document.querySelectorAll("nav button").forEach(x => x.classList.toggle("on", x === b));
  document.querySelectorAll("main section").forEach(s => s.hidden = s.id !== b.dataset.tab);
});
async function tick() {
  let busy = false;
  clearTimeout(timer);
  try {
    const r = await fetch(base + "/data", { cache: "no-store" });
    if (r.ok) {
      const d = await r.json();
      document.getElementById("game").textContent = d.game;
      const c = d.current, last = d.history[0];
      busy = !!(c && !c.done);
      const chips = c && c.done && c.next && c.next.length ? '<div class="chips">' + c.next.map((q) =>
        '<button class="chip" data-q="' + esc(q) + '">↪ ' + esc(q) + "</button>").join("") + "</div>" : "";
      const now = c ? '<div class="card"><div class="q">' + esc(c.question) + "</div>" + (c.html || "")
        + (busy ? '<div class="status">⏳ ' + esc(c.status || "Думаю…") + "</div>" : "") + chips + "</div>"
        : last ? '<div class="card"><div class="q">' + esc(last.q) + "</div>" + last.html + "</div>"
        : '<p class="empty">Задайте вопрос в Deck Mentor на Деке, и ответ появится здесь.</p>';
      put("nowbody", now);
      put("trackbody", d.tracker_html || '<p class="empty">Трекера пока нет. Создайте его кнопкой «📋 Трекер» в панели на Деке.</p>');
      document.getElementById("trackwhen").textContent = d.tracker_updated ? "Обновлено " + when(d.tracker_updated) : "";
      put("guide", d.guide || '<p class="empty">Гайда пока нет. Запустите глубокий разбор в панели.</p>');
      put("history", d.history.map(h => '<div class="card"><div class="q">' + when(h.ts) + " · " + esc(h.q || "Глубокий разбор")
        + "</div>" + h.html + "</div>").join("") || '<p class="empty">Пока пусто.</p>');
    } else if (r.status === 404) {
      document.getElementById("askf").hidden = true;
      put("nowbody", '<p class="empty">Страница выключена. Включите её снова в панели Deck Mentor.</p>');
      return;
    }
  } catch (e) {}
  busyNow = busy;
  document.getElementById("send").disabled = busy || sending;
  timer = setTimeout(tick, busy ? 2000 : 10000);
}
async function send(q, shot) {
  const err = document.getElementById("askerr");
  if (!q || busyNow || sending) return false;
  sending = true;
  err.textContent = "";
  document.getElementById("send").disabled = true;
  let ok = false;
  try {
    const r = await fetch(base + "/ask", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ q, shot }) });
    const d = await r.json();
    ok = !!d.ok;
    if (!ok) err.textContent = d.error || "Не получилось";
  } catch (x) { err.textContent = "Нет связи с Декой"; }
  sending = false;
  tick();
  return ok;
}
document.getElementById("askf").onsubmit = async (e) => {
  e.preventDefault();
  const box = document.getElementById("q");
  if (await send(box.value.trim(), document.getElementById("shot").checked)) { box.value = ""; box.blur(); }
};
document.getElementById("trackbtn").onclick = async () => {
  const btn = document.getElementById("trackbtn"), err = document.getElementById("trackerr");
  btn.disabled = true; err.textContent = ""; btn.textContent = "Читаю сейв…";
  try {
    const r = await fetch(base + "/tracker", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    const d = await r.json();
    if (!d.ok) err.textContent = d.error || "Не получилось";
  } catch (x) { err.textContent = "Нет связи с Декой"; }
  btn.disabled = false; btn.textContent = "🔄 Обновить по сейву";
  tick();
};
document.getElementById("nowbody").onclick = (e) => {
  const chip = e.target.closest("button.chip");
  if (chip) { chip.disabled = true; send(chip.dataset.q, false); }
};
tick();
</script></body></html>
"""
