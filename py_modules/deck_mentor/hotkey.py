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


class Binding:
    """A button combination: `on_hold` fires once it is held long enough, `on_release` when let go."""

    def __init__(self, combo: str, hold: float, on_hold: Callable[[], None],
                 on_release: Callable[[], None] | None = None) -> None:
        self.buttons = COMBOS.get(combo, COMBOS["L4+R4"])
        self.hold = max(0.1, hold)
        self.on_hold = on_hold
        self.on_release = on_release
        self.since: float | None = None
        self.fired = False


class HotkeyListener:
    def __init__(self, logger) -> None:
        self._log = logger
        self._bindings: list[Binding] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.status = "выключено"

    def set_bindings(self, bindings: list[Binding]) -> None:
        self._bindings = bindings
        if bindings:
            self.start()
        else:
            self.stop()

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

    def _call(self, fn: Callable[[], None] | None) -> None:
        if fn:
            try:
                fn()
            except Exception:  # never let a callback kill the listener
                self._log.exception("hotkey callback failed")

    def feed(self, buttons: set[str], now: float) -> None:
        for b in list(self._bindings):
            if b.buttons <= buttons:
                if b.since is None:
                    b.since = now
                elif not b.fired and now - b.since >= b.hold:
                    b.fired = True
                    self._call(b.on_hold)
            else:
                if b.fired:
                    self._call(b.on_release)
                b.since = None
                b.fired = False

    def _waiting(self) -> bool:
        return any(b.since is not None and not b.fired for b in self._bindings)

    def _loop(self, fd: int) -> None:
        """The controller sends hundreds of reports a second; only look at button changes.

        Each wake-up drains everything queued and keeps the newest report, so a busy
        moment never builds a backlog, and the Python side does almost no work while
        buttons stay the same.
        """
        last, held = b"", set()
        while not self._stop.is_set():
            ready, _, _ = select.select([fd], [], [], 0.2)
            newest = None
            if ready:
                while True:
                    try:
                        report = os.read(fd, 64)
                    except BlockingIOError:
                        break
                    if not report:
                        break
                    if len(report) >= 16 and report[2] == REPORT_DECK_STATE:
                        newest = report
            if newest is not None and newest[8:16] != last:
                last = newest[8:16]
                held = pressed_buttons(newest)
                self.feed(held, time.monotonic())
            elif self._waiting():
                self.feed(held, time.monotonic())  # same buttons, but a hold time may have run out
            time.sleep(0.02)
