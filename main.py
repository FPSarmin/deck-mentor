"""Deck Mentor: ask Claude for game help on the Steam Deck.

The frontend (src/index.tsx) calls the async methods of `Plugin`; progress is
pushed back with `decky.emit(EVENT, payload)`.
"""

import asyncio
import base64
import json
import os
import sys
import time
import uuid

import decky

_PY_MODULES = os.path.join(decky.DECKY_PLUGIN_DIR, "py_modules")
if _PY_MODULES not in sys.path:
    sys.path.insert(0, _PY_MODULES)

from deck_mentor import capture, claude_cli, cleanup, downloads, games, phone, prompts  # noqa: E402
from deck_mentor.hotkey import COMBOS, Binding, HotkeyListener  # noqa: E402
from deck_mentor.store import GameState, Settings  # noqa: E402
from deck_mentor.util import clean_env, dir_size, which  # noqa: E402
from deck_mentor.voice import PIPER_VOICES, WHISPER_MODELS, Voice, VoiceError  # noqa: E402

EVENT = "deck_mentor_event"
HOTKEY_EVENT = "deck_mentor_hotkey"
QUICK_TIMEOUT = 240
DEEP_TIMEOUT = 1500
CLEANUP_EVERY = 6 * 3600
REPO = "FPSarmin/deck-mentor"
UPDATE_EVERY = 12 * 3600

NOT_LOGGED_IN = (
    "Claude Code не вошёл в аккаунт. В режиме рабочего стола откройте Konsole, "
    "выполните `claude`, затем `/login` и войдите с подпиской."
)
NOT_INSTALLED = (
    "Claude Code не установлен. В режиме рабочего стола откройте Konsole и выполните:\n"
    "curl -fsSL https://claude.ai/install.sh | bash"
)


class Plugin:
    # ------------------------------------------------------------------ setup
    def _ensure(self):
        if getattr(self, "_ready", False):
            return
        self.loop = asyncio.get_running_loop()
        self.settings = Settings(decky.DECKY_PLUGIN_SETTINGS_DIR)
        self.games_dir = os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, "games")
        os.makedirs(self.games_dir, exist_ok=True)
        self.projects_dir = cleanup.default_projects_dir()
        self.task = None
        self.proc = None
        self.current = None
        self.cli_info = None
        self.last_cleanup = 0.0
        self.hotkey = HotkeyListener(decky.logger)
        self.phone_appid = 0
        self.phone = phone.PhoneServer(decky.logger, lambda: self._phone_page())
        self.voice = Voice(decky.DECKY_PLUGIN_DIR, decky.DECKY_PLUGIN_RUNTIME_DIR, decky.logger)
        self.limits_path = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "limits.json")
        try:
            with open(self.limits_path, encoding="utf-8") as fh:
                self.limits = json.load(fh)
        except (OSError, ValueError):
            self.limits = None
        self.update_info = None
        self.speak_task = None
        self._ready = True

    async def _main(self):
        self._ensure()
        self._apply_hotkey()
        while True:
            try:
                await self._cleanup()
            except Exception:
                decky.logger.exception("cleanup failed")
            await asyncio.sleep(CLEANUP_EVERY)

    async def _unload(self):
        if getattr(self, "_ready", False):
            self.hotkey.stop()
            try:
                await asyncio.wait_for(self._stop_all(), timeout=3)
            except (asyncio.TimeoutError, OSError):
                pass

    async def _stop_all(self):
        await self.phone.stop()
        await self.voice.cancel_recording()
        await self.voice.stop_speaking()
        await self.cancel()

    async def _uninstall(self):
        # Decky also calls this when it *updates* the plugin, so only temporary files go;
        # chat history, guides and Claude Code's transcripts stay.
        if getattr(self, "_ready", False):
            try:
                await asyncio.wait_for(self._cleanup(False), timeout=5)
            except (asyncio.TimeoutError, OSError):
                pass

    # ---------------------------------------------------------------- helpers
    def _game(self, appid) -> GameState:
        return GameState(self.games_dir, int(appid or 0))

    def _all_states(self):
        states = []
        for name in os.listdir(self.games_dir):
            if name.isdigit():
                states.append(GameState(self.games_dir, int(name)))
        return states

    def _api_key(self) -> str:
        return self.settings["api_key"] if self.settings["auth_mode"] == "api_key" else ""

    async def _emit(self, payload: dict):
        await decky.emit(EVENT, payload)

    def _from_thread(self, factory):
        """Run `factory()` (a coroutine function) on the event loop from the hotkey thread."""
        self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(factory()))

    async def _hotkey_event(self, payload: dict):
        await decky.emit(HOTKEY_EVENT, payload)

    def _apply_hotkey(self):
        bindings = []
        if self.settings["hotkey_enabled"]:
            bindings.append(Binding(self.settings["hotkey_combo"], 0.35,
                                    lambda: self._from_thread(lambda: self._hotkey_event({"kind": "hint"}))))
        if self.settings["voice_enabled"] and self.settings["voice_combo"] != self.settings["hotkey_combo"]:
            bindings.append(Binding(self.settings["voice_combo"], 0.25,
                                    lambda: self._from_thread(self._voice_hold),
                                    lambda: self._from_thread(self._voice_release)))
        self.hotkey.set_bindings(bindings)

    # Push-to-talk: hold the voice combo, speak, let go.
    async def _voice_hold(self):
        if self.task and not self.task.done():
            await self._hotkey_event({"kind": "voice_error", "text": "Ещё думаю над прошлым вопросом…"})
            return
        try:
            if not os.path.isfile(self.voice.model_path(self.settings["voice_model"])):
                raise VoiceError("Модель распознавания не скачана: откройте настройки голоса.")
            await self.voice.stop_speaking()
            await self.voice.start_recording()
            await self._hotkey_event({"kind": "voice_listening"})
        except (VoiceError, OSError) as exc:
            await self._hotkey_event({"kind": "voice_error", "text": str(exc)})

    async def _voice_release(self):
        try:
            wav = await self.voice.stop_recording()
            await self._hotkey_event({"kind": "voice_transcribing"})
            text = await self.voice.transcribe(wav, self.settings["voice_model"], self.settings["voice_language"])
            await self._hotkey_event({"kind": "voice", "text": text})
        except (VoiceError, OSError, asyncio.TimeoutError) as exc:
            await self._hotkey_event({"kind": "voice_error", "text": str(exc) or "Ошибка распознавания"})

    def _speak_later(self, text: str):
        if self.speak_task and not self.speak_task.done():
            self.speak_task.cancel()

        async def run():
            try:
                await self.voice.speak(text, self.settings["tts_voice"])
            except (VoiceError, OSError, asyncio.TimeoutError) as exc:
                decky.logger.warning("tts failed: %s", exc)

        self.speak_task = self.loop.create_task(run())

    def _save_limits(self, info: dict):
        windows = info.get("unifiedWindows") or {}
        self.limits = {
            "status": info.get("status"),
            "type": info.get("rateLimitType"),
            "resets_at": info.get("resetsAt"),
            "windows": {k: {"utilization": v.get("utilization"), "resets_at": v.get("resetsAt"),
                            "status": v.get("status")} for k, v in windows.items() if isinstance(v, dict)},
            "updated": int(time.time()),
        }
        try:
            with open(self.limits_path, "w", encoding="utf-8") as fh:
                json.dump(self.limits, fh)
        except OSError:
            pass

    async def _cleanup(self, everything: bool = False) -> int:
        if self.task and not self.task.done():
            return 0
        states = self._all_states()
        active = {s.data.get("session_id") for s in states if s.data.get("session_id")}
        known = {sid for s in states for sid in s.data.get("sessions", [])}
        freed = await asyncio.to_thread(
            cleanup.clean, self.projects_dir, self.games_dir,
            float(self.settings["keep_days"]), int(self.settings["max_storage_mb"]) * 1_000_000,
            active, known, everything,
        )
        if everything:
            for state in states:
                state.data.update(session_id=None, turns=0, shots=0, sessions=[])
                state.save()
        self.last_cleanup = time.time()
        if freed:
            decky.logger.info("cleanup freed %.1f MB", freed / 1e6)
        return freed

    # ------------------------------------------------------- frontend methods
    async def get_state(self, appid=0):
        self._ensure()
        state = self._game(appid)
        guide = state.guide()
        usage = await asyncio.to_thread(cleanup.usage, self.projects_dir, self.games_dir)
        return {
            "settings": self.settings.public(),
            "combos": list(COMBOS.keys()),
            "busy": bool(self.task and not self.task.done()),
            "current": self.current,
            "hotkey_status": self.hotkey.status,
            "storage_bytes": usage,
            "voice_bytes": await asyncio.to_thread(dir_size, self.voice.dir),
            "voice": self.voice.status(self.settings["voice_model"], self.settings["tts_voice"]),
            "voice_options": {"models": {k: v[1] for k, v in WHISPER_MODELS.items()}, "voices": list(PIPER_VOICES)},
            "limits": self.limits if self.settings["auth_mode"] == "subscription" else None,
            "version": getattr(decky, "DECKY_PLUGIN_VERSION", ""),
            "update": self.update_info,
            "phone": self.phone.status(),
            "game": {
                "spoiler": state.data.get("spoiler", ""),
                "wiki": state.data.get("wiki", ""),
                "notes": state.data.get("notes", ""),
                "history": state.data.get("history", [])[-30:],
                "history_total": len(state.data.get("history", [])),
                "guide": guide,
                "turns": state.data.get("turns", 0),
            },
        }

    async def check_cli(self):
        self._ensure()
        path = claude_cli.find_claude(self.settings["claude_path"])
        info = await claude_cli.status(path, self._api_key())
        if info.get("projectsDirectory"):
            self.projects_dir = info["projectsDirectory"]
        self.cli_info = info
        return info

    async def set_settings(self, patch):
        self._ensure()
        patch = dict(patch or {})
        if patch.get("api_key", "").startswith("••••"):
            patch.pop("api_key")
        for key in ("hotkey_combo", "voice_combo"):
            if key in patch and patch[key] not in COMBOS:
                patch.pop(key)
        if patch.get("voice_model") not in (None, *WHISPER_MODELS):
            patch.pop("voice_model")
        if patch.get("tts_voice") not in (None, *PIPER_VOICES):
            patch.pop("tts_voice")
        if patch.get("quick_model") not in (None, "same", "opus", "sonnet", "haiku"):
            patch.pop("quick_model")
        data = self.settings.update(patch)
        if {"hotkey_enabled", "hotkey_combo", "voice_enabled", "voice_combo"} & patch.keys():
            self._apply_hotkey()
        if {"auth_mode", "api_key", "claude_path"} & patch.keys():
            self.cli_info = None
        return self.settings.public() if data else {}

    async def set_game(self, appid, patch):
        """Per-game settings: spoiler level ("" = as in settings) and a preferred wiki."""
        self._ensure()
        state = self._game(appid)
        patch = dict(patch or {})
        if patch.get("spoiler") in ("", "hint", "balanced", "full"):
            state.data["spoiler"] = patch["spoiler"]
        if "wiki" in patch:
            state.data["wiki"] = str(patch["wiki"]).strip()[:200]
        state.save()
        return {"spoiler": state.data.get("spoiler", ""), "wiki": state.data.get("wiki", "")}

    # ------------------------------------------------------------ phone page
    async def phone_start(self, appid, game_name=""):
        self._ensure()
        self.phone_appid = int(appid or 0)
        if game_name:
            state = self._game(appid)
            state.data["name"] = game_name
            state.save()
        try:
            return {"ok": True, **(await self.phone.start())}
        except phone.PhoneError as exc:
            return {"ok": False, "error": str(exc)}

    async def phone_stop(self):
        self._ensure()
        await self.phone.stop()
        return True

    def _phone_page(self) -> dict:
        state = self._game(self.phone_appid)
        cur = self.current if self.current and self.current.get("appid") == self.phone_appid else None
        return {
            "game": state.data.get("name") or "Deck Mentor",
            "guide": state.guide(),
            "history": state.data.get("history", [])[-40:],
            "current": cur and {k: cur.get(k) for k in ("question", "text", "answer", "done", "status", "mode")},
        }

    async def set_notes(self, appid, notes):
        self._ensure()
        state = self._game(appid)
        state.data["notes"] = str(notes)[:4000]
        state.save()
        return True

    async def delete_guide(self, appid):
        self._ensure()
        try:
            os.remove(self._game(appid).guide_path)
        except OSError:
            pass
        return True

    async def new_chat(self, appid):
        self._ensure()
        state = self._game(appid)
        state.reset_chat()
        state.save()
        await self._cleanup()
        return True

    async def cleanup_now(self, everything=False):
        self._ensure()
        freed = await self._cleanup(bool(everything))
        usage = await asyncio.to_thread(cleanup.usage, self.projects_dir, self.games_dir)
        return {"freed_bytes": freed, "storage_bytes": usage}

    async def test_capture(self):
        self._ensure()
        try:
            png, w, h = await capture.capture_png()
        except capture.CaptureError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "width": w, "height": h, "bytes": len(png),
                "data_url": "data:image/png;base64," + base64.b64encode(png).decode()}

    async def cancel(self):
        self._ensure()
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
        if self.task and not self.task.done():
            self.task.cancel()
        return True

    async def ask(self, appid, game_name, question, with_shot=False, mode="quick", origin="panel", facts=None):
        """Start a request. mode: quick | deep | recap | achievements. origin: panel | hotkey | voice."""
        self._ensure()
        if self.task and not self.task.done():
            return {"ok": False, "error": "Я ещё отвечаю на прошлый вопрос."}
        mode = mode if mode in ("quick", "deep", "recap", "achievements") else "quick"
        request_id = uuid.uuid4().hex[:12]
        self.current = {
            "id": request_id, "appid": int(appid or 0), "game": game_name or "", "question": question or "",
            "mode": mode, "origin": origin, "shot": bool(with_shot), "text": "", "tools": [],
            "status": "Запускаю…", "done": False, "ok": False, "answer": "", "error": "",
            "started": time.time(),
        }
        if int(appid or 0):
            self.phone_appid = int(appid)  # the phone page follows the game being asked about
        self.task = self.loop.create_task(self._run(request_id, int(appid or 0), game_name or "",
                                                    question or "", bool(with_shot), mode, origin,
                                                    facts if isinstance(facts, dict) else None))
        return {"ok": True, "request_id": request_id}

    # ------------------------------------------------------- voice and TTS
    async def voice_setup(self, kind):
        """Download what the voice feature needs. kind: stt | tts. Progress goes out as events."""
        self._ensure()
        if self.voice.busy_downloading:
            return {"ok": False, "error": "Уже скачиваю."}
        self.voice.busy_downloading = True

        async def progress(fraction):
            await self._emit({"id": "setup", "type": "download", "kind": kind, "progress": round(fraction, 3)})

        try:
            if kind == "stt":
                await self.voice.ensure_stt(self.settings["voice_model"], progress)
            else:
                await self.voice.ensure_tts(self.settings["tts_voice"], progress)
            return {"ok": True}
        except (VoiceError, downloads.DownloadError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            self.voice.busy_downloading = False
            # Always end the progress bar, also when the download failed.
            await self._emit({"id": "setup", "type": "download", "kind": kind, "progress": 1.0})

    async def voice_remove(self):
        self._ensure()
        freed = await asyncio.to_thread(self.voice.remove_downloads)
        self.settings.update({"voice_enabled": False, "tts_enabled": False})
        self._apply_hotkey()
        return {"freed_bytes": freed}

    async def voice_record_start(self):
        self._ensure()
        try:
            await self.voice.stop_speaking()
            await self.voice.start_recording()
            return {"ok": True}
        except (VoiceError, OSError) as exc:
            return {"ok": False, "error": str(exc)}

    async def voice_record_stop(self):
        self._ensure()
        try:
            wav = await self.voice.stop_recording()
            text = await self.voice.transcribe(wav, self.settings["voice_model"], self.settings["voice_language"])
            return {"ok": True, "text": text}
        except (VoiceError, OSError, asyncio.TimeoutError) as exc:
            return {"ok": False, "error": str(exc) or "Ошибка распознавания"}

    async def speak(self, text):
        self._ensure()
        try:
            await self.voice.speak(str(text), self.settings["tts_voice"])
            return {"ok": True}
        except (VoiceError, OSError, asyncio.TimeoutError) as exc:
            return {"ok": False, "error": str(exc)}

    async def stop_speaking(self):
        self._ensure()
        await self.voice.stop_speaking()
        return True

    # -------------------------------------------------------------- updates
    async def check_update(self, force=False):
        self._ensure()
        if not force and self.update_info and time.time() - self.update_info.get("checked", 0) < UPDATE_EVERY:
            return self.update_info
        env = clean_env()
        curl = which("curl", env)
        if not curl:
            return None
        rc, out, _err = await claude_cli.run_simple(
            [curl, "-fsSL", "-H", "Accept: application/vnd.github+json",
             f"https://api.github.com/repos/{REPO}/releases/latest"], env, timeout=20)
        if rc != 0:
            return self.update_info
        try:
            data = json.loads(out)
        except ValueError:
            return self.update_info
        latest = str(data.get("tag_name", "")).lstrip("v")
        url = next((a.get("browser_download_url") for a in data.get("assets", [])
                    if a.get("name") == "deck-mentor.zip"), "")
        current = getattr(decky, "DECKY_PLUGIN_VERSION", "") or "0"

        def parse(v):
            return tuple(int(p) if p.isdigit() else 0 for p in v.split("."))

        self.update_info = {
            "current": current, "latest": latest, "url": url,
            "newer": bool(latest and url and parse(latest) > parse(current)),
            "notes": str(data.get("body") or "")[:600], "checked": time.time(),
        }
        return self.update_info

    # -------------------------------------------------------------- the work
    async def _status(self, rid, text):
        self.current["status"] = text
        await self._emit({"id": rid, "type": "status", "text": text})

    async def _finish(self, rid, ok, answer="", error=""):
        cur = self.current
        cur.update(done=True, ok=ok, answer=answer, error=error, status="")
        await self._emit({"id": rid, "type": "done", "ok": ok, "answer": answer, "error": error,
                          "origin": cur["origin"], "mode": cur["mode"], "game": cur["game"]})

    async def _run(self, rid, appid, game_name, question, with_shot, mode, origin, facts=None):
        state = self._game(appid)
        deep = mode == "deep"
        try:
            claude = claude_cli.find_claude(self.settings["claude_path"])
            if not claude:
                await self._finish(rid, False, error=NOT_INSTALLED)
                return

            png_b64 = None
            if with_shot:
                await self._status(rid, "Делаю скриншот…")
                try:
                    png, _w, _h = await capture.capture_png()
                    png_b64 = base64.b64encode(png).decode()
                except capture.CaptureError as exc:
                    await self._emit({"id": rid, "type": "warning", "text": str(exc)})
                await self._emit({"id": rid, "type": "captured", "ok": bool(png_b64)})

            os.makedirs(state.dir, exist_ok=True)
            if game_name:
                state.data["name"] = game_name
            spoiler = state.data.get("spoiler") or self.settings["spoiler"]
            wiki = state.data.get("wiki", "")
            quick_origin = origin in ("hotkey", "voice")
            # A fresh session knows nothing of earlier turns: give it a digest of the history.
            history = state.data.get("history", [])
            digest = ""
            if mode == "recap":
                digest = prompts.history_block(history, 15)
            elif not deep and state.needs_new_session():
                digest = prompts.history_block(history, 5)
            context = prompts.context_block(game_name, appid or None, spoiler, state.data.get("notes", ""),
                                            "" if deep else state.guide(), quick=quick_origin,
                                            facts=facts, history=digest, wiki=wiki)

            web = ["WebSearch", "WebFetch"] if self.settings["web_search"] else []
            tools, allowed = list(web), list(web)
            extra_args = []
            if deep:
                await self._status(rid, "Копирую сохранения…")
                saves_dir = os.path.join(state.dir, "saves")
                cleanup.remove_path(saves_dir)
                copied = await asyncio.to_thread(games.copy_saves, appid, saves_dir, 300_000_000, 64_000_000,
                                                 game_name)
                os.makedirs(os.path.join(state.dir, "scratch"), exist_ok=True)
                install = await asyncio.to_thread(games.guess_install_dir, appid, game_name) or copied.get("game_dir")
                if install:
                    extra_args += ["--add-dir", install]
                tools = ["Read", "Glob", "Grep", "Bash", "Write", "Edit"] + web
                # Writes only inside the game's workspace; Bash only for python3 scripts
                # (Claude Code still auto-allows read-only commands such as ls or strings).
                allowed = ["Read", "Glob", "Grep", "Edit(./**)", "Write(./**)",
                           "Bash(python3 *)", "Bash(rm -rf ./scratch)"] + web
                text = prompts.deep_task(game_name, install, spoiler, bool(copied["sources"]), question, wiki)
                effort = "medium" if self.settings["effort"] == "low" else self.settings["effort"]
                max_turns = int(self.settings["deep_max_turns"])
                timeout = DEEP_TIMEOUT
            else:
                text = question or "Что мне сейчас делать?"
                effort = "low" if quick_origin else self.settings["effort"]
                max_turns = 8
                timeout = QUICK_TIMEOUT
            model = self.settings["model"]
            if quick_origin and self.settings["quick_model"] in ("opus", "sonnet", "haiku"):
                model = self.settings["quick_model"]  # answers during play should come fast

            message = claude_cli.user_message(context + "\n\n" + text, png_b64)
            env = claude_cli.cli_env(self._api_key())

            for attempt in range(2):
                new_session = state.needs_new_session() or attempt == 1
                session_args = []
                if new_session:
                    state.start_session(str(uuid.uuid4()))
                    session_args = ["--session-id", state.data["session_id"]]
                else:
                    session_args = ["--resume", state.data["session_id"]]

                args = [
                    "--model", model,
                    "--effort", effort,
                    "--system-prompt", prompts.SYSTEM_PROMPT,
                    "--tools", ",".join(tools),
                    "--permission-mode", "dontAsk",
                    "--max-turns", str(max_turns),
                    "--strict-mcp-config",
                    "--disable-slash-commands",
                    *session_args,
                    *extra_args,
                ]
                if allowed:
                    args += ["--allowedTools", *allowed]

                await self._status(rid, "Разбираю сохранения и файлы игры…" if deep else "Думаю…")
                result = await self._stream(rid, claude, args, message, state.dir, env, timeout)
                missing_session = (not result.get("ok")) and "conversation" in result.get("text", "").lower() \
                    and not new_session
                if missing_session:
                    continue  # transcript was cleaned up: start a fresh session once
                break

            ok = bool(result.get("ok"))
            answer = result.get("text", "").strip()
            error = ""
            if not ok:
                error = self._explain_error(result)
                answer = self.current.get("text", "").strip() if result.get("subtype") == "error_max_turns" else ""
                if answer:
                    error = "Не хватило шагов, ответ может быть неполным."
            state.data["turns"] = state.data.get("turns", 0) + 1
            if png_b64:
                state.data["shots"] = state.data.get("shots", 0) + 1
            label = question if not deep else ("Глубокий разбор" + (f": {question}" if question else ""))
            state.record(label, answer or error, bool(png_b64), mode, ok)
            state.save()
            await self._finish(rid, ok or bool(answer), answer=answer, error=error)
            if (answer and origin in ("hotkey", "voice") and self.settings["tts_enabled"]
                    and self.settings["tts_auto"]):
                self._speak_later(answer.split(prompts.SPOILER_MARK)[0])
        except asyncio.CancelledError:
            await self._finish(rid, False, error="Отменено.")
        except asyncio.TimeoutError:
            await self._finish(rid, False, error="Claude не ответил вовремя. Попробуйте ещё раз.")
        except Exception as exc:
            decky.logger.exception("request failed")
            await self._finish(rid, False, error=f"Ошибка: {exc}")
        finally:
            self.proc = None
            if deep:
                cleanup.remove_path(os.path.join(state.dir, "saves"))
                cleanup.remove_path(os.path.join(state.dir, "scratch"))
            try:
                await self._cleanup()
            except Exception:
                decky.logger.exception("cleanup failed")

    async def _stream(self, rid, claude, args, message, cwd, env, timeout):
        def keep(proc):
            self.proc = proc

        result = {"ok": False, "text": "", "subtype": "crash"}
        limit_hit = None
        async for event in claude_cli.stream_turn(claude, args, message, cwd, env, timeout, keep):
            kind = event["type"]
            if kind == "text":
                if self.current["text"] and event.get("message", 0) > self.current.get("_msg", 0):
                    self.current["text"] += "\n\n"
                    await self._emit({"id": rid, "type": "text", "delta": "\n\n"})
                self.current["_msg"] = event.get("message", 0)
                self.current["text"] += event["delta"]
                await self._emit({"id": rid, "type": "text", "delta": event["delta"]})
            elif kind == "tool":
                self.current["tools"].append(event["label"])
                await self._status(rid, event["label"])
            elif kind == "limits":
                self._save_limits(event["info"])
                await self._emit({"id": rid, "type": "limits", "limits": self.limits})
            elif kind == "limit":
                limit_hit = event
            elif kind == "result":
                result = event
        if limit_hit and not result.get("ok"):
            result["limit"] = limit_hit
        return result

    @staticmethod
    def _explain_error(result) -> str:
        text = (result.get("text") or "").strip()
        low = text.lower()
        if result.get("limit"):
            resets = result["limit"].get("resetsAt")
            when = time.strftime("%H:%M", time.localtime(resets)) if resets else "позже"
            return f"Лимит подписки исчерпан. Сброс примерно в {when}."
        if "login" in low or "authenticat" in low or "invalid api key" in low or "oauth" in low:
            return NOT_LOGGED_IN
        if result.get("subtype") == "error_max_turns":
            return "Не хватило шагов на ответ. Попробуйте сузить вопрос."
        return text[:500] or "Claude вернул ошибку без описания."
