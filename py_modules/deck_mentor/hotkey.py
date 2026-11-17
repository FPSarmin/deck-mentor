"""Read-only Steam Deck button listener.

Steam keeps the built-in controller in full-report mode while it runs, so we
only *read* /dev/hidraw (no feature reports, nothing that changes the
controller's behaviour) and look for a held button combination.
"""

from __future__ import annotations

import glob
import os
import select
import struct
import threading
import time
from typing import Callable

VALVE_VID = 0x28DE
DECK_PID = 0x1205
REPORT_DECK_STATE = 0x09

# Bit masks of the Deck's input report, little-endian uint32 at bytes 8..11 and 12..15.
_LOW = {
    "R2": 1 << 0, "L2": 1 << 1, "R1": 1 << 2, "L1": 1 << 3,
    "Y": 1 << 4, "B": 1 << 5, "X": 1 << 6, "A": 1 << 7,
    "UP": 1 << 8, "RIGHT": 1 << 9, "LEFT": 1 << 10, "DOWN": 1 << 11,
    "SELECT": 1 << 12, "STEAM": 1 << 13, "START": 1 << 14,
    "L5": 1 << 15, "R5": 1 << 16, "L3": 1 << 22, "R3": 1 << 26,
}
_HIGH = {"L4": 1 << 9, "R4": 1 << 10, "QAM": 1 << 18}

COMBOS: dict[str, frozenset[str]] = {
    "L4+R4": frozenset({"L4", "R4"}),
    "L5+R5": frozenset({"L5", "R5"}),
    "L4+L5": frozenset({"L4", "L5"}),
    "R4+R5": frozenset({"R4", "R5"}),
    "SELECT+R4": frozenset({"SELECT", "R4"}),
}


def pressed_buttons(report: bytes) -> set[str]:
    if len(report) < 16 or report[2] != REPORT_DECK_STATE:
        return set()
    low, high = struct.unpack_from("<II", report, 8)
    names = {name for name, bit in _LOW.items() if low & bit}
    names.update(name for name, bit in _HIGH.items() if high & bit)
    return names


def _find_device() -> str | None:
    """Pick the Deck controller's gamepad interface (input 2)."""
    candidates = []
    for sys_path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(os.path.join(sys_path, "device", "uevent")) as fh:
                uevent = fh.read().upper()
        except OSError:
            continue
        if f"{VALVE_VID:08X}:{DECK_PID:08X}" not in uevent:
            continue
        dev = "/dev/" + os.path.basename(sys_path)
        target = os.path.realpath(sys_path)
        candidates.append((0 if ":1.2/" in target else 1, dev))
    candidates.sort()
    return candidates[0][1] if candidates else None


class HotkeyListener:
    def __init__(self, on_trigger: Callable[[], None], logger) -> None:
        self._on_trigger = on_trigger
        self._log = logger
        self._combo = COMBOS["L4+R4"]
        self._hold = 0.35
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.status = "выключено"

    def configure(self, combo: str, hold_seconds: float = 0.35) -> None:
        self._combo = COMBOS.get(combo, COMBOS["L4+R4"])
        self._hold = max(0.1, hold_seconds)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="deck-mentor-hotkey", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None
        self.status = "выключено"

    def _run(self) -> None:
        while not self._stop.is_set():
            path = _find_device()
            if not path:
                self.status = "контроллер Steam Deck не найден"
                self._stop.wait(10)
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as exc:
                self.status = f"нет доступа к {path}: {exc.strerror}"
                self._log.warning("hotkey: cannot open %s: %s", path, exc)
                self._stop.wait(10)
                continue
            self.status = "работает"
            self._log.info("hotkey: listening on %s", path)
            try:
                self._loop(fd)
            except OSError as exc:
                self._log.warning("hotkey: read error: %s", exc)
                self._stop.wait(2)
            finally:
                os.close(fd)

    def _loop(self, fd: int) -> None:
        held_since: float | None = None
        fired = False
        while not self._stop.is_set():
            ready, _, _ = select.select([fd], [], [], 0.2)
            if not ready:
                continue
            try:
                report = os.read(fd, 64)
            except BlockingIOError:
                continue
            buttons = pressed_buttons(report)
            if not buttons and report[2:3] != bytes([REPORT_DECK_STATE]):
                continue
            if self._combo <= buttons:
                now = time.monotonic()
                if held_since is None:
                    held_since = now
                elif not fired and now - held_since >= self._hold:
                    fired = True
                    try:
                        self._on_trigger()
                    except Exception:  # never let a callback kill the listener
                        self._log.exception("hotkey callback failed")
            else:
                held_since = None
                fired = False
