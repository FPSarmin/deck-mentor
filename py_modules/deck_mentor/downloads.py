"""Download optional components (speech models, TTS engine) on demand with the system curl."""

from __future__ import annotations

import asyncio
import os
import tarfile
from typing import Awaitable, Callable

from .util import clean_env, remove_path, which

Progress = Callable[[float], Awaitable[None]]


class DownloadError(RuntimeError):
    pass


async def download(url: str, dest: str, on_progress: Progress | None = None, expected_min: int = 1) -> str:
    """Fetch `url` to `dest` atomically. Progress is reported as a 0..1 fraction."""
    env = clean_env()
    curl = which("curl", env)
    if not curl:
        raise DownloadError("curl не найден")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    remove_path(tmp)

    size = 0
    proc = await asyncio.create_subprocess_exec(
        curl, "-sSIL", url, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env)
    out, _ = await proc.communicate()
    for line in out.decode(errors="ignore").splitlines():
        if line.lower().startswith("content-length:"):
            try:
                size = int(line.split(":", 1)[1].strip())  # the last one is the final redirect target
            except ValueError:
                pass

    proc = await asyncio.create_subprocess_exec(
        curl, "-fsSL", "--retry", "3", "-o", tmp, url,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE, env=env)
    while proc.returncode is None:
        try:
            await asyncio.wait_for(proc.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass
        if on_progress and size and os.path.exists(tmp):
            await on_progress(min(0.99, os.path.getsize(tmp) / size))
    err = (await proc.stderr.read()).decode(errors="ignore").strip() if proc.stderr else ""
    if proc.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) < expected_min:
        remove_path(tmp)
        raise DownloadError(f"Не удалось скачать {os.path.basename(dest)}: {err or proc.returncode}")
    os.replace(tmp, dest)
    if on_progress:
        await on_progress(1.0)
    return dest


def extract_tar(archive: str, dest: str) -> None:
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(archive) as tar:
        root = os.path.realpath(dest)
        for member in tar.getmembers():
            target = os.path.realpath(os.path.join(dest, member.name))
            if not target.startswith(root + os.sep) and target != root:
                raise DownloadError(f"unsafe path in archive: {member.name}")
            if member.issym() or member.islnk():
                link = os.path.realpath(os.path.join(os.path.dirname(target), member.linkname))
                if not link.startswith(root + os.sep):
                    raise DownloadError(f"unsafe link in archive: {member.name}")
        tar.extractall(dest)
