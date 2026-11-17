"""Optional voice features: push-to-talk questions and spoken answers.

Nothing here runs in the background. The microphone is only open while the
player holds the button (or between two taps in the panel), and the CPU only
works for the second or two it takes to transcribe or synthesize. Models and
the TTS engine are downloaded only when the player turns the feature on.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal

from . import downloads
from .util import clean_env, remove_path, which

HF = "https://huggingface.co"
WHISPER_MODELS = {
    # name: (url, approx MB)
    "base": (f"{HF}/ggerganov/whisper.cpp/resolve/main/ggml-base-q5_1.bin", 57),
    "small": (f"{HF}/ggerganov/whisper.cpp/resolve/main/ggml-small-q5_1.bin", 181),
}
PIPER_URL = "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz"
_PV = f"{HF}/rhasspy/piper-voices/resolve/main"
PIPER_VOICES = {
    "ru_irina": f"{_PV}/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx",
    "ru_dmitri": f"{_PV}/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx",
    "en_amy": f"{_PV}/en/en_US/amy/medium/en_US-amy-medium.onnx",
}
MAX_RECORD_SECONDS = 30


class VoiceError(RuntimeError):
    pass


class Voice:
    def __init__(self, plugin_dir: str, runtime_dir: str, logger) -> None:
        self.plugin_dir = plugin_dir
        self.dir = os.path.join(runtime_dir, "voice")
        self.tmp = os.path.join(self.dir, "tmp")
        os.makedirs(self.tmp, exist_ok=True)
        self.log = logger
        self._rec: asyncio.subprocess.Process | None = None
        self._rec_path = ""
        self._rec_timer: asyncio.TimerHandle | None = None
        self._player: asyncio.subprocess.Process | None = None
        self._speak_gen = 0  # bumped by stop_speaking, so a stop during synthesis wins
        self.busy_downloading = False

    # ----------------------------------------------------------- components
    def whisper_bin(self) -> str | None:
        """Decky unzips without exec bits, so keep an executable copy in our data folder."""
        shipped = os.path.join(self.plugin_dir, "bin", "whisper-cli")
        local = os.path.join(self.dir, "whisper-cli")
        if os.path.isfile(shipped):
            if not os.path.isfile(local) or os.path.getsize(local) != os.path.getsize(shipped):
                shutil.copyfile(shipped, local)
            os.chmod(local, 0o755)
            return local
        return local if os.path.isfile(local) else None

    def model_path(self, name: str) -> str:
        return os.path.join(self.dir, "models", f"ggml-{name}.bin")

    def piper_bin(self) -> str:
        return os.path.join(self.dir, "piper", "piper")

    def voice_path(self, name: str) -> str:
        return os.path.join(self.dir, "voices", os.path.basename(PIPER_VOICES.get(name, "")))

    def status(self, model: str, tts_voice: str) -> dict:
        env = clean_env()
        return {
            "stt_binary": bool(self.whisper_bin()),
            "stt_model": os.path.isfile(self.model_path(model)),
            "stt_model_mb": WHISPER_MODELS.get(model, ("", 0))[1],
            "recorder": bool(self._recorder_cmd("/dev/null", env)),
            "tts_engine": os.path.isfile(self.piper_bin()),
            "tts_voice": os.path.isfile(self.voice_path(tts_voice)),
            "player": bool(self._player_cmd("/dev/null", env)),
            "recording": self._rec is not None,
            "downloading": self.busy_downloading,
        }

    async def ensure_stt(self, model: str, on_progress) -> None:
        if model not in WHISPER_MODELS:
            raise VoiceError(f"неизвестная модель {model}")
        if not self.whisper_bin():
            raise VoiceError("В этой сборке плагина нет распознавателя речи (bin/whisper-cli).")
        path = self.model_path(model)
        if not os.path.isfile(path):
            await downloads.download(WHISPER_MODELS[model][0], path, on_progress, expected_min=10_000_000)

    async def ensure_tts(self, voice: str, on_progress) -> None:
        if voice not in PIPER_VOICES:
            raise VoiceError(f"неизвестный голос {voice}")
        if not os.path.isfile(self.piper_bin()):
            archive = os.path.join(self.dir, "piper.tar.gz")
            await downloads.download(PIPER_URL, archive, on_progress, expected_min=1_000_000)
            remove_path(os.path.join(self.dir, "piper"))
            await asyncio.to_thread(downloads.extract_tar, archive, self.dir)
            remove_path(archive)
            for root, _dirs, files in os.walk(os.path.join(self.dir, "piper")):
                for name in files:
                    if name == "piper" or name.endswith(".so") or ".so." in name:
                        os.chmod(os.path.join(root, name), 0o755)
        path = self.voice_path(voice)
        if not os.path.isfile(path):
            await downloads.download(PIPER_VOICES[voice], path, on_progress, expected_min=1_000_000)
            await downloads.download(PIPER_VOICES[voice] + ".json", path + ".json", None)

    def remove_downloads(self) -> int:
        freed = 0
        for name in ("models", "voices", "piper", "whisper-cli"):
            freed += remove_path(os.path.join(self.dir, name))
        return freed

    # ------------------------------------------------------------ recording
    @staticmethod
    def _recorder_cmd(path: str, env: dict) -> list[str] | None:
        if which("pw-record", env):
            return [which("pw-record", env), "--rate", "16000", "--channels", "1", "--format", "s16", path]
        if which("parecord", env):
            return [which("parecord", env), "--rate=16000", "--channels=1", "--format=s16le", "--file-format=wav", path]
        if which("arecord", env):
            return [which("arecord", env), "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", path]
        return None

    async def start_recording(self) -> None:
        if self._rec:
            return
        env = clean_env()
        self._rec_path = os.path.join(self.tmp, "question.wav")
        remove_path(self._rec_path)
        cmd = self._recorder_cmd(self._rec_path, env)
        if not cmd:
            raise VoiceError("Не найдена программа записи звука (pw-record, parecord или arecord).")
        self._rec = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE, env=env)
        loop = asyncio.get_running_loop()
        self._rec_timer = loop.call_later(MAX_RECORD_SECONDS, self._interrupt_recorder)

    def _interrupt_recorder(self) -> None:
        if self._rec and self._rec.returncode is None:
            self._rec.send_signal(signal.SIGINT)

    async def stop_recording(self) -> str:
        proc, self._rec = self._rec, None
        if self._rec_timer:
            self._rec_timer.cancel()
            self._rec_timer = None
        if not proc:
            raise VoiceError("Запись не идёт.")
        if proc.returncode is None:
            proc.send_signal(signal.SIGINT)
        try:
            await asyncio.wait_for(proc.wait(), timeout=3)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        # ~0.3 s of 16 kHz mono 16-bit audio plus the WAV header
        if not os.path.isfile(self._rec_path) or os.path.getsize(self._rec_path) < 10_000:
            err = (await proc.stderr.read()).decode(errors="ignore").strip() if proc.stderr else ""
            remove_path(self._rec_path)
            raise VoiceError("Слишком короткая запись." + (f" ({err[-200:]})" if err else ""))
        return self._rec_path

    async def cancel_recording(self) -> None:
        if self._rec:
            try:
                await self.stop_recording()
            except VoiceError:
                pass
        remove_path(self._rec_path)

    async def transcribe(self, wav: str, model: str, language: str) -> str:
        binary = self.whisper_bin()
        model_file = self.model_path(model)
        if not binary or not os.path.isfile(model_file):
            raise VoiceError("Распознавание речи не настроено: включите голос в настройках.")
        threads = str(max(2, min(6, (os.cpu_count() or 4) - 2)))
        try:
            proc = await asyncio.create_subprocess_exec(
                binary, "-m", model_file, "-f", wav, "-l", language or "auto", "-nt", "-np", "-t", threads,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=clean_env())
            out, err = await asyncio.wait_for(proc.communicate(), timeout=60)
        finally:
            remove_path(wav)
        if proc.returncode != 0:
            raise VoiceError("Ошибка распознавания: " + err.decode(errors="ignore").strip()[-300:])
        text = " ".join(line.strip() for line in out.decode(errors="ignore").splitlines())
        text = re.sub(r"\[[^\]]*\]|\([^)]*музык[^)]*\)", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            raise VoiceError("Не расслышал вопрос.")
        return text

    # -------------------------------------------------------------- speaking
    @staticmethod
    def _player_cmd(path: str, env: dict) -> list[str] | None:
        for tool, args in (("pw-play", []), ("paplay", []), ("aplay", ["-q"])):
            if which(tool, env):
                return [which(tool, env), *args, path]
        return None

    @staticmethod
    def speakable(text: str, limit: int = 700) -> str:
        text = re.sub(r"https?://\S+", "", text)
        text = text.replace("**", "").replace("`", "")
        text = re.sub(r"^\s*([-*•]|\d+[.)])\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*\n\s*", ". ", text.strip())
        text = re.sub(r"\.{2,}", ".", text)
        if len(text) > limit:
            cut = text.rfind(".", 0, limit)
            text = text[: cut + 1 if cut > limit // 2 else limit]
        return text

    async def synthesize(self, text: str, voice: str, wav: str) -> bool:
        """Write `text` as speech to `wav`. Returns False when there is nothing to say."""
        model = self.voice_path(voice)
        if not os.path.isfile(self.piper_bin()) or not os.path.isfile(model):
            raise VoiceError("Озвучка не настроена: включите её в настройках.")
        text = self.speakable(text)
        if not text:
            return False
        env = clean_env()
        piper_dir = os.path.dirname(self.piper_bin())
        env["LD_LIBRARY_PATH"] = piper_dir + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
        proc = await asyncio.create_subprocess_exec(
            self.piper_bin(), "--model", model, "--output_file", wav,
            "--espeak_data", os.path.join(piper_dir, "espeak-ng-data"),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            env=env, cwd=piper_dir)
        _out, err = await asyncio.wait_for(proc.communicate(text.encode()), timeout=60)
        if proc.returncode != 0 or not os.path.isfile(wav):
            raise VoiceError("Ошибка озвучки: " + err.decode(errors="ignore").strip()[-300:])
        return True

    async def speak(self, text: str, voice: str) -> None:
        """Synthesize and play; returns when playback ends or stop_speaking() is called."""
        await self.stop_speaking()
        gen = self._speak_gen
        wav = os.path.join(self.tmp, f"answer-{gen}.wav")
        try:
            if not await self.synthesize(text, voice, wav) or gen != self._speak_gen:
                return
            cmd = self._player_cmd(wav, clean_env())
            if not cmd:
                raise VoiceError("Не найден проигрыватель звука (pw-play, paplay или aplay).")
            self._player = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, env=clean_env())
            try:
                await self._player.wait()
            finally:
                self._player = None
        finally:
            remove_path(wav)

    async def stop_speaking(self) -> None:
        self._speak_gen += 1
        if self._player and self._player.returncode is None:
            self._player.terminate()
