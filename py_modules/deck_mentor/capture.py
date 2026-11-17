"""Grab the current game frame from gamescope's PipeWire stream.

Uses only system GStreamer elements that SteamOS ships (pipewiresrc,
videoconvert, videoscale) and encodes PNG in pure Python, so the plugin
needs no bundled binaries. The image stays in memory and is never written
to disk.
"""

from __future__ import annotations

import asyncio
import os
import signal
import struct
import zlib

from .util import clean_env, which

# The API downsizes anything above ~1.15 megapixels, so 1280x800 (the Deck's
# own resolution) is the sweet spot between legibility and token cost.
WIDTH = 1280
HEIGHT = 800
FRAMES = 4  # the first frames after connecting can be blank
MAX_PNG_BYTES = 3_500_000  # stays under the 5 MB base64 image limit


class CaptureError(RuntimeError):
    pass


def _png(rgb: bytes, width: int, height: int, level: int = 6) -> bytes:
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, level)) + chunk(b"IEND", b"")


def _half(rgb: bytes, width: int, height: int) -> tuple[bytes, int, int]:
    """Nearest-neighbour downscale by 2 using C-level slicing (fast enough on the Deck)."""
    stride = width * 3
    new_w, new_h = width // 2, height // 2
    out = bytearray(new_w * new_h * 3)
    row_len = new_w * 3
    for y in range(new_h):
        row = rgb[(2 * y) * stride:(2 * y) * stride + new_w * 6]
        dst = bytearray(row_len)
        dst[0::3] = row[0::6]
        dst[1::3] = row[1::6]
        dst[2::3] = row[2::6]
        out[y * row_len:(y + 1) * row_len] = dst
    return bytes(out), new_w, new_h


def _source() -> list[str]:
    # DECK_MENTOR_GST_SOURCE lets tests swap in e.g. "videotestsrc pattern=ball".
    override = os.environ.get("DECK_MENTOR_GST_SOURCE")
    if override:
        return override.split() + [f"num-buffers={FRAMES}"]
    return ["pipewiresrc", "do-timestamp=true", f"num-buffers={FRAMES}"]


def _looks_blank(frame: bytes) -> bool:
    sample = frame[:: max(1, len(frame) // 4096)]
    return max(sample) - min(sample) < 8


async def capture_png(width: int = WIDTH, height: int = HEIGHT, timeout: float = 5.0) -> tuple[bytes, int, int]:
    """Return (png_bytes, width, height) of the current screen."""
    env = clean_env()
    gst = which("gst-launch-1.0", env)
    if not gst:
        raise CaptureError("gst-launch-1.0 не найден. Скриншоты работают только в игровом режиме SteamOS.")

    caps = f"video/x-raw,format=RGB,width={width},height={height},pixel-aspect-ratio=1/1"
    proc = await asyncio.create_subprocess_exec(
        gst, "-q", "-e",
        *_source(),
        "!", "videoconvert", "!", "videoscale", "!", caps,
        "!", "fdsink", "fd=1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.send_signal(signal.SIGINT)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=1.5)
        except asyncio.TimeoutError:
            proc.kill()
            out, err = await proc.communicate()

    frame_size = width * height * 3
    frames = [out[i:i + frame_size] for i in range(0, len(out) - frame_size + 1, frame_size)]
    frame = next((f for f in reversed(frames) if not _looks_blank(f)), None)
    if frame is None:
        detail = err.decode(errors="ignore").strip().splitlines()[-1:] or ["нет кадров"]
        raise CaptureError(f"Не удалось получить кадр с экрана ({detail[0]}).")

    png = _png(frame, width, height)
    if len(png) > MAX_PNG_BYTES:
        small, w2, h2 = _half(frame, width, height)
        return _png(small, w2, h2), w2, h2
    return png, width, height
