"""CS2 inventory module: Steam sign-in, inventory, CSFloat prices, and API keys.

Ported from DaizSkins. Steam is read through a hidden browser window that keeps
the Steam sign-in cookies, so newly unboxed and trade-protected items show up
exactly as they do on Steam. Prices are the lowest CSFloat buy-now listing.

Everything lives in ./inventory next to Argus and is kept apart from the aim
configs, so switching configs never touches it. API keys are encrypted with
Windows DPAPI (tied to this Windows user).
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import webview

if sys.platform == "win32":
    from ctypes import wintypes

DATA_DIR = Path(__file__).resolve().parent / "inventory"
STATE_PATH = DATA_DIR / "state.json"
ITEMS_PATH = DATA_DIR / "items.json"
PRICES_PATH = DATA_DIR / "prices.json"
PRICELIST_PATH = DATA_DIR / "pricelist.json"
KEYS_PATH = DATA_DIR / "keys.json"
ICONBOX_PATH = DATA_DIR / "iconbox.json"

STEAM64_BASE = 76561197960265728
CS2_APPID = 730
CS2_CONTEXTID = 2
CS2_PROTECTED_CONTEXTID = 16
STEAM_INV_COUNT = 75
STEAM_ICON_CDN = "https://community.akamai.steamstatic.com/economy/image"
STEAM_COMMUNITY_HOME = "https://steamcommunity.com/"
STEAM_LOGIN_URL = "https://steamcommunity.com/login/home/?goto="
STEAM_RATE_LIMIT_SEC = 2 * 60 * 60
STEAM_PAGE_GAP_SEC = 2.5
STEAM_MAX_REQUESTS_PER_MIN = 6
STEAM_LOGIN_TIMEOUT_SEC = 600
INVENTORY_STALE_SEC = 10 * 60

CSFLOAT_LISTINGS_URL = "https://csfloat.com/api/v1/listings"
CSFLOAT_PRICE_LIST_URL = "https://csfloat.com/api/v1/listings/price-list"
CSFLOAT_PRICE_TTL = 2 * 60 * 60
CSFLOAT_MISS_TTL = 15 * 60
CSFLOAT_GAP_SEC = 1.05
CSFLOAT_SKIN_CATEGORIES = {"weapons", "knives", "gloves"}

MAX_SECRET_LEN = 8192
_SECRETS_ENTROPY = b"ArgusAI.cs2inventory.keys.v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01
SW_RESTORE = 9

API_KEYS = {
    "csfloat": {
        "label": "CSFloat",
        "use": "Estimated prices for your CS2 inventory.",
        "url": "https://csfloat.com/profile",
        "where": "CSFloat profile, Developer tab",
    },
    "leetify": {
        "label": "Leetify",
        "use": "CS2 stats. Optional: without a key Leetify applies stricter rate limits.",
        "url": "https://leetify.com/app/developer",
        "where": "Leetify, Developer page",
    },
}

_IO_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()
_SECRETS_LOCK = threading.Lock()
_SECRETS_MEM: dict[str, str] | None = None
_STEAM_REQ_LOCK = threading.Lock()
_steam_req_times: list[float] = []
_STEAM_WIN_LOCK = threading.Lock()
_STEAM_WIN: Any = None
_LOGIN_WIN: Any = None
_LOGIN_CLOSED: threading.Event | None = None
_STEAM_BRIDGE: "SteamInvBridge | None" = None
_STEAM_LOGIN_LOCK = threading.Lock()
_steam_login_busy = False
_INVENTORY_FETCH_LOCK = threading.Lock()
_inventory_fetching = False
_PRICE_LOCK = threading.Lock()
_PRICE_QUEUE: list[tuple[str, str]] = []
_PRICE_QUEUED: set[str] = set()
_PRICE_THREAD: threading.Thread | None = None
_PRICE_CACHE: dict[str, dict[str, Any]] | None = None
_PRICELIST: dict[str, int] | None = None
_PRICELIST_AT = 0.0
_PRICELIST_LOADING = False
_CSFLOAT_AUTH_BAD = False
_ITEMS_MEM: dict[str, Any] | None = None
_ITEMS_LOADED = False
_SHUTDOWN = False
_ICONBOX_LOCK = threading.Lock()
_ICONBOX: dict[str, list[float]] | None = None
_ICONBOX_PENDING: set[str] = set()
_ICONBOX_FAILED: set[str] = set()
_ICONBOX_THREAD: threading.Thread | None = None


# ----------------------------------------------------------------------------
# JSON storage
# ----------------------------------------------------------------------------

def _read_json(path: Path, fallback: Any) -> Any:
    try:
        with _IO_LOCK:
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return fallback


def _write_json(path: Path, payload: Any) -> None:
    with _IO_LOCK:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)


def _load_state() -> dict[str, Any]:
    raw = _read_json(STATE_PATH, {})
    return raw if isinstance(raw, dict) else {}


def _update_state(**changes: Any) -> dict[str, Any]:
    with _STATE_LOCK:
        state = _load_state()
        state.update(changes)
        try:
            _write_json(STATE_PATH, state)
        except OSError:
            pass
        return state


def _cooldown_left() -> int:
    try:
        until = float(_load_state().get("cooldownUntil") or 0)
    except (TypeError, ValueError):
        until = 0.0
    return max(0, int(until - time.time()))


def _mark_cooldown(seconds: int) -> int:
    wait = max(1, int(seconds))
    _update_state(cooldownUntil=time.time() + wait)
    return wait


def _load_items() -> dict[str, Any] | None:
    global _ITEMS_MEM, _ITEMS_LOADED
    if not _ITEMS_LOADED:
        raw = _read_json(ITEMS_PATH, None)
        _ITEMS_MEM = raw if isinstance(raw, dict) and isinstance(raw.get("items"), list) else None
        _ITEMS_LOADED = True
    return _ITEMS_MEM


def _save_items(payload: dict[str, Any] | None) -> None:
    global _ITEMS_MEM, _ITEMS_LOADED
    _ITEMS_MEM, _ITEMS_LOADED = payload, True
    try:
        if payload is None:
            ITEMS_PATH.unlink(missing_ok=True)
        else:
            _write_json(ITEMS_PATH, payload)
    except OSError:
        pass


# ----------------------------------------------------------------------------
# API keys (DPAPI)
# ----------------------------------------------------------------------------

if sys.platform == "win32":
    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]
else:
    _DATA_BLOB = None  # type: ignore[misc, assignment]

_crypt32 = None
_kernel32 = None


def _secrets_dlls():
    global _crypt32, _kernel32
    if sys.platform != "win32":
        raise OSError("API key encryption requires Windows")
    if _crypt32 is None:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        for fn in (crypt32.CryptProtectData, crypt32.CryptUnprotectData):
            fn.restype = wintypes.BOOL
        crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_DATA_BLOB),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
        ]
        crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DATA_BLOB),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
        ]
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        _crypt32, _kernel32 = crypt32, kernel32
    return _crypt32, _kernel32


def _dpapi(data: bytes, protect: bool) -> bytes:
    if not data:
        return b""
    crypt32, kernel32 = _secrets_dlls()
    buf_in = ctypes.create_string_buffer(data, len(data))
    buf_ent = ctypes.create_string_buffer(_SECRETS_ENTROPY, len(_SECRETS_ENTROPY))
    blob_in = _DATA_BLOB(len(data), ctypes.cast(buf_in, ctypes.POINTER(ctypes.c_ubyte)))
    blob_ent = _DATA_BLOB(len(_SECRETS_ENTROPY), ctypes.cast(buf_ent, ctypes.POINTER(ctypes.c_ubyte)))
    blob_out = _DATA_BLOB()
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(blob_in), "Argus AI", ctypes.byref(blob_ent), None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, ctypes.byref(blob_ent), None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out),
        )
    if not ok:
        raise OSError(ctypes.get_last_error() or "DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        if blob_out.pbData:
            kernel32.LocalFree(blob_out.pbData)


def _load_secrets() -> dict[str, str]:
    global _SECRETS_MEM
    with _SECRETS_LOCK:
        if _SECRETS_MEM is not None:
            return dict(_SECRETS_MEM)
        rows = _read_json(KEYS_PATH, {})
        out: dict[str, str] = {}
        if isinstance(rows, dict):
            for name, blob in rows.items():
                if name not in API_KEYS or not isinstance(blob, str):
                    continue
                try:
                    text = _dpapi(base64.b64decode(blob.encode("ascii")), False).decode("utf-8").strip()
                except (OSError, ValueError, UnicodeDecodeError):
                    continue
                if text:
                    out[name] = text[:MAX_SECRET_LEN]
        _SECRETS_MEM = out
        return dict(out)


def _save_secrets(secrets: dict[str, str]) -> None:
    global _SECRETS_MEM
    clean = {name: str(value).strip()[:MAX_SECRET_LEN] for name, value in secrets.items() if name in API_KEYS and value}
    rows = {name: base64.b64encode(_dpapi(value.encode("utf-8"), True)).decode("ascii") for name, value in clean.items()}
    with _SECRETS_LOCK:
        _write_json(KEYS_PATH, rows)
        _SECRETS_MEM = clean


def _secret(name: str) -> str:
    return _load_secrets().get(name, "")


def _mask_secret(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return "•" * 4 if len(text) <= 4 else "•" * 4 + text[-4:]


def _csfloat_headers(key: str) -> dict[str, str]:
    return {"Accept": "application/json", "User-Agent": "ArgusAI/1.0", "Authorization": str(key or "").strip()}


def _validate_csfloat_key(key: str) -> int:
    token = str(key or "").strip()
    if not token:
        return 401
    url = f"{CSFLOAT_LISTINGS_URL}?{urllib.parse.urlencode({'limit': '1', 'type': 'buy_now'})}"
    request = urllib.request.Request(url, method="GET", headers=_csfloat_headers(token))
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return int(getattr(response, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        return int(exc.code or 401)
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0


def _key_rows() -> list[dict[str, Any]]:
    secrets = _load_secrets()
    rows = []
    for name, meta in API_KEYS.items():
        value = secrets.get(name, "")
        rows.append({
            "name": name,
            **meta,
            "set": bool(value),
            "masked": _mask_secret(value),
            "valid": bool(value) and not (name == "csfloat" and _CSFLOAT_AUTH_BAD),
            "unverified": bool(value) and name == "leetify" and not _load_state().get("leetifyKeyVerified", True),
        })
    return rows


# ----------------------------------------------------------------------------
# Steam identity
# ----------------------------------------------------------------------------

def _as_steam64(value: object) -> str:
    text = str(value or "").strip()
    found = re.search(r"(7656119\d{10,})", text)
    if found:
        return found.group(1)
    if text.isdigit() and 1 <= len(text) <= 16:
        account = int(text)
        return str(account + STEAM64_BASE) if 0 < account < STEAM64_BASE else ""
    return ""


def _saved_steam64() -> str:
    return _as_steam64(_load_state().get("steam64"))


def _remember_steam64(steamid: str) -> str:
    sid = _as_steam64(steamid)
    if sid and sid != _saved_steam64():
        _update_state(steam64=sid, persona="", avatar="")
    return sid


def _fetch_steam_persona(steamid: str) -> dict[str, str]:
    sid = _as_steam64(steamid)
    if not sid:
        return {"persona": "", "avatar": "", "steam64": ""}
    try:
        with urllib.request.urlopen(f"https://steamcommunity.com/profiles/{sid}/?xml=1", timeout=12) as response:
            root = ElementTree.fromstring(response.read())
        persona = (root.findtext("steamID") or "").strip()
        avatar = (root.findtext("avatarFull") or root.findtext("avatarMedium") or "").strip()
        _update_state(persona=persona, avatar=avatar)
        return {"persona": persona, "avatar": avatar, "steam64": sid}
    except (urllib.error.URLError, TimeoutError, OSError, ElementTree.ParseError):
        state = _load_state()
        return {"persona": str(state.get("persona") or ""), "avatar": str(state.get("avatar") or ""), "steam64": sid}


# ----------------------------------------------------------------------------
# Steam browser window
# ----------------------------------------------------------------------------

class SteamInvBridge:
    """js_api for the Steam window: large inventory pages are streamed back in chunks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._parts: list[str] = []
        self._status = 0
        self._retry_after = ""
        self._error = ""
        self._event = threading.Event()

    def reset(self) -> None:
        with self._lock:
            self._parts = []
            self._status = 0
            self._retry_after = ""
            self._error = ""
            self._event.clear()

    def begin(self, status: object = 0, retry_after: object = "", length: object = 0) -> bool:
        with self._lock:
            self._parts = []
            try:
                self._status = int(status or 0)
            except (TypeError, ValueError):
                self._status = 0
            self._retry_after = str(retry_after or "")
            self._error = ""
            self._event.clear()
        return True

    def push(self, part: object = "") -> bool:
        if isinstance(part, dict):
            raw = part.get("t")
            text = raw if isinstance(raw, str) else "" if raw is None else str(raw)
        else:
            text = part if isinstance(part, str) else "" if part is None else str(part)
        with self._lock:
            self._parts.append(text)
        return True

    def fail(self, error: object = "") -> bool:
        with self._lock:
            self._error = str(error or "Steam inventory request failed")
            self._event.set()
        return True

    def finish(self) -> bool:
        self._event.set()
        return True

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)

    def result(self) -> tuple[str, int, str, str]:
        with self._lock:
            return "".join(self._parts), self._status, self._retry_after, self._error


def _steam_bridge() -> SteamInvBridge:
    global _STEAM_BRIDGE
    if _STEAM_BRIDGE is None:
        _STEAM_BRIDGE = SteamInvBridge()
    return _STEAM_BRIDGE


def _destroy_window(window) -> None:
    if window is None:
        return
    try:
        window.destroy()
    except Exception:
        pass


def _steam_window():
    """Hidden window used to read Steam. Never shown, resized, or made topmost.

    pywebview's WinForms resize() re-shows a hidden window and on_top is set
    straight from the calling thread, so this window is only ever created hidden
    and destroyed, never reconfigured.
    """
    global _STEAM_WIN
    with _STEAM_WIN_LOCK:
        window = _STEAM_WIN
    if window is not None:
        return window, False
    window = webview.create_window(
        "Argus Steam", STEAM_COMMUNITY_HOME, width=420, height=320,
        hidden=True, focus=False, text_select=True, js_api=_steam_bridge(),
    )
    if window is None:
        raise ValueError("Could not open a Steam browser window.")

    def closed() -> None:
        global _STEAM_WIN
        with _STEAM_WIN_LOCK:
            if _STEAM_WIN is window:
                _STEAM_WIN = None

    window.events.closed += closed
    with _STEAM_WIN_LOCK:
        _STEAM_WIN = window
    return window, True


def _reset_steam_window() -> None:
    """Drop the hidden window so the next read starts from a fresh page (new account, signed out)."""
    global _STEAM_WIN
    with _STEAM_WIN_LOCK:
        window, _STEAM_WIN = _STEAM_WIN, None
    _destroy_window(window)


def _webview_hwnd(window) -> int:
    handle = getattr(getattr(window, "native", None), "Handle", None)
    if handle is None:
        return 0
    try:
        if hasattr(handle, "ToInt64"):
            return int(handle.ToInt64())
        if hasattr(handle, "ToInt32"):
            return int(handle.ToInt32())
        return int(handle)
    except (TypeError, ValueError, OverflowError, AttributeError):
        return 0


def _focus_webview(window) -> None:
    hwnd = _webview_hwnd(window)
    if not hwnd or sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def _steam_login_url() -> str:
    sid = _saved_steam64()
    return STEAM_LOGIN_URL + urllib.parse.quote(f"profiles/{sid}/inventory" if sid else "my/inventory")


def _open_login_window():
    """A fresh, always-on-top sign-in popup. Destroyed as soon as sign-in ends."""
    global _LOGIN_WIN, _LOGIN_CLOSED
    closed_event = threading.Event()
    window = webview.create_window(
        "Sign in to Steam", _steam_login_url(), width=540, height=780,
        focus=True, on_top=True, text_select=True,
    )
    if window is None:
        raise ValueError("Could not open a Steam sign-in window.")

    def closed() -> None:
        closed_event.set()

    window.events.closed += closed
    with _STEAM_WIN_LOCK:
        _LOGIN_WIN, _LOGIN_CLOSED = window, closed_event
    return window, closed_event


def _close_login_window() -> None:
    global _LOGIN_WIN, _LOGIN_CLOSED
    with _STEAM_WIN_LOCK:
        window, event = _LOGIN_WIN, _LOGIN_CLOSED
        _LOGIN_WIN, _LOGIN_CLOSED = None, None
    if event is not None:
        event.set()
    _destroy_window(window)


def _steam_eval(window, script: str, timeout: float = 45) -> Any:
    done = threading.Event()
    holder: dict[str, Any] = {}

    def got(result: object) -> None:
        holder["result"] = result
        done.set()

    # pywebview only calls the callback when the script returns a Promise; a plain
    # value (e.g. g_steamID already set once signed in) would never arrive.
    try:
        window.evaluate_js(f"Promise.resolve({script.strip().rstrip(';')})", got)
    except Exception as exc:
        raise ValueError("Could not read Steam through the browser window.") from exc
    if not done.wait(timeout):
        raise ValueError("Steam request timed out.")
    return holder.get("result")


def _eval_object(raw: object) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _steam_session_steamid(window: Any = None) -> str:
    if window is None:
        with _STEAM_WIN_LOCK:
            window = _STEAM_WIN
    if window is None:
        return ""
    script = (
        "(function(){"
        "var href=String(location.href||'');"
        "var id=String(window.g_steamID||'');"
        "if(/^7656\\d+$/.test(id)) return {href:href,id:id};"
        "return fetch('https://steamcommunity.com/my?xml=1',{credentials:'include'})"
        ".then(function(r){return r.text();})"
        ".then(function(t){var m=t.match(/<steamID64>(\\d+)<\\/steamID64>/i);return {href:href,id:m?m[1]:''};})"
        ".catch(function(){return {href:href,id:''};});"
        "})()"
    )
    try:
        info = _eval_object(_steam_eval(window, script, timeout=12))
    except ValueError:
        return ""
    href = str(info.get("href") or "").lower()
    if "login." in href or "/login" in href or "steamcommunity.com" not in href:
        return ""
    return _as_steam64(str(info.get("id") or ""))


# ----------------------------------------------------------------------------
# Inventory parsing
# ----------------------------------------------------------------------------

_INV_RARITY_HEX = {
    "b0c3d9": "consumer", "5e98d9": "industrial", "4b69ff": "milspec", "8847ff": "restricted",
    "d32ce6": "classified", "eb4b4b": "covert", "e4ae39": "gold", "ffd700": "gold", "e4ae33": "gold",
}
_INV_CONTAINS_RE = re.compile(r"contains one of the following", re.I)
_INV_RARE_SPECIAL_RE = re.compile(r"exceedingly rare|rare special item", re.I)
_INV_HTML_COLOR_RE = re.compile(r"<(font|span)([^>]*)>(.*?)</\1>", re.I | re.S)
_INV_ATTR_COLOR_RE = re.compile(r"(?:color\s*=\s*['\"]?#?|color\s*:\s*#?)([0-9a-fA-F]{3,8})", re.I)


def _inv_tag_map(tags: object) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(tags, list):
        return out
    for tag in tags:
        if not isinstance(tag, dict):
            continue
        category = str(tag.get("category") or "").strip().lower()
        label = str(tag.get("localized_tag_name") or tag.get("name") or "").strip()
        if category and label:
            out[category] = label
    return out


def _inv_plain_text(value: object) -> str:
    text = re.sub(r"<br\s*/?>", "\n", str(value or ""), flags=re.I)
    text = re.sub(r"<img[^>]*>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text).replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _index_asset_properties(raw: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    rows = raw.get("asset_properties")
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        aid = str(row.get("assetid") or row.get("id") or "")
        nested = row.get("asset_properties")
        if not isinstance(nested, list):
            nested = row.get("properties")
        if isinstance(nested, list):
            props = [item for item in nested if isinstance(item, dict)]
            if aid and props:
                out.setdefault(aid, []).extend(props)
            continue
        if aid and (row.get("propertyid") is not None or row.get("name")):
            out.setdefault(aid, []).append(row)
    return out


def _prop_value(row: dict[str, Any]) -> str:
    for key in ("string_value", "int_value", "float_value", "value"):
        value = row.get(key)
        if value is not None and value != "":
            return str(value)
    return ""


def _parse_asset_props(rows: object) -> tuple[float | None, int | None, str]:
    wear: float | None = None
    pattern: int | None = None
    nametag = ""
    if not isinstance(rows, list):
        return wear, pattern, nametag
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("property_name") or row.get("localized_label") or "").lower()
        try:
            pid = int(row.get("propertyid") if row.get("propertyid") is not None else row.get("id"))
        except (TypeError, ValueError):
            pid = None
        raw_string = row.get("string_value") if row.get("string_value") is not None else row.get("value")
        number: float | None = None
        for candidate in (row.get("float_value"), row.get("int_value"), raw_string):
            if candidate is None or candidate == "":
                continue
            try:
                number = float(candidate)
                break
            except (TypeError, ValueError):
                continue
        if "name tag" in name or "nametag" in name or pid == 5:
            text = str(raw_string or "").strip()
            if text:
                nametag = text
            continue
        if ("wear" in name or pid == 2) and number is not None and 0 <= number <= 1:
            wear = number
            continue
        if ("pattern" in name or "paint seed" in name or "paintseed" in name or pid == 1) and number is not None:
            pattern = int(number)
    return wear, pattern, nametag


def _sticker_rarity_from_name(name: str) -> str:
    label = str(name or "").lower()
    if "(gold)" in label:
        return "covert"
    if "(holo)" in label or "(foil)" in label or "(lenticular)" in label:
        return "classified"
    if "(glitter)" in label:
        return "restricted"
    return ""


def _inv_hex_rarity(color: str) -> str:
    key = re.sub(r"[^0-9a-f]", "", str(color or "").lower())
    if len(key) == 3:
        key = "".join(ch * 2 for ch in key)
    return _INV_RARITY_HEX.get(key[:6], "")


def _inv_colored_fragments(html: str, default_color: str = "") -> list[tuple[str, str]]:
    text = str(html or "")
    if not text.strip():
        return []
    parts: list[tuple[str, str]] = []
    for match in _INV_HTML_COLOR_RE.finditer(text):
        found = _INV_ATTR_COLOR_RE.search(match.group(2) or "")
        name = _inv_plain_text(match.group(3) or "")
        if name:
            parts.append((name, found.group(1) if found else default_color))
    if parts:
        return parts
    plain = _inv_plain_text(text)
    return [(line, default_color) for line in plain.split("\n") if line] if plain else []


def _inv_stickers(desc: dict[str, Any]) -> list[dict[str, str]]:
    names: list[str] = []
    icons: list[str] = []
    colors: list[str] = []
    rows = desc.get("descriptions")
    if not isinstance(rows, list):
        return []
    for row in rows:
        value = str(row.get("value") or "") if isinstance(row, dict) else str(row or "")
        row_color = str(row.get("color") or "") if isinstance(row, dict) else ""
        if "sticker" not in value.lower() and "<img" not in value.lower():
            continue
        icons.extend(re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', value, flags=re.I))
        found = re.search(r"sticker:\s*(.+)", _inv_plain_text(value), re.I)
        if found:
            parts = [part.strip() for part in found.group(1).split(",") if part.strip()]
            names.extend(parts)
            tint = row_color
            if not tint:
                colored = _inv_colored_fragments(value, "")
                tint = colored[0][1] if colored else ""
            colors.extend([tint] * len(parts))
    out: list[dict[str, str]] = []
    for index in range(max(len(names), len(icons))):
        name = names[index] if index < len(names) else ""
        icon = icons[index] if index < len(icons) else ""
        if not name and not icon:
            continue
        color = colors[index] if index < len(colors) else ""
        out.append({"name": name, "icon": icon, "rarity": _inv_hex_rarity(color) or _sticker_rarity_from_name(name)})
    return out


def _inv_contents(desc: dict[str, Any]) -> list[dict[str, str]]:
    rows = desc.get("descriptions")
    if not isinstance(rows, list):
        return []
    started = False
    seen: set[str] = set()
    out: list[dict[str, str]] = []

    def add(name: str, color: str, *, listed: bool = False) -> None:
        label = " ".join(str(name or "").split())
        if not label or _INV_CONTAINS_RE.search(label):
            return
        rarity = _inv_hex_rarity(color)
        if _INV_RARE_SPECIAL_RE.search(label):
            rarity = rarity or "gold"
        if not listed and not rarity and "|" not in label:
            return
        if label.lower() in seen:
            return
        seen.add(label.lower())
        out.append({"name": label, "rarity": rarity or "other"})

    for row in rows:
        raw = str(row.get("value") or "") if isinstance(row, dict) else str(row or "")
        color = str(row.get("color") or "") if isinstance(row, dict) else ""
        if _INV_CONTAINS_RE.search(_inv_plain_text(raw) or raw):
            started = True
            for name, tint in _inv_colored_fragments(raw, color):
                if not _INV_CONTAINS_RE.search(name):
                    add(name, tint, listed=True)
            continue
        for name, tint in _inv_colored_fragments(raw, color):
            if started or _inv_hex_rarity(tint) or _INV_RARE_SPECIAL_RE.search(name):
                add(name, tint, listed=started)
    return out


def _inv_trade_hold(desc: dict[str, Any]) -> str:
    rows = desc.get("owner_descriptions")
    if not isinstance(rows, list):
        return ""
    for row in rows:
        text = _inv_plain_text(row.get("value") if isinstance(row, dict) else row)
        if re.search(r"tradable|marketable after", text, re.I):
            return text
    return ""


def _inv_notes(desc: dict[str, Any], contents: list[dict[str, str]]) -> list[str]:
    skip = re.compile(r"^(sticker:|exterior:|posted for sale)", re.I)
    listed = {row["name"].lower() for row in contents}
    notes: list[str] = []
    rows: list[Any] = []
    for key in ("owner_descriptions", "descriptions"):
        blob = desc.get(key)
        if isinstance(blob, list):
            rows.extend(blob)
    for row in rows:
        value = row.get("value") if isinstance(row, dict) else row
        blob = str(value or "").lower()
        if "sticker_info" in blob or ("<img" in blob and "sticker" in blob):
            continue
        text = _inv_plain_text(value)
        if not text or skip.search(text) or text in notes:
            continue
        if _INV_CONTAINS_RE.search(text) or text.lower() in listed or ("|" in text and listed):
            continue
        notes.append(text)
        if len(notes) >= 12:
            break
    return notes


def _inv_nametag(desc: dict[str, Any]) -> str:
    warnings = desc.get("fraudwarnings")
    rows = warnings if isinstance(warnings, list) else [warnings] if warnings else []
    for row in rows:
        text = _inv_plain_text(row)
        found = re.search(r"name tag:\s*['\"](.+?)['\"]", text, re.I)
        if found:
            return found.group(1).strip()
        if text:
            return text
    return ""


def _inv_inspect(desc: dict[str, Any], steamid: str, assetid: str, props: list[dict[str, Any]]) -> str:
    values: dict[str, str] = {}
    for row in props:
        try:
            pid = int(row.get("propertyid") if row.get("propertyid") is not None else row.get("id"))
        except (TypeError, ValueError):
            continue
        text = _prop_value(row)
        if text:
            values[str(pid)] = text
    link = ""
    for key in ("actions", "market_actions"):
        rows = desc.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            candidate = str(row.get("link") or "").strip()
            if "csgo_econ_action_preview" in candidate or "inspect" in str(row.get("name") or "").lower():
                link = candidate
                break
        if link:
            break
    if not link:
        return ""
    link = link.replace("%owner_steamid%", steamid).replace("%assetid%", assetid)
    link = re.sub(r"%propid:(\d+)%", lambda m: values.get(m.group(1) or "", m.group(0)), link)
    return "" if "%propid:" in link else link


def _inv_rarity_class(name: str) -> str:
    key = "".join(ch for ch in name.lower() if ch.isalnum())
    if "highgrade" in key:
        return "milspec"
    if "remarkable" in key:
        return "restricted"
    if "exotic" in key:
        return "classified"
    if "contraband" in key:
        return "gold"
    if "sticker" in key and "extraordinary" in key:
        return "covert"
    if "extraordinary" in key or "gold" in key:
        return "gold"
    if "covert" in key or "ancient" in key:
        return "covert"
    if "classified" in key or "legendary" in key:
        return "classified"
    if "restricted" in key or "mythical" in key:
        return "restricted"
    if "milspec" in key or ("rare" in key and "remarkable" not in key):
        return "milspec"
    if "industrial" in key or "uncommon" in key:
        return "industrial"
    if "consumer" in key or "common" in key or "basegrade" in key:
        return "consumer"
    return "other"


def _inv_category(type_name: str, tags: dict[str, str], item_name: str) -> str:
    blob = f"{type_name} {tags.get('type', '')} {tags.get('weapon', '')} {item_name}".lower()
    if "★" in item_name or "knife" in blob:
        return "knives"
    if "glove" in blob:
        return "gloves"
    if "agent" in blob:
        return "agents"
    if "sticker" in blob:
        return "stickers"
    if "music kit" in blob:
        return "music"
    if "graffiti" in blob:
        return "graffiti"
    if "container" in blob or "case" in blob or "package" in blob:
        return "cases"
    if tags.get("weapon") or any(w in blob for w in ("rifle", "pistol", "smg", "shotgun", "sniper", "machinegun", "gun")):
        return "weapons"
    return "other"


def _steam_icon_url(icon: object, size: str | None = "256fx256f") -> str:
    token = str(icon or "").strip().lstrip("/")
    if not token:
        return ""
    if token.startswith(("http://", "https://")):
        if size is None:
            return re.sub(r"/\d+f?x\d+f?/?$", "", token)
        return token if re.search(r"/\d+f?x\d+f?/?$", token) else f"{token}/{size}"
    return f"{STEAM_ICON_CDN}/{token}" if size is None else f"{STEAM_ICON_CDN}/{token}/{size}"


def _flag(value: object) -> bool:
    return bool(int(value)) if str(value).isdigit() else bool(value)


def _slim_inventory_item(
    asset: dict[str, Any],
    desc_index: dict[tuple[str, str], dict[str, Any]],
    class_index: dict[str, dict[str, Any]],
    contextid: str,
    steamid: str,
    extra_props: list[dict[str, Any]],
) -> dict[str, Any] | None:
    classid = str(asset.get("classid") or "")
    instanceid = str(asset.get("instanceid") or "0")
    assetid = str(asset.get("assetid") or asset.get("id") or "")
    if not assetid:
        return None
    merged = dict(desc_index.get((classid, instanceid)) or class_index.get(classid) or {})
    tags = _inv_tag_map(merged.get("tags"))
    name = str(merged.get("market_hash_name") or merged.get("market_name") or merged.get("name") or "Unknown")
    type_name = str(merged.get("type") or tags.get("type") or "")
    rarity_name = tags.get("rarity") or ""
    own_props: list[dict[str, Any]] = []
    asset_props = asset.get("asset_properties") if isinstance(asset.get("asset_properties"), list) else []
    for row in [*extra_props, *asset_props]:
        if not isinstance(row, dict):
            continue
        inner = row.get("asset_properties")
        if isinstance(inner, list) and row.get("propertyid") is None:
            own_props.extend(item for item in inner if isinstance(item, dict))
        else:
            own_props.append(row)
    wear, pattern, prop_nametag = _parse_asset_props(own_props)
    contents = _inv_contents(merged)
    notes = _inv_notes(merged, contents)
    trade_hold = _inv_trade_hold(merged)
    if trade_hold and trade_hold not in notes:
        notes.insert(0, trade_hold)
    joined = " ".join(notes)
    if wear is None:
        found = re.search(r"(?:float(?:\s*value)?|wear(?:\s*rating)?)\s*[:=]\s*(0\.\d+)", joined, re.I)
        wear = float(found.group(1)) if found else None
    if pattern is None:
        found = re.search(r"(?:paint\s*seed|pattern(?:\s*template)?)\s*[:=]\s*(\d+)", joined, re.I)
        pattern = int(found.group(1)) if found else None
    try:
        amount = max(1, int(asset.get("amount") or 1))
    except (TypeError, ValueError):
        amount = 1
    quality = tags.get("quality") or ""
    lowered = name.lower()
    return {
        "id": assetid,
        "name": name,
        "shortName": str(merged.get("name") or name),
        "type": type_name,
        "category": _inv_category(type_name, tags, name),
        "rarity": _inv_rarity_class(f"{rarity_name} {type_name}"),
        "rarityName": rarity_name,
        "exterior": tags.get("exterior") or "",
        "weapon": tags.get("weapon") or "",
        "collection": tags.get("itemset") or tags.get("collection") or "",
        "icon": _steam_icon_url(merged.get("icon_url") or merged.get("icon_url_large")),
        "iconLarge": _steam_icon_url(merged.get("icon_url_large") or merged.get("icon_url"), "512fx384f"),
        "amount": amount,
        "wear": wear,
        "pattern": pattern,
        "nametag": prop_nametag or _inv_nametag(merged),
        "stickers": _inv_stickers(merged),
        "contents": contents,
        "notes": notes,
        "inspect": _inv_inspect(merged, steamid, assetid, own_props),
        "tradable": _flag(merged.get("tradable", "")),
        "marketable": _flag(merged.get("marketable", "")),
        "tradeHold": trade_hold,
        "statTrak": "stattrak" in lowered.replace("-", "") or "strange" in quality.lower(),
        "souvenir": "souvenir" in lowered or "tournament" in quality.lower(),
        "contextId": str(merged.get("contextid") or asset.get("contextid") or contextid),
    }


# ----------------------------------------------------------------------------
# Inventory fetch
# ----------------------------------------------------------------------------

def _inventory_wait_error(remaining: int) -> str:
    mins = max(1, int((remaining + 59) // 60))
    if mins >= 120:
        return f"Steam is still blocking inventory requests. Wait about {max(2, (mins + 59) // 60)} hours, then refresh once."
    return f"Steam is rate-limiting inventory requests. Wait about {mins} min, then refresh once."


def _retry_after_seconds(raw: object) -> int:
    text = str(raw or "").strip()
    if text.isdigit():
        return max(int(text), STEAM_RATE_LIMIT_SEC)
    if not text:
        return STEAM_RATE_LIMIT_SEC
    try:
        when = parsedate_to_datetime(text)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(int(when.timestamp() - time.time()), STEAM_RATE_LIMIT_SEC)
    except (TypeError, ValueError, OverflowError, OSError):
        return STEAM_RATE_LIMIT_SEC


def _pace_steam_request() -> None:
    global _steam_req_times
    while True:
        with _STEAM_REQ_LOCK:
            now = time.time()
            _steam_req_times = [stamp for stamp in _steam_req_times if now - stamp < 60]
            if len(_steam_req_times) >= STEAM_MAX_REQUESTS_PER_MIN:
                sleep_for = max(0.05, 60.0 - (now - _steam_req_times[0]) + 0.05)
            elif _steam_req_times and STEAM_PAGE_GAP_SEC - (now - _steam_req_times[-1]) > 0:
                sleep_for = STEAM_PAGE_GAP_SEC - (now - _steam_req_times[-1])
            else:
                _steam_req_times.append(now)
                return
        time.sleep(sleep_for)


def _empty_page() -> dict[str, Any]:
    return {"success": 1, "assets": [], "descriptions": [], "total_inventory_count": 0, "more_items": 0}


def _parse_inventory_payload(text: str) -> dict[str, Any]:
    body = str(text or "").strip()
    lowered = body.lower()
    if not body:
        raise ValueError("Steam returned an empty inventory page. Try again in a few minutes.")
    if body.startswith("<") or "too many requests" in lowered or "access denied" in lowered or "error 429" in lowered:
        raise ValueError(_inventory_wait_error(_mark_cooldown(_retry_after_seconds(None))))
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("Steam returned a page Argus could not read. Try again in a few minutes.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Steam returned a page Argus could not read. Try again in a few minutes.")
    return parsed


def _read_window_body(window, length: object) -> str:
    try:
        total = max(0, int(length or 0))
    except (TypeError, ValueError):
        total = 0
    parts: list[str] = []
    for offset in range(0, total, 6000):
        raw = window.evaluate_js(
            f"(function(){{return {{t:String(window.__argusInvBody||'').slice({offset},{min(offset + 6000, total)})}};}})()"
        )
        chunk = _eval_object(raw).get("t")
        parts.append(chunk if isinstance(chunk, str) else "")
    try:
        window.evaluate_js("window.__argusInvBody=''")
    except Exception:
        pass
    return "".join(parts)


def _steam_inventory_page(steamid: str, start: str, contextid: int, optional: bool) -> dict[str, Any]:
    query = {"l": "english", "count": str(STEAM_INV_COUNT)}
    if start:
        query["start_assetid"] = start
    url = f"https://steamcommunity.com/inventory/{steamid}/{CS2_APPID}/{int(contextid)}?{urllib.parse.urlencode(query)}"
    referrer = f"https://steamcommunity.com/profiles/{steamid}/inventory"
    window, _created = _steam_window()
    bridge = _steam_bridge()
    bridge.reset()
    _pace_steam_request()
    script = (
        "(function(){"
        "var c=new AbortController();"
        "setTimeout(function(){try{c.abort()}catch(e){}},20000);"
        f"return fetch({json.dumps(url)},{{credentials:'include',referrer:{json.dumps(referrer)},"
        "headers:{Accept:'application/json, text/javascript;q=0.9,*/*;q=0.8'},signal:c.signal})"
        ".then(function(r){return r.text().then(function(t){"
        "window.__argusInvBody=t;"
        "var api=window.pywebview&&pywebview.api;"
        "if(!(api&&api.begin&&api.push&&api.finish)){"
        "return {status:r.status,retryAfter:r.headers.get('Retry-After')||'',length:t.length,bridge:false};}"
        "var i=0,n=6000;"
        "return Promise.resolve(api.begin(r.status,r.headers.get('Retry-After')||'',t.length))"
        ".then(function step(){"
        "if(i>=t.length) return api.finish().then(function(){return {status:r.status,length:t.length,bridge:true};});"
        "var s=t.slice(i,i+n); i+=n;"
        "return api.push({t:s}).then(step);"
        "});"
        "});})"
        ".catch(function(e){"
        "try{if(window.pywebview&&pywebview.api&&pywebview.api.fail) pywebview.api.fail(String(e))}catch(x){}"
        "return {status:0,error:String(e),length:0,bridge:false};"
        "});"
        "})()"
    )
    meta = _eval_object(_steam_eval(window, script, timeout=35))
    retry_after = str(meta.get("retryAfter") or "")
    if bridge.wait(1):
        body, status, bridge_retry, error = bridge.result()
        if error:
            raise ValueError(error)
        retry_after = bridge_retry or retry_after
        status = status or int(meta.get("status") or 0)
    else:
        try:
            status = int(meta.get("status") or 0)
        except (TypeError, ValueError):
            status = 0
        body = _read_window_body(window, meta.get("length"))
    if not body:
        if optional:
            return _empty_page()
        err = str(meta.get("error") or "").strip()
        if "abort" in err.lower():
            raise ValueError("Steam inventory request timed out.")
        raise ValueError(err or "Could not read Steam inventory through the browser window.")
    if status == 429:
        if optional:
            return _empty_page()
        raise ValueError(_inventory_wait_error(_mark_cooldown(_retry_after_seconds(retry_after))))
    if status in {400, 401, 403, 404}:
        if optional:
            return _empty_page()
        if status in {401, 403}:
            raise ValueError("This Steam inventory is private. Set your inventory to public in Steam privacy settings.")
        raise ValueError("Steam could not find that inventory.")
    if status not in {0, 200}:
        if optional:
            return _empty_page()
        raise ValueError(f"Steam inventory request failed (HTTP {status}).")
    try:
        return _parse_inventory_payload(body)
    except ValueError:
        if optional:
            return _empty_page()
        raise


def _more_items(raw: dict[str, Any]) -> bool:
    value = raw.get("more_items")
    if value in {1, True, "1", "true", "True"}:
        return True
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return bool(value)


def _fetch_context(steamid: str, contextid: int, optional: bool) -> tuple[list[dict[str, Any]], int]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    start = ""
    stagnant = 0
    stub_hits = 0
    for _ in range(40):
        raw = _steam_inventory_page(steamid, start, contextid, optional)
        if raw.get("success") in {0, False, "0", "false"}:
            if optional:
                break
            raise ValueError("This Steam inventory is private or unavailable.")
        assets = raw.get("assets") if isinstance(raw.get("assets"), list) else []
        descriptions = raw.get("descriptions") if isinstance(raw.get("descriptions"), list) else []
        try:
            count = int(raw.get("total_inventory_count") or 0)
        except (TypeError, ValueError):
            count = 0
        if count and not assets and not descriptions:
            stub_hits += 1
            if optional or items:
                break
            if stub_hits > 2:
                raise ValueError("Steam sent an item count but no items. Wait a bit, then refresh once.")
            time.sleep(1.2)
            continue
        if count:
            total = count
        if count == 0 and not assets:
            break
        desc_index: dict[tuple[str, str], dict[str, Any]] = {}
        class_index: dict[str, dict[str, Any]] = {}
        for row in descriptions:
            if isinstance(row, dict):
                cid = str(row.get("classid") or "")
                desc_index[(cid, str(row.get("instanceid") or "0"))] = row
                class_index.setdefault(cid, row)
        prop_index = _index_asset_properties(raw)
        added = 0
        last_asset = ""
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            aid = str(asset.get("assetid") or asset.get("id") or "")
            if aid:
                last_asset = aid
            item = _slim_inventory_item(asset, desc_index, class_index, str(contextid), steamid, prop_index.get(aid) or [])
            if not item or item["id"] in seen:
                continue
            seen.add(item["id"])
            items.append(item)
            added += 1
        more = _more_items(raw)
        nxt = str(raw.get("last_assetid") or last_asset or "")
        if added == 0 and not more:
            stagnant += 1
            if stagnant >= 2:
                break
        else:
            stagnant = 0
        if nxt and nxt != start and (more or (total and len(items) < total)):
            start = nxt
            continue
        break
    return items, total or len(items)


def _fetch_inventory(steamid: str, signed_in: bool) -> dict[str, Any]:
    items, total = _fetch_context(steamid, CS2_CONTEXTID, optional=False)
    seen = {row["id"] for row in items}
    try:
        extra, extra_total = _fetch_context(steamid, CS2_PROTECTED_CONTEXTID, optional=True)
    except ValueError:
        extra, extra_total = [], 0
    for item in extra:
        if item["id"] not in seen:
            seen.add(item["id"])
            items.append(item)
    items.sort(key=lambda row: int(re.sub(r"\D", "", row["id"]) or 0), reverse=True)
    return {
        "steam64": steamid,
        "total": (total + extra_total) or len(items),
        "count": len(items),
        "items": items,
        "fetchedAt": int(time.time() * 1000),
        "signedIn": bool(signed_in),
    }


# ----------------------------------------------------------------------------
# CSFloat prices
# ----------------------------------------------------------------------------

def _price_cache() -> dict[str, dict[str, Any]]:
    global _PRICE_CACHE
    if _PRICE_CACHE is None:
        raw = _read_json(PRICES_PATH, {})
        _PRICE_CACHE = {k: v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}
    return _PRICE_CACHE


def _flush_price_cache() -> None:
    with _PRICE_LOCK:
        snapshot = dict(_price_cache())
    try:
        _write_json(PRICES_PATH, snapshot)
    except OSError:
        pass


def _csfloat_category(item: dict[str, Any]) -> str:
    if str(item.get("category") or "") not in CSFLOAT_SKIN_CATEGORIES:
        return ""
    name = str(item.get("name") or "").lower()
    if item.get("souvenir") or "souvenir" in name:
        return "3"
    if item.get("statTrak") or "stattrak" in name.replace("-", ""):
        return "2"
    return "1"


def _parse_price_list(raw: object) -> dict[str, int]:
    rows = raw if isinstance(raw, list) else raw.get("data") or raw.get("items") or [] if isinstance(raw, dict) else []
    out: dict[str, int] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("market_hash_name") or row.get("name") or "").strip()
        price = row.get("min_price") if row.get("min_price") is not None else row.get("price")
        try:
            cents = int(price)
        except (TypeError, ValueError):
            continue
        if name and cents >= 0:
            out[name] = cents
    return out


def _price_list() -> dict[str, int]:
    """Bulk lowest-listing price for every item name. One request, refreshed every 2 hours."""
    global _PRICELIST, _PRICELIST_AT, _PRICELIST_LOADING, _CSFLOAT_AUTH_BAD
    now = time.time()
    if _PRICELIST is not None and now - _PRICELIST_AT < CSFLOAT_PRICE_TTL:
        return _PRICELIST
    if _PRICELIST is None:
        saved = _read_json(PRICELIST_PATH, {})
        if isinstance(saved, dict) and isinstance(saved.get("items"), dict):
            _PRICELIST = {k: int(v) for k, v in saved["items"].items() if isinstance(v, (int, float))}
            _PRICELIST_AT = float(saved.get("at") or 0)
            if now - _PRICELIST_AT < CSFLOAT_PRICE_TTL:
                return _PRICELIST
    key = _secret("csfloat")
    if not key or _PRICELIST_LOADING:
        return _PRICELIST or {}
    _PRICELIST_LOADING = True
    try:
        request = urllib.request.Request(CSFLOAT_PRICE_LIST_URL, method="GET", headers=_csfloat_headers(key))
        with urllib.request.urlopen(request, timeout=45) as response:
            parsed = _parse_price_list(json.loads(response.read().decode("utf-8")))
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            _CSFLOAT_AUTH_BAD = True
        _PRICELIST_AT = now - CSFLOAT_PRICE_TTL + 120
        return _PRICELIST or {}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        _PRICELIST_AT = now - CSFLOAT_PRICE_TTL + 120
        return _PRICELIST or {}
    finally:
        _PRICELIST_LOADING = False
    _PRICELIST = parsed
    _PRICELIST_AT = time.time()
    try:
        _write_json(PRICELIST_PATH, {"at": _PRICELIST_AT, "items": parsed})
    except OSError:
        pass
    return parsed


def _fetch_csfloat_lowest(name: str, category: str) -> dict[str, Any]:
    key = _secret("csfloat")
    if not key:
        raise FileNotFoundError("CSFloat API key is not set")
    params = {"type": "buy_now", "limit": "1", "sort_by": "lowest_price", "market_hash_name": name}
    if category in {"1", "2", "3"}:
        params["category"] = category
    request = urllib.request.Request(
        f"{CSFLOAT_LISTINGS_URL}?{urllib.parse.urlencode(params)}", method="GET", headers=_csfloat_headers(key)
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = json.loads(response.read().decode("utf-8"))
    rows = raw if isinstance(raw, list) else raw.get("data") or raw.get("listings") or [] if isinstance(raw, dict) else []
    now = time.time()
    row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
    if row is None:
        return {"state": "none", "cents": None, "listingId": "", "fetchedAt": now}
    try:
        cents = int(row.get("price"))
    except (TypeError, ValueError):
        return {"state": "error", "cents": None, "listingId": "", "fetchedAt": now}
    return {"state": "ready", "cents": cents, "listingId": str(row.get("id") or ""), "fetchedAt": now}


def _price_worker() -> None:
    global _PRICE_THREAD, _CSFLOAT_AUTH_BAD
    done = 0
    try:
        _price_list()
        while not _SHUTDOWN:
            with _PRICE_LOCK:
                if not _PRICE_QUEUE:
                    _PRICE_THREAD = None
                    return
                name, category = _PRICE_QUEUE[0]
            if name in _price_list():
                with _PRICE_LOCK:
                    _PRICE_QUEUE.pop(0)
                    _PRICE_QUEUED.discard(name)
                continue
            try:
                row = _fetch_csfloat_lowest(name, category)
            except urllib.error.HTTPError as exc:
                if exc.code in {401, 403}:
                    _CSFLOAT_AUTH_BAD = True
                    with _PRICE_LOCK:
                        _PRICE_QUEUE.clear()
                        _PRICE_QUEUED.clear()
                    return
                if exc.code == 429:
                    raw = str(exc.headers.get("Retry-After") or "").strip() if exc.headers else ""
                    time.sleep(min(max(int(raw), 5), 120) if raw.isdigit() else 20.0)
                    continue
                row = {"state": "error", "cents": None, "listingId": "", "fetchedAt": time.time()}
            except Exception:
                row = {"state": "error", "cents": None, "listingId": "", "fetchedAt": time.time()}
            with _PRICE_LOCK:
                _price_cache()[name] = row
                if _PRICE_QUEUE and _PRICE_QUEUE[0][0] == name:
                    _PRICE_QUEUE.pop(0)
                _PRICE_QUEUED.discard(name)
            done += 1
            if done % 10 == 0:
                _flush_price_cache()
            time.sleep(CSFLOAT_GAP_SEC)
    finally:
        if done:
            _flush_price_cache()
        with _PRICE_LOCK:
            if _PRICE_THREAD is threading.current_thread():
                _PRICE_THREAD = None


def _needs_lookup(name: str) -> bool:
    row = _price_cache().get(name)
    if not isinstance(row, dict):
        return True
    try:
        age = time.time() - float(row.get("fetchedAt") or 0)
    except (TypeError, ValueError):
        return True
    return age >= (CSFLOAT_PRICE_TTL if row.get("state") == "ready" else CSFLOAT_MISS_TTL)


def _queue_prices(items: list[dict[str, Any]]) -> None:
    """Price every name from the bulk list; look the rest up one by one in the background."""
    global _PRICE_THREAD
    if _SHUTDOWN or _CSFLOAT_AUTH_BAD or not _secret("csfloat"):
        return
    listed = _PRICELIST or {}
    jobs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name or name == "Unknown" or name in seen:
            continue
        seen.add(name)
        if name not in listed and _needs_lookup(name):
            jobs.append((name, _csfloat_category(item)))
    with _PRICE_LOCK:
        for job in jobs:
            if job[0] not in _PRICE_QUEUED:
                _PRICE_QUEUED.add(job[0])
                _PRICE_QUEUE.append(job)
        if (_PRICE_QUEUE or _PRICELIST is None or time.time() - _PRICELIST_AT >= CSFLOAT_PRICE_TTL) and (
            _PRICE_THREAD is None or not _PRICE_THREAD.is_alive()
        ):
            if not _PRICE_QUEUE and _PRICELIST_LOADING:
                return
            _PRICE_THREAD = threading.Thread(target=_price_worker, name="cs2-prices", daemon=True)
            _PRICE_THREAD.start()


def _price_for(item: dict[str, Any], has_key: bool) -> tuple[int | None, str, str]:
    name = str(item.get("name") or "").strip()
    if not name or name == "Unknown":
        return None, "none", ""
    if not has_key:
        return None, "nokey", ""
    if _CSFLOAT_AUTH_BAD:
        return None, "keybad", ""
    listed = (_PRICELIST or {}).get(name)
    row = _price_cache().get(name)
    if listed is not None:
        return listed, "ready", str(row.get("listingId") or "") if isinstance(row, dict) else ""
    if isinstance(row, dict) and row.get("state") in {"ready", "none", "error"}:
        cents = row.get("cents")
        return (int(cents) if isinstance(cents, (int, float)) else None), str(row["state"]), str(row.get("listingId") or "")
    return None, "pending", ""


def _priced_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    has_key = bool(_secret("csfloat"))
    boxes = _icon_boxes()
    out = []
    for item in items:
        cents, state, listing = _price_for(item, has_key)
        key = _icon_key(item.get("icon"))
        out.append({
            **item, "priceCents": cents, "priceState": state, "priceListingId": listing,
            "iconKey": key, "box": boxes.get(key),
        })
    return out


# ----------------------------------------------------------------------------
# Icon crop boxes
# ----------------------------------------------------------------------------
# Steam item images carry very different amounts of transparent padding (a case
# or music kit fills ~45% of its image, most rifles ~90%). The visible area of
# each image is measured once and cached, so the UI can crop to it and every
# item shows at a similar size.

def _icon_key(url: object) -> str:
    found = re.search(r"/economy/image/([^/?#]+)", str(url or ""))
    return found.group(1) if found else ""


def _icon_boxes() -> dict[str, list[float]]:
    global _ICONBOX
    with _ICONBOX_LOCK:
        if _ICONBOX is None:
            raw = _read_json(ICONBOX_PATH, {})
            _ICONBOX = {k: v for k, v in raw.items() if isinstance(v, list) and len(v) == 4} if isinstance(raw, dict) else {}
        return _ICONBOX


def _measure_icon(key: str, original: bool = False) -> list[float] | None:
    """Visible area of an image as fractions. Measured on the exact URL the UI shows:
    the padded 256px card icon, or the unpadded original used in the item dialog."""
    import cv2
    import numpy as np

    url = f"{STEAM_ICON_CDN}/{key}" if original else f"{STEAM_ICON_CDN}/{key}/256fx256f"
    with urllib.request.urlopen(url, timeout=15) as response:
        raw = response.read()
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    if img.ndim != 3 or img.shape[2] != 4:
        return [0.0, 0.0, 1.0, 1.0]
    alpha = img[:, :, 3]
    ys, xs = np.nonzero(alpha > 16)
    if not len(xs):
        return [0.0, 0.0, 1.0, 1.0]
    h, w = alpha.shape
    return [round(float(xs.min()) / w, 4), round(float(ys.min()) / h, 4), round(float(xs.max() + 1) / w, 4), round(float(ys.max() + 1) / h, 4)]


def _icon_worker() -> None:
    global _ICONBOX_THREAD
    from concurrent.futures import ThreadPoolExecutor

    try:
        while not _SHUTDOWN:
            with _ICONBOX_LOCK:
                batch = list(_ICONBOX_PENDING)[:24]
            if not batch:
                return
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda k: (k, _safe_measure(k)), batch))
            boxes = _icon_boxes()
            with _ICONBOX_LOCK:
                for key, box in results:
                    _ICONBOX_PENDING.discard(key)
                    if box is None:
                        _ICONBOX_FAILED.add(key)
                    else:
                        boxes[key] = box
                snapshot = dict(boxes)
            try:
                _write_json(ICONBOX_PATH, snapshot)
            except OSError:
                pass
    finally:
        with _ICONBOX_LOCK:
            if _ICONBOX_THREAD is threading.current_thread():
                _ICONBOX_THREAD = None


def _safe_measure(key: str) -> list[float] | None:
    try:
        return _measure_icon(key)
    except Exception:
        return None


def _queue_icon_boxes(items: list[dict[str, Any]]) -> None:
    global _ICONBOX_THREAD
    boxes = _icon_boxes()
    with _ICONBOX_LOCK:
        for item in items:
            key = _icon_key(item.get("icon"))
            if key and key not in boxes and key not in _ICONBOX_FAILED:
                _ICONBOX_PENDING.add(key)
        if _ICONBOX_PENDING and (_ICONBOX_THREAD is None or not _ICONBOX_THREAD.is_alive()):
            _ICONBOX_THREAD = threading.Thread(target=_icon_worker, name="cs2-icon-boxes", daemon=True)
            _ICONBOX_THREAD.start()


def _price_table(items: list[dict[str, Any]]) -> dict[str, Any]:
    has_key = bool(_secret("csfloat"))
    table: dict[str, dict[str, Any]] = {}
    pending = 0
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name or name in table:
            continue
        cents, state, listing = _price_for(item, has_key)
        table[name] = {"cents": cents, "state": state, "listingId": listing}
        if state == "pending":
            pending += 1
    return {"prices": table, "pending": pending}


# ----------------------------------------------------------------------------
# Public API (called from ui_bridge.SettingsApi)
# ----------------------------------------------------------------------------

def state() -> dict[str, Any]:
    saved = _load_state()
    steam64 = _as_steam64(saved.get("steam64"))
    has_key = bool(_secret("csfloat"))
    return {
        "ok": True,
        "signedIn": bool(steam64),
        "steam64": steam64,
        "persona": str(saved.get("persona") or ""),
        "avatar": str(saved.get("avatar") or ""),
        "pricing": has_key,
        "keyBad": has_key and _CSFLOAT_AUTH_BAD,
        "cooldown": _cooldown_left(),
        "hasCache": _load_items() is not None,
    }


def sign_in() -> dict[str, Any]:
    global _steam_login_busy
    with _INVENTORY_FETCH_LOCK:
        if _inventory_fetching:
            return {"ok": False, "error": "Wait for the inventory to finish loading, then sign in."}
    with _STEAM_LOGIN_LOCK:
        if _steam_login_busy:
            with _STEAM_WIN_LOCK:
                popup = _LOGIN_WIN
            _focus_webview(popup)
            return {"ok": False, "error": "The Steam sign-in window is already open.", "busy": True}
        _steam_login_busy = True
    try:
        window, closed = _open_login_window()
        window.events.loaded.wait(20)
        _focus_webview(window)
        deadline = time.time() + STEAM_LOGIN_TIMEOUT_SEC
        sid = ""
        while time.time() < deadline and not closed.is_set():
            sid = _steam_session_steamid(window)
            if sid or closed.wait(1.2):
                break
        if closed.is_set() and not sid:
            _close_login_window()
            return {"ok": False, "error": "Steam sign-in was closed.", "cancelled": True}
        _close_login_window()
        if not sid:
            return {"ok": False, "error": "Steam sign-in timed out."}
        _reset_steam_window()
        _remember_steam64(sid)
        _fetch_steam_persona(sid)
        return state()
    except ValueError as exc:
        _close_login_window()
        return {"ok": False, "error": str(exc) or "Could not open Steam sign-in."}
    finally:
        with _STEAM_LOGIN_LOCK:
            _steam_login_busy = False


def cancel_sign_in() -> dict[str, Any]:
    _close_login_window()
    return {"ok": True}


def sign_out() -> dict[str, Any]:
    try:
        window, created = _steam_window()
        if created:
            window.events.loaded.wait(20)
        window.clear_cookies()
    except Exception:
        pass
    _reset_steam_window()
    _update_state(steam64="", persona="", avatar="", cooldownUntil=0)
    _save_items(None)
    return state()


def inventory(force: bool = False) -> dict[str, Any]:
    global _inventory_fetching
    cached = _load_items()
    with _STEAM_LOGIN_LOCK:
        login_busy = _steam_login_busy

    def reply(payload: dict[str, Any] | None, **extra: Any) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": True, "cooldown": _cooldown_left(), **extra}
        if payload is not None:
            items = payload.get("items") or []
            _queue_prices(items)
            _queue_icon_boxes(items)
            out["data"] = {**payload, "items": _priced_items(items)}
        else:
            out["data"] = None
        return out

    if login_busy:
        return reply(cached, cached=True, notice="Finish signing in to Steam first.")
    stale = cached is not None and time.time() - float(cached.get("fetchedAt") or 0) / 1000.0 >= INVENTORY_STALE_SEC
    if not force and cached is not None and (not stale or _cooldown_left() > 0):
        return reply(cached, cached=True, stale=stale)
    remaining = _cooldown_left()
    if remaining > 0:
        if cached is not None:
            return reply(cached, cached=True, notice=_inventory_wait_error(remaining))
        return {"ok": False, "error": _inventory_wait_error(remaining), "cooldown": remaining}
    with _INVENTORY_FETCH_LOCK:
        if _inventory_fetching:
            return reply(cached, cached=True, notice="Inventory is already loading.")
        _inventory_fetching = True
    try:
        try:
            _window, created = _steam_window()
            if created:
                time.sleep(1.2)
        except ValueError:
            pass
        session = _steam_session_steamid()
        steamid = _remember_steam64(session) if session else _saved_steam64()
        if not steamid:
            return {"ok": False, "error": "Sign in to Steam first.", "needsSignIn": True}
        try:
            result = _fetch_inventory(steamid, signed_in=bool(session))
        except (ValueError, OSError) as exc:
            if cached is not None:
                return reply(cached, cached=True, notice=str(exc) or "Could not refresh inventory")
            return {"ok": False, "error": str(exc) or "Could not load CS2 inventory", "cooldown": _cooldown_left()}
        _update_state(cooldownUntil=0)
        _save_items(result)
        threading.Thread(target=_fetch_steam_persona, args=(steamid,), daemon=True).start()
        return reply(result, cached=False)
    finally:
        with _INVENTORY_FETCH_LOCK:
            _inventory_fetching = False


def prices() -> dict[str, Any]:
    cached = _load_items()
    items = cached.get("items") if cached else []
    with _PRICE_LOCK:
        queued = len(_PRICE_QUEUE)
        table = _price_table(items or [])
    loading = _PRICELIST_LOADING or (bool(_secret("csfloat")) and _PRICELIST is None and not _CSFLOAT_AUTH_BAD)
    return {
        "ok": True,
        **table,
        "queued": queued,
        "loading": loading,
        "hasKey": bool(_secret("csfloat")),
        "keyBad": _CSFLOAT_AUTH_BAD,
    }


def keys() -> dict[str, Any]:
    return {"ok": True, "keys": _key_rows()}


def icon_box_original(key: str) -> dict[str, Any]:
    """Crop box for the full-size image in the item dialog; measured on first open, then cached."""
    key = str(key or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{8,512}", key):
        return {"ok": False, "error": "Bad icon"}
    cache_key = f"{key}@o"
    boxes = _icon_boxes()
    with _ICONBOX_LOCK:
        if cache_key in boxes:
            return {"ok": True, "box": boxes[cache_key]}
    box = _safe_measure_original(key)
    if box is None:
        return {"ok": False, "error": "Could not measure image"}
    with _ICONBOX_LOCK:
        boxes[cache_key] = box
        snapshot = dict(boxes)
    try:
        _write_json(ICONBOX_PATH, snapshot)
    except OSError:
        pass
    return {"ok": True, "box": box}


def _safe_measure_original(key: str) -> list[float] | None:
    try:
        return _measure_icon(key, original=True)
    except Exception:
        return None


def icon_boxes() -> dict[str, Any]:
    cached = _load_items()
    wanted = {_icon_key(item.get("icon")) for item in (cached.get("items") if cached else [])}
    boxes = _icon_boxes()
    with _ICONBOX_LOCK:
        found = {key: boxes[key] for key in wanted if key in boxes}
        pending = len(_ICONBOX_PENDING)
    return {"ok": True, "boxes": found, "pending": pending}


def save_key(name: str, value: str) -> dict[str, Any]:
    global _CSFLOAT_AUTH_BAD, _PRICELIST_AT
    name = str(name or "").strip()
    text = str(value or "").strip()
    if name not in API_KEYS:
        return {"ok": False, "error": "Unknown key"}
    if not text:
        return {"ok": False, "error": "Paste a key first"}
    if len(text) > MAX_SECRET_LEN:
        return {"ok": False, "error": "That key is too long"}
    warning = ""
    if name == "csfloat":
        code = _validate_csfloat_key(text)
        if code in {401, 403}:
            return {"ok": False, "error": "CSFloat rejected this key. Copy it again from the Developer tab."}
        if code not in {200, 429}:
            warning = "Could not reach CSFloat to check the key. It was saved anyway."
        _CSFLOAT_AUTH_BAD = False
        _PRICELIST_AT = 0.0
    elif name == "leetify":
        import leetify

        text = leetify.clean_key(text)
        if not text:
            return {"ok": False, "error": "Paste a key first"}
        confirmed, codes = leetify.validate_key(text)
        _update_state(leetifyKeyVerified=confirmed)
        if not confirmed:
            # A wrong key never breaks stats requests (Leetify just applies the
            # keyless rate limits), so save it rather than refuse a key that may be fine.
            try:
                from logger import log

                log("Argus", "Leetify key check did not confirm the key (" + ", ".join(f"{k} header: HTTP {v or 'no response'}" for k, v in codes.items()) + "). Saved it anyway.")
            except Exception:
                pass
            warning = (
                "Saved. Leetify's key check didn't confirm it, but stats work either way. "
                "If you get rate-limited, copy the key again from leetify.com/app/developer."
            )
    secrets = _load_secrets()
    secrets[name] = text
    try:
        _save_secrets(secrets)
    except OSError:
        return {"ok": False, "error": "Could not encrypt the key"}
    if name == "csfloat":
        cached = _load_items()
        if cached:
            _queue_prices(cached.get("items") or [])
    return {"ok": True, "keys": _key_rows(), "warning": warning}


def reveal_key(name: str) -> dict[str, Any]:
    value = _secret(str(name or ""))
    if not value:
        return {"ok": False, "error": "No key saved"}
    return {"ok": True, "value": value}


def delete_key(name: str) -> dict[str, Any]:
    global _CSFLOAT_AUTH_BAD
    name = str(name or "").strip()
    if name not in API_KEYS:
        return {"ok": False, "error": "Unknown key"}
    secrets = _load_secrets()
    secrets.pop(name, None)
    try:
        _save_secrets(secrets)
    except OSError:
        return {"ok": False, "error": "Could not update saved keys"}
    if name == "csfloat":
        _CSFLOAT_AUTH_BAD = False
        with _PRICE_LOCK:
            _PRICE_QUEUE.clear()
            _PRICE_QUEUED.clear()
    return {"ok": True, "keys": _key_rows()}


def open_url(url: str) -> dict[str, Any]:
    href = str(url or "").strip()
    scheme = urllib.parse.urlparse(href).scheme.lower()
    if scheme not in {"https", "steam"}:
        return {"ok": False, "error": "Unsupported link"}
    try:
        if scheme == "steam" and sys.platform == "win32":
            os.startfile(href)  # type: ignore[attr-defined]
        else:
            webbrowser.open(href)
    except OSError as exc:
        return {"ok": False, "error": str(exc) or "Could not open link"}
    return {"ok": True}


def shutdown() -> None:
    global _SHUTDOWN
    _SHUTDOWN = True
    _close_login_window()
    _reset_steam_window()
