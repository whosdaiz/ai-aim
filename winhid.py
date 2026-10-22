"""Minimal Windows HID access, with no third-party dependency.

Just enough of hid.dll / setupapi.dll / kernel32.dll to find a HID interface
by vendor id, product id and usage page, then exchange fixed-size reports with
it using overlapped I/O:

    devices = enumerate_devices(vid=0x239A, pid=0x810A)
    handle = HidHandle.open(devices[0])
    handle.write_report(b"...", timeout_ms=250)
    data = handle.read_report(timeout_ms=100)     # None on timeout
    handle.close()

Reads and writes use separate OVERLAPPED structures, so one thread can sit in
``read_report`` while another calls ``write_report`` on the same handle. A
read that times out stays outstanding and is picked up by the next call, so a
report that arrives between two calls is never lost.

This is Windows only. Importing the module works anywhere; calling into it
elsewhere raises OSError.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional

_IS_WINDOWS = sys.platform == "win32"

DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000

ERROR_INSUFFICIENT_BUFFER = 122
ERROR_NO_MORE_ITEMS = 259
ERROR_IO_PENDING = 997
ERROR_OPERATION_ABORTED = 995

WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102

HIDP_STATUS_SUCCESS = 0x00110000

_INVALID_HANDLE = ctypes.c_void_p(-1).value


class HidError(OSError):
    """An I/O error on an open HID handle (device unplugged, access lost)."""


class HidAborted(HidError):
    """The pending I/O was cancelled because the handle is being closed."""


if _IS_WINDOWS:

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("InterfaceClassGuid", _GUID),
            ("Flags", wintypes.DWORD),
            ("Reserved", ctypes.c_void_p),
        ]

    class _HIDD_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("Size", wintypes.ULONG),
            ("VendorID", wintypes.USHORT),
            ("ProductID", wintypes.USHORT),
            ("VersionNumber", wintypes.USHORT),
        ]

    class _HIDP_CAPS(ctypes.Structure):
        _fields_ = [
            ("Usage", wintypes.USHORT),
            ("UsagePage", wintypes.USHORT),
            ("InputReportByteLength", wintypes.USHORT),
            ("OutputReportByteLength", wintypes.USHORT),
            ("FeatureReportByteLength", wintypes.USHORT),
            ("Reserved", wintypes.USHORT * 17),
            ("NumberLinkCollectionNodes", wintypes.USHORT),
            ("NumberInputButtonCaps", wintypes.USHORT),
            ("NumberInputValueCaps", wintypes.USHORT),
            ("NumberInputDataIndices", wintypes.USHORT),
            ("NumberOutputButtonCaps", wintypes.USHORT),
            ("NumberOutputValueCaps", wintypes.USHORT),
            ("NumberOutputDataIndices", wintypes.USHORT),
            ("NumberFeatureButtonCaps", wintypes.USHORT),
            ("NumberFeatureValueCaps", wintypes.USHORT),
            ("NumberFeatureDataIndices", wintypes.USHORT),
        ]

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _hid = ctypes.WinDLL("hid", use_last_error=True)
    _sapi = ctypes.WinDLL("setupapi", use_last_error=True)

    _k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.ReadFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(_OVERLAPPED),
    ]
    _k32.ReadFile.restype = wintypes.BOOL
    _k32.WriteFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(_OVERLAPPED),
    ]
    _k32.WriteFile.restype = wintypes.BOOL
    _k32.GetOverlappedResult.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED),
        ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
    ]
    _k32.GetOverlappedResult.restype = wintypes.BOOL
    _k32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED)]
    _k32.CancelIoEx.restype = wintypes.BOOL
    _k32.CreateEventW.argtypes = [
        ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR,
    ]
    _k32.CreateEventW.restype = wintypes.HANDLE
    _k32.ResetEvent.argtypes = [wintypes.HANDLE]
    _k32.ResetEvent.restype = wintypes.BOOL
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.WaitForSingleObject.restype = wintypes.DWORD

    _hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(_GUID)]
    _hid.HidD_GetHidGuid.restype = None
    _hid.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(_HIDD_ATTRIBUTES)]
    _hid.HidD_GetAttributes.restype = wintypes.BOOLEAN
    _hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    _hid.HidD_GetPreparsedData.restype = wintypes.BOOLEAN
    _hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
    _hid.HidD_FreePreparsedData.restype = wintypes.BOOLEAN
    _hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(_HIDP_CAPS)]
    _hid.HidP_GetCaps.restype = wintypes.LONG
    _hid.HidD_GetProductString.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
    _hid.HidD_GetProductString.restype = wintypes.BOOLEAN

    _sapi.SetupDiGetClassDevsW.argtypes = [
        ctypes.POINTER(_GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD,
    ]
    _sapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    _sapi.SetupDiEnumDeviceInterfaces.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(_GUID), wintypes.DWORD,
        ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
    ]
    _sapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
    _sapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    _sapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
    _sapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
    _sapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL


def _require_windows() -> None:
    if not _IS_WINDOWS:
        raise OSError("HID access is only implemented for Windows")


def _last_error() -> int:
    return ctypes.get_last_error()


def _error(prefix: str, code: int) -> HidError:
    return HidError(f"{prefix} (Windows error {code}: {ctypes.FormatError(code).strip()})")


@dataclass(frozen=True)
class HidInfo:
    """One HID interface as Windows reports it."""

    path: str
    vendor_id: int
    product_id: int
    usage_page: int
    usage: int
    input_report_len: int    # bytes, including the leading report-id byte
    output_report_len: int   # bytes, including the leading report-id byte
    product: str = ""

    @property
    def label(self) -> str:
        return f"HID {self.vendor_id:04X}:{self.product_id:04X}"


def _query(path: str) -> Optional[HidInfo]:
    """Read one interface's ids and capabilities without opening it for I/O."""
    handle = _k32.CreateFileW(
        path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None
    )
    if handle is None or handle == _INVALID_HANDLE:
        return None
    try:
        attrs = _HIDD_ATTRIBUTES()
        attrs.Size = ctypes.sizeof(_HIDD_ATTRIBUTES)
        if not _hid.HidD_GetAttributes(handle, ctypes.byref(attrs)):
            return None
        preparsed = ctypes.c_void_p()
        if not _hid.HidD_GetPreparsedData(handle, ctypes.byref(preparsed)):
            return None
        try:
            caps = _HIDP_CAPS()
            if _hid.HidP_GetCaps(preparsed, ctypes.byref(caps)) != HIDP_STATUS_SUCCESS:
                return None
        finally:
            _hid.HidD_FreePreparsedData(preparsed)
        name = ctypes.create_unicode_buffer(128)
        product = ""
        if _hid.HidD_GetProductString(handle, name, ctypes.sizeof(name)):
            product = name.value
        return HidInfo(
            path=path,
            vendor_id=int(attrs.VendorID),
            product_id=int(attrs.ProductID),
            usage_page=int(caps.UsagePage),
            usage=int(caps.Usage),
            input_report_len=int(caps.InputReportByteLength),
            output_report_len=int(caps.OutputReportByteLength),
            product=product,
        )
    finally:
        _k32.CloseHandle(handle)


def enumerate_devices(
    vid: Optional[int] = None,
    pid: Optional[int] = None,
    usage_page: Optional[int] = None,
) -> list[HidInfo]:
    """Every present HID interface matching the given filters (None = any)."""
    _require_windows()
    guid = _GUID()
    _hid.HidD_GetHidGuid(ctypes.byref(guid))
    dev_info = _sapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    )
    if dev_info is None or dev_info == _INVALID_HANDLE:
        raise _error("SetupDiGetClassDevs failed", _last_error())
    found: list[HidInfo] = []
    try:
        index = 0
        # The detail struct's cbSize is the size of its fixed part, which is 8
        # on 64-bit builds (4-byte size + padding) and 6 on 32-bit ones.
        detail_cb = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
        while True:
            data = _SP_DEVICE_INTERFACE_DATA()
            data.cbSize = ctypes.sizeof(_SP_DEVICE_INTERFACE_DATA)
            if not _sapi.SetupDiEnumDeviceInterfaces(
                dev_info, None, ctypes.byref(guid), index, ctypes.byref(data)
            ):
                if _last_error() == ERROR_NO_MORE_ITEMS:
                    break
                raise _error("SetupDiEnumDeviceInterfaces failed", _last_error())
            index += 1

            needed = wintypes.DWORD(0)
            _sapi.SetupDiGetDeviceInterfaceDetailW(
                dev_info, ctypes.byref(data), None, 0, ctypes.byref(needed), None
            )
            if needed.value < detail_cb + 2:
                continue
            buf = ctypes.create_string_buffer(needed.value)
            ctypes.c_uint32.from_buffer(buf, 0).value = detail_cb
            if not _sapi.SetupDiGetDeviceInterfaceDetailW(
                dev_info, ctypes.byref(data), buf, needed.value, None, None
            ):
                continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            info = _query(path)
            if info is None:
                continue
            if vid is not None and info.vendor_id != vid:
                continue
            if pid is not None and info.product_id != pid:
                continue
            if usage_page is not None and info.usage_page != usage_page:
                continue
            found.append(info)
    finally:
        _sapi.SetupDiDestroyDeviceInfoList(dev_info)
    return found


class HidHandle:
    """An open HID interface. Reports are exchanged including the leading
    report-id byte (0 for interfaces that use no report ids), which is what
    Windows hands back and expects."""

    def __init__(self, handle: int, input_len: int, output_len: int, path: str = "") -> None:
        _require_windows()
        self.path = path
        self.input_len = int(input_len)
        self.output_len = int(output_len)
        self._handle: Optional[int] = handle
        self._read_ov = _OVERLAPPED()
        self._write_ov = _OVERLAPPED()
        self._read_ov.hEvent = self._new_event()
        self._write_ov.hEvent = self._new_event()
        self._read_buf = ctypes.create_string_buffer(max(1, self.input_len))
        self._read_pending = False

    @staticmethod
    def _new_event() -> int:
        event = _k32.CreateEventW(None, True, False, None)
        if not event:
            raise _error("CreateEvent failed", _last_error())
        return event

    @classmethod
    def open(cls, info: HidInfo) -> "HidHandle":
        _require_windows()
        handle = _k32.CreateFileW(
            info.path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_OVERLAPPED,
            None,
        )
        if handle is None or handle == _INVALID_HANDLE:
            raise _error("could not open the HID interface", _last_error())
        try:
            return cls(handle, info.input_report_len, info.output_report_len, info.path)
        except Exception:
            _k32.CloseHandle(handle)
            raise

    @property
    def is_open(self) -> bool:
        return self._handle is not None

    # ------------------------------------------------------------------ I/O

    def write_report(self, report: bytes, timeout_ms: int = 250) -> bool:
        """Send one output report. ``report`` is the payload without the
        report-id byte; it is padded to the interface's output length.
        Returns False if the device did not take it in time."""
        handle = self._handle
        if handle is None:
            raise HidError("the HID handle is closed")
        length = max(self.output_len, len(report) + 1)
        # Leading report-id byte (0: this interface uses no report ids), then
        # the report, zero padded to the length Windows expects.
        data = ctypes.create_string_buffer(b"\x00" + bytes(report), length)
        ov = self._write_ov
        _k32.ResetEvent(ov.hEvent)
        sent = wintypes.DWORD(0)
        if not _k32.WriteFile(handle, data, length, None, ctypes.byref(ov)):
            err = _last_error()
            if err != ERROR_IO_PENDING:
                raise _error("HID write failed", err)
        wait = _k32.WaitForSingleObject(ov.hEvent, int(timeout_ms))
        if wait == WAIT_TIMEOUT:
            # Withdraw just this write, then wait for the cancel to land so the
            # buffer is not touched after we return.
            _k32.CancelIoEx(handle, ctypes.byref(ov))
            _k32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(sent), True)
            return False
        if wait != WAIT_OBJECT_0:
            raise _error("waiting for the HID write failed", _last_error())
        if not _k32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(sent), False):
            err = _last_error()
            if err == ERROR_OPERATION_ABORTED:
                raise HidAborted("HID write cancelled")
            raise _error("HID write failed", err)
        return True

    def read_report(self, timeout_ms: int = 100) -> Optional[bytes]:
        """Wait up to ``timeout_ms`` for one input report. Returns the report
        (with its leading report-id byte), or None if nothing arrived. A read
        that times out stays outstanding, so nothing is dropped."""
        handle = self._handle
        if handle is None:
            raise HidError("the HID handle is closed")
        ov = self._read_ov
        if not self._read_pending:
            _k32.ResetEvent(ov.hEvent)
            if not _k32.ReadFile(handle, self._read_buf, self.input_len, None, ctypes.byref(ov)):
                err = _last_error()
                if err != ERROR_IO_PENDING:
                    raise _error("HID read failed", err)
            self._read_pending = True
        wait = _k32.WaitForSingleObject(ov.hEvent, int(timeout_ms))
        if wait == WAIT_TIMEOUT:
            return None
        if wait != WAIT_OBJECT_0:
            raise _error("waiting for the HID read failed", _last_error())
        got = wintypes.DWORD(0)
        ok = _k32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(got), False)
        self._read_pending = False
        if not ok:
            err = _last_error()
            if err == ERROR_OPERATION_ABORTED:
                raise HidAborted("HID read cancelled")
            raise _error("HID read failed", err)
        return bytes(self._read_buf.raw[: got.value])

    def cancel(self) -> None:
        """Abort every pending read and write. Used to unblock threads that
        are waiting on this handle before it is closed."""
        handle = self._handle
        if handle is not None:
            _k32.CancelIoEx(handle, None)

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        _k32.CancelIoEx(handle, None)
        if self._read_pending:
            got = wintypes.DWORD(0)
            # Let the cancelled read finish so its buffer can be freed.
            _k32.GetOverlappedResult(handle, ctypes.byref(self._read_ov), ctypes.byref(got), True)
            self._read_pending = False
        _k32.CloseHandle(handle)
        for ov in (self._read_ov, self._write_ov):
            if ov.hEvent:
                _k32.CloseHandle(ov.hEvent)
                ov.hEvent = None
