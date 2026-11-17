"""Network smoke test for the voice features (run in CI, needs bin/whisper-cli).

Checks that every download URL is alive, then downloads the default speech model,
the TTS engine and a Russian voice the same way the plugin does, speaks a phrase
and recognises it back.
"""
import asyncio
import logging
import os
import struct
import subprocess
import sys
import tempfile
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

from deck_mentor import voice as voice_mod  # noqa: E402
from deck_mentor.voice import PIPER_URL, PIPER_VOICES, WHISPER_MODELS, Voice  # noqa: E402


def check_urls():
    urls = [PIPER_URL] + [u for u, _ in WHISPER_MODELS.values()]
    urls += [u for v in PIPER_VOICES.values() for u in (v, v + ".json")]
    for url in urls:
        code = subprocess.run(["curl", "-sSIL", "-o", "/dev/null", "-w", "%{http_code}", url],
                              capture_output=True, text=True, timeout=60).stdout.strip()
        assert code == "200", f"{code} {url}"
        print("ok", url)


def to_16k(src, dst):
    """Piper writes 22.05 kHz; the plugin records 16 kHz mono, so test with that."""
    with wave.open(src) as w:
        rate, n = w.getframerate(), w.getnframes()
        assert w.getnchannels() == 1 and w.getsampwidth() == 2
        samples = struct.unpack(f"<{n}h", w.readframes(n))
    out_n = int(n * 16000 / rate)
    out = []
    for i in range(out_n):
        x = i * rate / 16000
        j = int(x)
        a, b = samples[j], samples[min(j + 1, n - 1)]
        out.append(int(a + (b - a) * (x - j)))
    with wave.open(dst, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack(f"<{out_n}h", *out))


async def roundtrip():
    with tempfile.TemporaryDirectory() as runtime:
        v = Voice(ROOT, runtime, logging.getLogger("voice"))
        assert v.whisper_bin(), "bin/whisper-cli is missing"
        steps = []

        async def progress(f):
            steps.append(f)

        t = time.time()
        await v.ensure_stt("base", progress)
        await v.ensure_tts("ru_irina", progress)
        print(f"downloads {time.time() - t:.1f}s, progress events {len(steps)}, last {steps[-1]}")
        assert steps and steps[-1] == 1.0

        phrase = "Как победить босса на мосту?"
        raw = os.path.join(runtime, "tts.wav")
        t = time.time()
        assert await v.synthesize(phrase, "ru_irina", raw)
        print(f"tts {time.time() - t:.2f}s, {os.path.getsize(raw)} bytes")
        wav = os.path.join(runtime, "q.wav")
        to_16k(raw, wav)
        t = time.time()
        text = await v.transcribe(wav, "base", "ru")
        print(f"stt {time.time() - t:.2f}s: {text!r}")
        low = text.lower()
        assert any(w in low for w in ("босс", "мост", "побед")), text
        assert not os.path.exists(wav)  # transcribe removes the recording

        freed = v.remove_downloads()
        print(f"removed {freed // 1_000_000} MB")
        assert freed > 50_000_000


if __name__ == "__main__":
    check_urls()
    asyncio.run(roundtrip())
    print("voice ok", voice_mod.__name__)
