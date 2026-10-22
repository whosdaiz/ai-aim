"""CS2 stats from the Leetify Public CS API.

Uses the Steam account signed in through the CS2 inventory module. Per Leetify's
developer guidelines (https://leetify.com/blog/leetify-api-developer-guidelines/)
nothing returned by the API is written to disk: responses are kept in memory for
a short time only, so opening the tab again shows current data.

An API key is optional (Settings > Manage API keys). Without one, Leetify applies
stricter rate limits.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import cs2_inventory

API_BASE = "https://api-public.cs-prod.leetify.com"
PROFILE_TTL_S = 60.0
MATCH_TTL_S = 300.0
_UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, Any]] = {}


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "ArgusAI/1.0"}
    key = cs2_inventory._secret("leetify")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _retry_after(exc: urllib.error.HTTPError) -> int:
    raw = str(exc.headers.get("Retry-After") or "").strip() if exc.headers else ""
    return int(raw) if raw.isdigit() else 60


def _get(path: str, params: dict[str, str] | None = None) -> tuple[int, Any, int]:
    """Returns (status, parsed JSON or None, retry-after seconds)."""
    url = f"{API_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, method="GET", headers=_headers())
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return int(response.status or 200), json.loads(response.read().decode("utf-8")), 0
    except urllib.error.HTTPError as exc:
        return int(exc.code or 0), None, _retry_after(exc) if exc.code == 429 else 0
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return 0, None, 0


def _cached(key: str, ttl: float) -> Any:
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        if hit:
            _CACHE.pop(key, None)
    return None


def _remember(key: str, value: Any) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), value)


def _error(status: int, retry: int, what: str) -> dict[str, Any]:
    if status == 404:
        return {
            "ok": False,
            "notFound": True,
            "error": f"Leetify has no {what} for this Steam account yet. Sign in once at leetify.com so it can start tracking your matches.",
        }
    if status == 429:
        return {"ok": False, "rateLimited": True, "error": f"Leetify is rate-limiting requests. Try again in about {max(1, retry)} s, or add a Leetify API key in Settings."}
    if status == 0:
        return {"ok": False, "error": "Could not reach Leetify."}
    return {"ok": False, "error": f"Leetify returned an error (HTTP {status})."}


# ----------------------------------------------------------------------------
# Public API (called from ui_bridge.SettingsApi)
# ----------------------------------------------------------------------------

def state() -> dict[str, Any]:
    saved = cs2_inventory._load_state()
    steam64 = cs2_inventory._saved_steam64()
    return {
        "ok": True,
        "signedIn": bool(steam64),
        "steam64": steam64,
        "persona": str(saved.get("persona") or ""),
        "avatar": str(saved.get("avatar") or ""),
        "hasKey": bool(cs2_inventory._secret("leetify")),
    }


def profile(force: bool = False) -> dict[str, Any]:
    steam64 = cs2_inventory._saved_steam64()
    if not steam64:
        return {"ok": False, "needsSignIn": True, "error": "Sign in to Steam first."}
    key = f"profile:{steam64}"
    if not force:
        hit = _cached(key, PROFILE_TTL_S)
        if hit is not None:
            return {"ok": True, "profile": hit, "steam64": steam64}
    status, data, retry = _get("/v3/profile", {"steam64_id": steam64})
    if status != 200 or not isinstance(data, dict):
        return _error(status, retry, "profile")
    _remember(key, data)
    return {"ok": True, "profile": data, "steam64": steam64}


def match(game_id: str) -> dict[str, Any]:
    game_id = str(game_id or "").strip()
    if not _UUID_RE.match(game_id):
        return {"ok": False, "error": "Unknown match"}
    key = f"match:{game_id}"
    hit = _cached(key, MATCH_TTL_S)
    if hit is not None:
        return {"ok": True, "match": hit}
    status, data, retry = _get(f"/v2/matches/{game_id}")
    if status != 200 or not isinstance(data, dict):
        return _error(status, retry, "details for this match")
    _remember(key, data)
    return {"ok": True, "match": data}


def clean_key(raw: str) -> str:
    """Pasted keys often carry stray whitespace, quotes, or a "Bearer " prefix."""
    text = re.sub(r"(?i)^\s*bearer\s+", "", str(raw or ""))
    return "".join(text.split()).strip("\"'")


def _check(headers: dict[str, str]) -> int:
    request = urllib.request.Request(
        f"{API_BASE}/api-key/validate",
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "ArgusAI/1.0", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return int(response.status or 200)
    except urllib.error.HTTPError as exc:
        return int(exc.code or 0)
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0


def validate_key(key: str) -> tuple[bool, dict[str, int]]:
    """Leetify documents two ways to send the key; accept it if either confirms it.

    Returns (confirmed, status per header style) so a failed check can be logged
    without ever logging the key itself.
    """
    key = clean_key(key)
    results = {"Authorization": _check({"Authorization": f"Bearer {key}"})}
    if results["Authorization"] != 200:
        results["_leetify_key"] = _check({"_leetify_key": key})
    return any(code == 200 for code in results.values()), results


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
