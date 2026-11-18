"""Read the guide and answers on a phone: a tiny read-only web page on the local network.

Off until the player turns it on in the panel. The address carries a random token,
the page only shows this plugin's guide and answers for the current game, nothing
can be changed through it, and the server stops by itself after a few hours.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import secrets
import socket
import time
from typing import Callable

from .prompts import SPOILER_MARK

PORTS = (8765, 8766, 8767, 18765, 28765)
LIFETIME = 3 * 3600


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


class PhoneServer:
    def __init__(self, logger, page: Callable[[], dict]) -> None:
        self.log = logger
        self.page = page
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
                "question": cur.get("question") or ("Глубокий разбор" if cur.get("mode") == "deep" else ""),
                "html": to_html(cur.get("answer") or cur.get("text") or ""),
                "done": bool(cur.get("done")),
                "status": cur.get("status") or "",
            },
            "history": history,
        }

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(reader.readline(), timeout=10)
            for _ in range(100):  # skip headers
                line = await asyncio.wait_for(reader.readline(), timeout=10)
                if line in (b"\r\n", b"\n", b""):
                    break
            parts = request.decode("latin-1").split()
            method, path = (parts[0], parts[1].split("?")[0]) if len(parts) >= 2 else ("", "")
            base = f"/{self.token}" if self.token else "/\0"
            if method != "GET":
                status, ctype, body = "405 Method Not Allowed", "text/plain", b"GET only"
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
</style></head><body>
<header><h1 id="game">Deck Mentor</h1>
<nav><button data-tab="now" class="on">Сейчас</button><button data-tab="guide">Гайд</button><button data-tab="history">История</button></nav></header>
<main><section id="now"></section><section id="guide" hidden></section><section id="history" hidden></section></main>
<script>
const shown = {};
function put(id, html) { if (shown[id] !== html) { document.getElementById(id).innerHTML = html; shown[id] = html; } }
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function when(ts) { const d = new Date(ts * 1000); return d.toLocaleDateString() + " " + d.toLocaleTimeString().slice(0, 5); }
document.querySelectorAll("nav button").forEach(b => b.onclick = () => {
  document.querySelectorAll("nav button").forEach(x => x.classList.toggle("on", x === b));
  document.querySelectorAll("main section").forEach(s => s.hidden = s.id !== b.dataset.tab);
});
async function tick() {
  let busy = false;
  try {
    const r = await fetch(location.pathname.replace(/\\/$/, "") + "/data", { cache: "no-store" });
    if (r.ok) {
      const d = await r.json();
      document.getElementById("game").textContent = d.game;
      const c = d.current, last = d.history[0];
      busy = !!(c && !c.done);
      const now = c ? '<div class="card"><div class="q">' + esc(c.question) + "</div>" + (c.html || "")
        + (busy ? '<div class="status">⏳ ' + esc(c.status || "Думаю…") + "</div>" : "") + "</div>"
        : last ? '<div class="card"><div class="q">' + esc(last.q) + "</div>" + last.html + "</div>"
        : '<p class="empty">Задайте вопрос в Deck Mentor на Деке, и ответ появится здесь.</p>';
      put("now", now);
      put("guide", d.guide || '<p class="empty">Гайда пока нет. Запустите глубокий разбор в панели.</p>');
      put("history", d.history.map(h => '<div class="card"><div class="q">' + when(h.ts) + " · " + esc(h.q || "Глубокий разбор")
        + "</div>" + h.html + "</div>").join("") || '<p class="empty">Пока пусто.</p>');
    } else if (r.status === 404) {
      put("now", '<p class="empty">Страница выключена. Включите её снова в панели Deck Mentor.</p>');
      return;
    }
  } catch (e) {}
  setTimeout(tick, busy ? 2000 : 10000);
}
tick();
</script></body></html>
"""
