"""
RP2040 hardware input: HID link to the Argus Feather firmware plus a
dispatcher with the same interface as main.MouseDispatcher.

The Feather's USB-C port is a composite USB HID device and nothing else: the
mouse the game sees, and a vendor-defined command interface (usage page
0xFF00) that this module talks to. There is no serial port, no USB-TTL adapter
and no UART wiring; everything goes over that one USB cable.

Every report, in both directions, is 16 bytes (must match
firmware/rp2040/argus_mouse/protocol.h):

    cmd | len | payload[len] | zero padding | xor      (xor at byte 15)

    MOVE     0x01  int16 le dx, int16 le dy      inject extra movement
    BUTTONS  0x02  u8 set_bits, u8 clear_bits    inject button state
    PING     0x03  -> PONG 0x83                  liveness probe
                                                 (shown as SYN / SYN-ACK / ACK)
    SCALE    0x04  u16 le Q8.8, u16 le timeout   multiply PHYSICAL deltas
    FREEZE   0x05  u16 le duration_ms            drop PHYSICAL deltas
    ARM      0x06  u8 armed, u8 hold_ms, u16 to  withhold physical LMB
    PHYS_LMB 0x84  u8 flags (bit0 consumed, bit1 withheld)  device -> host

SCALE, FREEZE, and ARM modify the physical mouse's packets inside the
firmware (MITM); MOVE/BUTTONS add on top. That difference is why the
RP2040-only modules exist at all.

A serial write returned almost at once, but a HID write only completes when
the device has taken the report, roughly a millisecond later. Doing that on
the aim loop's thread would stall every tick, so the loop only queues
commands and a writer thread does the waiting. Consecutive MOVEs are summed
into one report, which is safe because the firmware adds them together anyway.
"""

from __future__ import annotations

import re
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

import winhid

VENDOR_ID = 0x239A
PRODUCT_ID = 0x810A
USAGE_PAGE_VENDOR = 0xFF00

REPORT_SIZE = 16
PAYLOAD_MAX = REPORT_SIZE - 3  # cmd, len and checksum take the rest

CMD_MOVE = 0x01
CMD_BUTTONS = 0x02
CMD_PING = 0x03
CMD_PONG = 0x83
CMD_SCALE = 0x04
CMD_FREEZE = 0x05
CMD_ARM = 0x06
EVT_PHYS_LMB = 0x84
LMB_CONSUMED = 0x01
LMB_WITHHELD = 0x02

SCALE_ONE = 256  # Q8.8 identity, mirrors ARGUS_SCALE_ONE
FREEZE_MAX_MS = 500  # mirrors ARGUS_FREEZE_MAX_MS
HOLD_MS_MIN = 20
HOLD_MS_MAX = 60

_PING_TRIES = 3
_PING_WAIT_S = 0.2
_WRITE_TIMEOUT_MS = 250
_MAX_WRITE_TIMEOUTS = 8  # in a row, before the link is called dead
_MAX_QUEUE = 256
_MAX_EVENT_QUEUE = 32
_LEGACY_COM_PORT = re.compile(r"^com\d+$", re.IGNORECASE)


def _checksum(cmd: int, payload: bytes) -> int:
    value = cmd ^ len(payload)
    for byte in payload:
        value ^= byte
    return value & 0xFF


def _report(cmd: int, payload: bytes = b"") -> bytes:
    """One 16-byte command report."""
    if len(payload) > PAYLOAD_MAX:
        raise ValueError(f"payload of {len(payload)} bytes does not fit in a report")
    body = bytes((cmd, len(payload))) + payload
    return body.ljust(REPORT_SIZE - 1, b"\x00") + bytes((_checksum(cmd, payload),))


def _parse_report(data: bytes) -> Optional[tuple[int, bytes]]:
    """``(cmd, payload)`` from a report the device sent, or None if it is not
    a valid one. Windows puts a report-id byte (0) in front; it is removed."""
    if len(data) == REPORT_SIZE + 1 and data[0] == 0:
        data = data[1:]
    if len(data) != REPORT_SIZE:
        return None
    cmd = data[0]
    length = data[1]
    if length > PAYLOAD_MAX:
        return None
    payload = bytes(data[2 : 2 + length])
    if data[REPORT_SIZE - 1] != _checksum(cmd, payload):
        return None
    return cmd, payload


HandshakeProgress = Callable[[str, str], None]


@dataclass(frozen=True)
class PhysLmbEvent:
    """One physical left-button event from the Feather."""

    consumed: bool
    withheld: bool
    at: float


class _Queued:
    """One command waiting for the writer. A MOVE keeps its deltas as numbers
    so the next MOVE can be folded into it before it is sent."""

    __slots__ = ("cmd", "payload", "dx", "dy")

    def __init__(self, cmd: int, payload: bytes = b"", dx: int = 0, dy: int = 0) -> None:
        self.cmd = cmd
        self.payload = payload
        self.dx = dx
        self.dy = dy

    def to_report(self) -> bytes:
        if self.cmd == CMD_MOVE:
            return _report(CMD_MOVE, struct.pack("<hh", self.dx, self.dy))
        return _report(self.cmd, self.payload)


def _clamp16(value: int) -> int:
    return max(-32768, min(32767, value))


class RP2040Link:
    """HID connection to the Argus firmware. Commands are queued from any
    thread (the capture loop, the click-release timer) and sent in order by a
    writer thread; a reader thread collects what the device sends back."""

    def __init__(self, port: str = "auto") -> None:
        # `port` is kept for the settings file. "auto" finds the device; any
        # other text narrows the search to HID paths containing it. A saved
        # COM port from the old UART setup means nothing here, so it is ignored.
        self.port = (port or "auto").strip() or "auto"
        self.port_used: str = ""
        self._dev: Optional[winhid.HidHandle] = None
        self._dead_reason: str = ""
        self._stop = threading.Event()
        self._reader: Optional[threading.Thread] = None
        self._writer: Optional[threading.Thread] = None
        self._queue: deque[_Queued] = deque()
        self._queue_lock = threading.Lock()
        self._wake = threading.Event()
        self._events: deque[PhysLmbEvent] = deque()
        self._events_lock = threading.Lock()
        self._pong = threading.Event()
        self._synced = False
        self._write_timeouts = 0

    # ---------------------------------------------------------------- state

    @property
    def connected(self) -> bool:
        return self._dev is not None and not self._dead_reason

    @property
    def error(self) -> str:
        return self._dead_reason

    # -------------------------------------------------------------- connect

    def _candidates(self) -> list[winhid.HidInfo]:
        devices = winhid.enumerate_devices(VENDOR_ID, PRODUCT_ID, USAGE_PAGE_VENDOR)
        hint = self.port.lower()
        if hint == "auto" or _LEGACY_COM_PORT.match(hint):
            return devices
        return [d for d in devices if hint in d.path.lower()]

    def connect(self, progress: Optional[HandshakeProgress] = None) -> None:
        def report(port: str, step: str) -> None:
            if progress is not None:
                progress(port, step)

        try:
            devices = self._candidates()
        except OSError as exc:
            report("", "fail")
            raise RuntimeError(f"RP2040: could not list HID devices ({exc})") from exc

        # A build without the command channel still shows up as a mouse but
        # has no vendor interface, so there is nothing to find on those.
        usable = [
            d for d in devices
            if d.input_report_len > REPORT_SIZE and d.output_report_len > REPORT_SIZE
        ]
        if not usable:
            report("", "fail")
            raise RuntimeError(
                "RP2040: no Argus command interface found. Plug the Feather's USB-C "
                "port into this PC and flash the HID-only firmware "
                "(firmware/rp2040/argus_mouse); the older UART firmware has no HID "
                "command channel."
            )

        errors: list[str] = []
        for info in usable:
            label = info.label
            try:
                dev = winhid.HidHandle.open(info)
            except OSError as exc:  # opened elsewhere, access denied, unplugged
                errors.append(f"{label}: {exc}")
                continue
            self._attach(dev, label)
            report(label, "syn")
            if self._handshake():
                report(label, "synack")
                report(label, "ack")
                return
            reason = self._dead_reason or "no PONG"
            self._detach(cleanup=False)
            report(label, "timeout")
            errors.append(f"{label}: {reason}")

        report("", "fail")
        raise RuntimeError(
            f"RP2040 handshake failed ({'; '.join(errors) or 'no candidates'}) - "
            "check the Feather is running the HID-only argus_mouse firmware and "
            "its USB-C port is connected to this PC"
        )

    def _attach(self, dev: winhid.HidHandle, label: str) -> None:
        self._dev = dev
        self.port_used = label
        self._dead_reason = ""
        self._synced = False
        self._write_timeouts = 0
        self._pong.clear()
        self._stop.clear()
        with self._queue_lock:
            self._queue.clear()
        with self._events_lock:
            self._events.clear()
        self._reader = threading.Thread(target=self._read_loop, name="rp2040-hid-rx", daemon=True)
        self._writer = threading.Thread(target=self._write_loop, name="rp2040-hid-tx", daemon=True)
        self._reader.start()
        self._writer.start()

    def _handshake(self) -> bool:
        for _ in range(_PING_TRIES):
            self._pong.clear()
            self._enqueue(_Queued(CMD_PING))
            if self._pong.wait(_PING_WAIT_S):
                return True
            if self._dead_reason:
                return False
        return False

    # -------------------------------------------------------------- threads

    def _read_loop(self) -> None:
        dev = self._dev
        while dev is not None and not self._stop.is_set():
            try:
                data = dev.read_report(100)
            except winhid.HidAborted:
                break
            except OSError as exc:
                if not self._stop.is_set():
                    self._dead_reason = str(exc) or "HID read failed"
                break
            if data is None:
                continue
            parsed = _parse_report(data)
            if parsed is None:
                continue
            cmd, payload = parsed
            if cmd == CMD_PONG:
                # Everything queued on the device before this answer is stale
                # (from before we were listening), so events only count from here.
                self._synced = True
                self._pong.set()
            elif cmd == EVT_PHYS_LMB and payload and self._synced:
                flags = payload[0]
                event = PhysLmbEvent(
                    consumed=bool(flags & LMB_CONSUMED),
                    withheld=bool(flags & LMB_WITHHELD),
                    at=time.monotonic(),
                )
                with self._events_lock:
                    self._events.append(event)
                    while len(self._events) > _MAX_EVENT_QUEUE:
                        self._events.popleft()

    def _next_report(self) -> Optional[bytes]:
        with self._queue_lock:
            if not self._queue:
                self._wake.clear()
                return None
            item = self._queue.popleft()
        return item.to_report()

    def _write_loop(self) -> None:
        dev = self._dev
        while dev is not None and not self._stop.is_set():
            self._wake.wait(0.1)
            while not self._stop.is_set():
                report = self._next_report()
                if report is None:
                    break
                try:
                    accepted = dev.write_report(report, _WRITE_TIMEOUT_MS)
                except winhid.HidAborted:
                    return
                except OSError as exc:
                    # Feather unplugged or the interface went away: mark the
                    # link dead so the capture loop can say so, and never
                    # raise into the hot path.
                    if not self._stop.is_set():
                        self._dead_reason = str(exc) or "HID write failed"
                    return
                if accepted:
                    self._write_timeouts = 0
                    continue
                self._write_timeouts += 1
                if self._write_timeouts >= _MAX_WRITE_TIMEOUTS:
                    self._dead_reason = "the Feather stopped accepting commands"
                    return

    # ------------------------------------------------------------- sending

    def _enqueue(self, item: _Queued) -> None:
        if self._dev is None or self._dead_reason:
            return
        with self._queue_lock:
            self._queue.append(item)
            while len(self._queue) > _MAX_QUEUE:
                self._queue.popleft()
        self._wake.set()

    def send_move(self, dx: int, dy: int) -> None:
        dx = int(dx)
        dy = int(dy)
        if (dx == 0 and dy == 0) or self._dev is None or self._dead_reason:
            return
        with self._queue_lock:
            last = self._queue[-1] if self._queue else None
            while dx != 0 or dy != 0:
                if last is not None and last.cmd == CMD_MOVE:
                    merged_x = last.dx + dx
                    merged_y = last.dy + dy
                    if -32768 <= merged_x <= 32767 and -32768 <= merged_y <= 32767:
                        last.dx, last.dy = merged_x, merged_y
                        break
                # A report carries int16; anything bigger is split. Assist
                # deltas are tiny so this effectively never loops more than once.
                step_x = _clamp16(dx)
                step_y = _clamp16(dy)
                last = _Queued(CMD_MOVE, dx=step_x, dy=step_y)
                self._queue.append(last)
                dx -= step_x
                dy -= step_y
            while len(self._queue) > _MAX_QUEUE:
                self._queue.popleft()
        self._wake.set()

    def send_buttons(self, set_bits: int, clear_bits: int) -> None:
        self._enqueue(_Queued(CMD_BUTTONS, bytes((set_bits & 0xFF, clear_bits & 0xFF))))

    def send_scale(self, scale: float, timeout_ms: int) -> None:
        q8 = max(0, min(1024, int(round(float(scale) * SCALE_ONE))))
        timeout = max(0, min(65535, int(timeout_ms)))
        self._enqueue(_Queued(CMD_SCALE, struct.pack("<HH", q8, timeout)))

    def send_freeze(self, duration_ms: int) -> None:
        duration = max(0, min(FREEZE_MAX_MS, int(duration_ms)))
        self._enqueue(_Queued(CMD_FREEZE, struct.pack("<H", duration)))

    def send_arm(self, armed: bool, hold_ms: int, timeout_ms: int) -> None:
        hold = max(HOLD_MS_MIN, min(HOLD_MS_MAX, int(round(hold_ms))))
        timeout = max(0, min(65535, int(timeout_ms)))
        self._enqueue(_Queued(CMD_ARM, struct.pack("<BBH", 1 if armed else 0, hold, timeout)))

    # -------------------------------------------------------------- events

    def drain_lmb_events(self) -> list[PhysLmbEvent]:
        with self._events_lock:
            if not self._events:
                return []
            events = list(self._events)
            self._events.clear()
            return events

    # --------------------------------------------------------------- close

    def _detach(self, cleanup: bool) -> None:
        """Stop both threads and release the device. With ``cleanup`` the
        firmware is first left in a clean state: no scale, no freeze, no ARM
        withhold, no held injected buttons."""
        self._stop.set()
        self._wake.set()
        dev = self._dev
        if dev is not None:
            dev.cancel()  # unblock a thread waiting inside a read or write
        for thread in (self._reader, self._writer):
            if thread is not None and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=0.5)
        self._reader = None
        self._writer = None
        if dev is not None and cleanup and not self._dead_reason:
            # The writer is stopped, so this thread is now the only writer.
            for report in (
                _report(CMD_ARM, struct.pack("<BBH", 0, HOLD_MS_MIN, 0)),
                _report(CMD_SCALE, struct.pack("<HH", SCALE_ONE, 0)),
                _report(CMD_FREEZE, struct.pack("<H", 0)),
                _report(CMD_BUTTONS, bytes((0x00, 0xFF))),
            ):
                try:
                    if not dev.write_report(report, 100):
                        break
                except OSError:
                    break
        self._dev = None
        if dev is not None:
            try:
                dev.close()
            except OSError:
                pass
        with self._queue_lock:
            self._queue.clear()
        with self._events_lock:
            self._events.clear()

    def close(self) -> None:
        self._detach(cleanup=True)


class RP2040Dispatcher:
    """Same interface as main.MouseDispatcher, but the movement comes out of
    the Argus HID device on the Feather instead of SendInput."""

    # A game needs to see the button held for at least a frame or two; USB
    # full speed is 1ms per report so 20ms is comfortably registered.
    _CLICK_HOLD_S = 0.02

    def __init__(self, link: RP2040Link) -> None:
        self._link = link

    def move(self, dx: int, dy: int) -> None:
        if dx == 0 and dy == 0:
            return
        self._link.send_move(dx, dy)

    def click_left(self) -> None:
        self._link.send_buttons(0x01, 0x00)
        timer = threading.Timer(self._CLICK_HOLD_S, self._link.send_buttons, args=(0x00, 0x01))
        timer.daemon = True
        timer.start()

    def left_down(self) -> None:
        self._link.send_buttons(0x01, 0x00)

    def left_up(self) -> None:
        self._link.send_buttons(0x00, 0x01)
