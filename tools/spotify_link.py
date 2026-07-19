import base64
import json
import os
import random
import time
from threading import Lock
from typing import Any, Dict, Optional

import requests


_TOKEN_CACHE: Dict[str, Any] = {
    "access_token": "",
    "expires_at": 0.0,
}

_ENV_LOADED = False
_ENV_LOCK = Lock()


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _load_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_env_file(path: str) -> None:
    if not os.path.isfile(path):
        return

    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return

    for line in lines:
        raw = str(line or "").strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue

        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            continue

        value = value.strip()
        if len(value) >= 2 and ((value[0] == '"' and value[-1] == '"') or (value[0] == "'" and value[-1] == "'")):
            value = value[1:-1]

        current = os.getenv(key)
        if current is None or str(current).strip() == "":
            os.environ[key] = value


def _ensure_env_loaded() -> None:
    global _ENV_LOADED

    if _ENV_LOADED:
        return

    with _ENV_LOCK:
        if _ENV_LOADED:
            return

        root = _project_root()
        _load_env_file(os.path.join(root, ".env"))
        _load_env_file(os.path.join(root, ".env.local"))
        _ENV_LOADED = True


def _spotify_cfg() -> Dict[str, Any]:
    cfg_path = os.path.join(_project_root(), "config", "mk1_config.json")
    raw = _load_json(cfg_path)
    out = raw.get("spotify") if isinstance(raw.get("spotify"), dict) else {}
    return out


def _cred(name: str) -> str:
    _ensure_env_loaded()
    return (os.getenv(name, "") or "").strip()


def _default_public() -> bool:
    env_val = _cred("SPOTIFY_DEFAULT_PUBLIC")
    if env_val:
        return _truthy(env_val, default=False)

    cfg = _spotify_cfg()
    cfg_val = cfg.get("default_public")
    if cfg_val is None:
        return False
    return _truthy(cfg_val, default=False)


def _default_device_id() -> str:
    return _cred("SPOTIFY_DEVICE_ID")


def _self_heal_transfer_enabled() -> bool:
    env_val = _cred("SPOTIFY_SELF_HEAL_TRANSFER")
    if env_val:
        return _truthy(env_val, default=True)
    return True


def _extract_reason_code(result: Dict[str, Any]) -> str:
    payload = result.get("result") if isinstance(result, dict) else {}
    if not isinstance(payload, dict):
        return ""
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    err_obj = body.get("error") if isinstance(body.get("error"), dict) else {}
    return str(err_obj.get("reason") or "").strip().upper()


def _with_hint(result: Dict[str, Any], hint: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result

    out = dict(result)
    payload = out.get("result") if isinstance(out.get("result"), dict) else {}
    payload = dict(payload)
    payload["self_heal_hint"] = str(hint or "").strip()
    if isinstance(extra, dict) and extra:
        payload["self_heal"] = extra
    out["result"] = payload
    return out


def _list_devices() -> Dict[str, Any]:
    r = _api_request("GET", "/v1/me/player/devices")
    if not r.get("ok"):
        return r
    devices = (r.get("result") or {}).get("devices") if isinstance(r.get("result"), dict) else []
    return {
        "ok": True,
        "result": {
            "devices": devices if isinstance(devices, list) else [],
        },
        "error": None,
    }


def _choose_recovery_device(preferred_device_id: str = "") -> Dict[str, Any]:
    listed = _list_devices()
    if not listed.get("ok"):
        return listed

    devices = (listed.get("result") or {}).get("devices") or []
    if not devices:
        return {
            "ok": False,
            "result": {"devices": []},
            "error": "spotify_no_available_devices",
        }

    preferred = str(preferred_device_id or "").strip()
    if preferred:
        for d in devices:
            if isinstance(d, dict) and str(d.get("id") or "").strip() == preferred:
                return {"ok": True, "result": {"device": d}, "error": None}

    for d in devices:
        if isinstance(d, dict) and bool(d.get("is_active")):
            return {"ok": True, "result": {"device": d}, "error": None}

    for d in devices:
        if not isinstance(d, dict):
            continue
        if not bool(d.get("is_restricted")):
            return {"ok": True, "result": {"device": d}, "error": None}

    return {
        "ok": False,
        "result": {"devices": devices},
        "error": "spotify_no_usable_device",
    }


def _transfer_playback(device_id: str, play: bool = False) -> Dict[str, Any]:
    did = str(device_id or "").strip()
    if not did:
        return {
            "ok": False,
            "result": None,
            "error": "device_id_required",
        }

    return _api_request(
        "PUT",
        "/v1/me/player",
        payload={
            "device_ids": [did],
            "play": bool(play),
        },
    )


def _auth_header(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _refresh_access_token() -> Dict[str, Any]:
    client_id = _cred("SPOTIFY_CLIENT_ID")
    client_secret = _cred("SPOTIFY_CLIENT_SECRET")
    refresh_token = _cred("SPOTIFY_REFRESH_TOKEN")

    if not client_id or not client_secret or not refresh_token:
        return {
            "ok": False,
            "result": None,
            "error": "spotify_credentials_missing",
        }

    try:
        resp = requests.post(
            "https://accounts.spotify.com/api/token",
            headers={
                "Authorization": f"Basic {_auth_header(client_id, client_secret)}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=20,
        )
    except Exception as e:
        return {
            "ok": False,
            "result": None,
            "error": f"spotify_auth_request_failed: {type(e).__name__}: {e}",
        }

    body: Dict[str, Any]
    try:
        body = resp.json() if resp.content else {}
    except Exception:
        body = {}

    if resp.status_code >= 400:
        err = str(body.get("error") or "") if isinstance(body, dict) else ""
        detail_error = "spotify_auth_failed"
        if err == "invalid_grant":
            detail_error = "spotify_refresh_token_invalid_or_expired"
        return {
            "ok": False,
            "result": {
                "status_code": resp.status_code,
                "body": body,
            },
            "error": detail_error,
        }

    token = str(body.get("access_token") or "").strip()
    expires_in = int(body.get("expires_in") or 3600)
    if not token:
        return {
            "ok": False,
            "result": body,
            "error": "spotify_auth_missing_access_token",
        }

    _TOKEN_CACHE["access_token"] = token
    _TOKEN_CACHE["expires_at"] = time.time() + max(60, expires_in - 45)
    return {
        "ok": True,
        "result": {
            "expires_in": expires_in,
        },
        "error": None,
    }


def _get_access_token() -> Dict[str, Any]:
    token = str(_TOKEN_CACHE.get("access_token") or "")
    expires_at = float(_TOKEN_CACHE.get("expires_at") or 0.0)
    if token and time.time() < expires_at:
        return {
            "ok": True,
            "result": {
                "access_token": token,
            },
            "error": None,
        }

    refreshed = _refresh_access_token()
    if not refreshed.get("ok"):
        return refreshed

    token = str(_TOKEN_CACHE.get("access_token") or "")
    if not token:
        return {
            "ok": False,
            "result": None,
            "error": "spotify_auth_token_cache_empty",
        }

    return {
        "ok": True,
        "result": {
            "access_token": token,
        },
        "error": None,
    }


def _api_request(method: str, path: str, params: Optional[Dict[str, Any]] = None, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    token_result = _get_access_token()
    if not token_result.get("ok"):
        return token_result

    token = str((token_result.get("result") or {}).get("access_token") or "")
    if not token:
        return {
            "ok": False,
            "result": None,
            "error": "spotify_auth_missing_token",
        }

    url = f"https://api.spotify.com{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=payload,
            timeout=25,
        )
    except Exception as e:
        return {
            "ok": False,
            "result": None,
            "error": f"spotify_request_failed: {type(e).__name__}: {e}",
        }

    try:
        body = resp.json() if resp.content else {}
    except Exception:
        body = {
            "raw": (resp.text or "")[:1000],
        }

    if resp.status_code >= 400:
        detail = "spotify_api_error"
        if resp.status_code == 401:
            detail = "spotify_unauthorized"
        elif resp.status_code == 403:
            detail = "spotify_forbidden_scope_or_premium_required"
        elif resp.status_code == 404:
            detail = "spotify_not_found_or_no_active_device"
        return {
            "ok": False,
            "result": {
                "status_code": resp.status_code,
                "body": body,
                "path": path,
            },
            "error": detail,
        }

    return {
        "ok": True,
        "result": body,
        "error": None,
    }


def _search_track(query: str, limit: int = 1) -> Dict[str, Any]:
    q = str(query or "").strip()
    if not q:
        return {
            "ok": False,
            "result": None,
            "error": "track_query_required",
        }

    safe_limit = max(1, min(10, int(limit)))
    r = _api_request(
        "GET",
        "/v1/search",
        params={
            "q": q,
            "type": "track",
            "limit": safe_limit,
        },
    )
    if not r.get("ok"):
        return r

    items = (((r.get("result") or {}).get("tracks") or {}).get("items") or [])
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        artists = item.get("artists") if isinstance(item.get("artists"), list) else []
        artist_names = [str(a.get("name") or "").strip() for a in artists if isinstance(a, dict)]
        out.append(
            {
                "name": item.get("name"),
                "uri": item.get("uri"),
                "id": item.get("id"),
                "artists": [x for x in artist_names if x],
                "album": ((item.get("album") or {}).get("name") if isinstance(item.get("album"), dict) else None),
            }
        )

    return {
        "ok": True,
        "result": {
            "query": q,
            "tracks": out,
            "count": len(out),
        },
        "error": None,
    }


def _current_user() -> Dict[str, Any]:
    r = _api_request("GET", "/v1/me")
    if not r.get("ok"):
        return r
    body = r.get("result") if isinstance(r.get("result"), dict) else {}
    return {
        "ok": True,
        "result": {
            "id": body.get("id"),
            "display_name": body.get("display_name"),
            "product": body.get("product"),
            "country": body.get("country"),
        },
        "error": None,
    }


def _create_playlist(name: str, description: str = "", public: Optional[bool] = None) -> Dict[str, Any]:
    playlist_name = str(name or "").strip()
    if not playlist_name:
        return {
            "ok": False,
            "result": None,
            "error": "playlist_name_required",
        }

    me = _current_user()
    if not me.get("ok"):
        return me

    user_id = str((me.get("result") or {}).get("id") or "").strip()
    if not user_id:
        return {
            "ok": False,
            "result": None,
            "error": "spotify_user_id_missing",
        }

    is_public = _default_public() if public is None else bool(public)
    desc = str(description or "").strip()

    r = _api_request(
        "POST",
        f"/v1/users/{user_id}/playlists",
        payload={
            "name": playlist_name,
            "description": desc,
            "public": is_public,
        },
    )
    if not r.get("ok"):
        return r

    body = r.get("result") if isinstance(r.get("result"), dict) else {}
    return {
        "ok": True,
        "result": {
            "id": body.get("id"),
            "name": body.get("name"),
            "uri": body.get("uri"),
            "external_url": ((body.get("external_urls") or {}).get("spotify") if isinstance(body.get("external_urls"), dict) else None),
            "public": body.get("public"),
        },
        "error": None,
    }


def _add_tracks_to_playlist(playlist_id: str, uris: list[str]) -> Dict[str, Any]:
    pid = str(playlist_id or "").strip()
    clean_uris = [str(u).strip() for u in uris if str(u).strip()]
    if not pid:
        return {
            "ok": False,
            "result": None,
            "error": "playlist_id_required",
        }
    if not clean_uris:
        return {
            "ok": False,
            "result": None,
            "error": "track_uris_required",
        }

    r = _api_request(
        "POST",
        f"/v1/playlists/{pid}/tracks",
        payload={"uris": clean_uris},
    )
    if not r.get("ok"):
        return r

    body = r.get("result") if isinstance(r.get("result"), dict) else {}
    return {
        "ok": True,
        "result": {
            "playlist_id": pid,
            "added_count": len(clean_uris),
            "snapshot_id": body.get("snapshot_id"),
        },
        "error": None,
    }


def _play(payload: Dict[str, Any], device_id: str = "") -> Dict[str, Any]:
    params = {"device_id": device_id} if device_id else None
    first = _api_request("PUT", "/v1/me/player/play", params=params, payload=payload)
    if first.get("ok"):
        return first

    if not _self_heal_transfer_enabled():
        return first

    if first.get("error") not in {"spotify_not_found_or_no_active_device", "spotify_forbidden_scope_or_premium_required"}:
        return first

    picked = _choose_recovery_device(preferred_device_id=device_id or _default_device_id())
    if not picked.get("ok"):
        return _with_hint(
            first,
            "Open Spotify on desktop/phone and start playback once, then retry.",
            {"attempted": "pick_device", "pick_error": picked.get("error")},
        )

    dev = (picked.get("result") or {}).get("device") if isinstance(picked.get("result"), dict) else {}
    target_id = str((dev or {}).get("id") or "").strip() if isinstance(dev, dict) else ""
    if not target_id:
        return _with_hint(first, "No Spotify device id available for recovery.")

    moved = _transfer_playback(target_id, play=False)
    if not moved.get("ok"):
        return _with_hint(
            first,
            "Could not transfer playback to a usable device. Keep Spotify app open and retry.",
            {"attempted": "transfer", "target_device_id": target_id, "transfer_error": moved.get("error")},
        )

    retry = _api_request("PUT", "/v1/me/player/play", params={"device_id": target_id}, payload=payload)
    if retry.get("ok"):
        out = dict(retry)
        payload_out = out.get("result") if isinstance(out.get("result"), dict) else {}
        payload_out = dict(payload_out)
        payload_out["self_heal"] = {
            "attempted": "transfer_then_retry",
            "target_device_id": target_id,
        }
        out["result"] = payload_out
        return out

    return _with_hint(
        retry,
        "Playback retry failed after device transfer. Verify Premium playback controls and active device.",
        {"attempted": "transfer_then_retry", "target_device_id": target_id},
    )


def _play_track(track_uri: str, device_id: str = "") -> Dict[str, Any]:
    uri = str(track_uri or "").strip()
    if not uri:
        return {
            "ok": False,
            "result": None,
            "error": "track_uri_required",
        }
    return _play({"uris": [uri]}, device_id=device_id)


def _play_searched_track(track_row: Dict[str, Any], device_id: str = "", requested_query: str = "") -> Dict[str, Any]:
    if not isinstance(track_row, dict):
        return {
            "ok": False,
            "result": None,
            "error": "track_not_found",
        }

    uri = str(track_row.get("uri") or "").strip()
    if not uri:
        return {
            "ok": False,
            "result": None,
            "error": "track_uri_required",
        }

    # Avoid no-op loop responses when the requested song is already playing.
    now = _current_playback()
    if now.get("ok"):
        current = (now.get("result") or {}) if isinstance(now.get("result"), dict) else {}
        is_playing = bool(current.get("is_playing"))
        current_track = current.get("track") if isinstance(current.get("track"), dict) else {}
        current_uri = str((current_track or {}).get("uri") or "").strip()
        if is_playing and current_uri and current_uri == uri:
            return {
                "ok": True,
                "result": {
                    "status": "already_playing",
                    "requested_query": str(requested_query or "").strip(),
                    "played": {
                        "name": track_row.get("name"),
                        "uri": uri,
                        "artists": track_row.get("artists"),
                        "album": track_row.get("album"),
                    },
                },
                "error": None,
            }

    played = _play_track(track_uri=uri, device_id=device_id)
    if not played.get("ok"):
        return played

    payload_out = (played.get("result") or {}) if isinstance(played.get("result"), dict) else {}
    out = dict(played)
    out["result"] = {
        **payload_out,
        "status": "play_started",
        "requested_query": str(requested_query or "").strip(),
        "played": {
            "name": track_row.get("name"),
            "uri": uri,
            "artists": track_row.get("artists"),
            "album": track_row.get("album"),
        },
    }
    return out


def _play_playlist(playlist_uri: str, device_id: str = "") -> Dict[str, Any]:
    uri = str(playlist_uri or "").strip()
    if not uri:
        return {
            "ok": False,
            "result": None,
            "error": "playlist_uri_required",
        }
    return _play({"context_uri": uri}, device_id=device_id)


def _pause(device_id: str = "") -> Dict[str, Any]:
    params = {"device_id": device_id} if device_id else None
    first = _api_request("PUT", "/v1/me/player/pause", params=params, payload=None)
    if first.get("ok"):
        return first

    if not _self_heal_transfer_enabled():
        return first

    # If nothing is playing, treat pause as successful no-op.
    now = _current_playback()
    if now.get("ok"):
        is_playing = bool(((now.get("result") or {}).get("is_playing")) if isinstance(now.get("result"), dict) else False)
        if not is_playing:
            return {
                "ok": True,
                "result": {
                    "noop": True,
                    "already_paused": True,
                },
                "error": None,
            }

    if first.get("error") not in {"spotify_not_found_or_no_active_device", "spotify_forbidden_scope_or_premium_required"}:
        return first

    picked = _choose_recovery_device(preferred_device_id=device_id or _default_device_id())
    if not picked.get("ok"):
        return _with_hint(first, "Open Spotify and ensure a controllable device is available for pause/stop.")

    dev = (picked.get("result") or {}).get("device") if isinstance(picked.get("result"), dict) else {}
    target_id = str((dev or {}).get("id") or "").strip() if isinstance(dev, dict) else ""
    if not target_id:
        return _with_hint(first, "No device id available for pause recovery.")

    moved = _transfer_playback(target_id, play=False)
    if not moved.get("ok"):
        return _with_hint(first, "Could not transfer control to target device for pause.")

    retry = _api_request("PUT", "/v1/me/player/pause", params={"device_id": target_id}, payload=None)
    if retry.get("ok"):
        out = dict(retry)
        payload_out = out.get("result") if isinstance(out.get("result"), dict) else {}
        payload_out = dict(payload_out)
        payload_out["self_heal"] = {
            "attempted": "transfer_then_retry",
            "target_device_id": target_id,
        }
        out["result"] = payload_out
        return out

    return _with_hint(retry, "Pause retry failed after device transfer. The player may be restricted by Spotify.")


def _set_shuffle(state: bool, device_id: str = "") -> Dict[str, Any]:
    params: Dict[str, Any] = {"state": "true" if state else "false"}
    if device_id:
        params["device_id"] = device_id
    return _api_request("PUT", "/v1/me/player/shuffle", params=params, payload=None)


def _list_playlists(limit: int = 20) -> Dict[str, Any]:
    safe_limit = max(1, min(50, int(limit)))
    r = _api_request("GET", "/v1/me/playlists", params={"limit": safe_limit})
    if not r.get("ok"):
        return r

    items = (r.get("result") or {}).get("items") if isinstance(r.get("result"), dict) else []
    out = []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            out.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "uri": item.get("uri"),
                    "tracks_total": ((item.get("tracks") or {}).get("total") if isinstance(item.get("tracks"), dict) else None),
                    "external_url": ((item.get("external_urls") or {}).get("spotify") if isinstance(item.get("external_urls"), dict) else None),
                }
            )

    return {
        "ok": True,
        "result": {
            "count": len(out),
            "playlists": out,
        },
        "error": None,
    }


def _find_playlist_by_name(name: str) -> Dict[str, Any]:
    needle = str(name or "").strip().lower()
    if not needle:
        return {
            "ok": False,
            "result": None,
            "error": "playlist_name_required",
        }

    listed = _list_playlists(limit=50)
    if not listed.get("ok"):
        return listed

    rows = (listed.get("result") or {}).get("playlists") or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pname = str(row.get("name") or "").strip().lower()
        if pname == needle or needle in pname:
            return {
                "ok": True,
                "result": row,
                "error": None,
            }

    return {
        "ok": False,
        "result": {
            "searched": name,
            "count": len(rows),
        },
        "error": "playlist_not_found",
    }


def _list_playlist_tracks(playlist_id: str, limit: int = 50) -> Dict[str, Any]:
    pid = str(playlist_id or "").strip()
    if not pid:
        return {
            "ok": False,
            "result": None,
            "error": "playlist_id_required",
        }

    safe_limit = max(1, min(100, int(limit)))
    r = _api_request(
        "GET",
        f"/v1/playlists/{pid}/tracks",
        params={"limit": safe_limit},
    )
    if not r.get("ok"):
        return r

    items = (r.get("result") or {}).get("items") if isinstance(r.get("result"), dict) else []
    out = []
    if isinstance(items, list):
        for row in items:
            if not isinstance(row, dict):
                continue
            track = row.get("track") if isinstance(row.get("track"), dict) else {}
            artists = track.get("artists") if isinstance(track.get("artists"), list) else []
            artist_names = [str(a.get("name") or "").strip() for a in artists if isinstance(a, dict) and str(a.get("name") or "").strip()]
            out.append(
                {
                    "name": track.get("name"),
                    "uri": track.get("uri"),
                    "id": track.get("id"),
                    "artists": artist_names,
                    "album": ((track.get("album") or {}).get("name") if isinstance(track.get("album"), dict) else None),
                }
            )

    return {
        "ok": True,
        "result": {
            "playlist_id": pid,
            "count": len(out),
            "tracks": out,
        },
        "error": None,
    }


def _current_playback() -> Dict[str, Any]:
    r = _api_request("GET", "/v1/me/player")
    if not r.get("ok"):
        return r

    body = r.get("result") if isinstance(r.get("result"), dict) else {}
    item = body.get("item") if isinstance(body.get("item"), dict) else {}
    device = body.get("device") if isinstance(body.get("device"), dict) else {}
    artists = item.get("artists") if isinstance(item.get("artists"), list) else []

    artist_names = []
    for a in artists:
        if isinstance(a, dict):
            name = str(a.get("name") or "").strip()
            if name:
                artist_names.append(name)

    return {
        "ok": True,
        "result": {
            "is_playing": bool(body.get("is_playing")),
            "progress_ms": body.get("progress_ms"),
            "device": {
                "id": device.get("id"),
                "name": device.get("name"),
                "type": device.get("type"),
                "is_active": device.get("is_active"),
            },
            "track": {
                "name": item.get("name"),
                "uri": item.get("uri"),
                "artists": artist_names,
                "album": ((item.get("album") or {}).get("name") if isinstance(item.get("album"), dict) else None),
            },
        },
        "error": None,
    }


def _favorite_tracks(limit: int = 20) -> Dict[str, Any]:
    safe_limit = max(1, min(50, int(limit)))
    r = _api_request("GET", "/v1/me/tracks", params={"limit": safe_limit})
    if not r.get("ok"):
        return r

    items = (r.get("result") or {}).get("items") if isinstance(r.get("result"), dict) else []
    out = []
    if isinstance(items, list):
        for row in items:
            if not isinstance(row, dict):
                continue
            track = row.get("track") if isinstance(row.get("track"), dict) else {}
            artists = track.get("artists") if isinstance(track.get("artists"), list) else []
            artist_names = [str(a.get("name") or "").strip() for a in artists if isinstance(a, dict) and str(a.get("name") or "").strip()]
            out.append(
                {
                    "name": track.get("name"),
                    "uri": track.get("uri"),
                    "id": track.get("id"),
                    "artists": artist_names,
                    "album": ((track.get("album") or {}).get("name") if isinstance(track.get("album"), dict) else None),
                    "saved_at": row.get("added_at"),
                }
            )

    return {
        "ok": True,
        "result": {
            "count": len(out),
            "tracks": out,
        },
        "error": None,
    }


def _play_favorite(track_query: str = "", device_id: str = "", pick_random: bool = False) -> Dict[str, Any]:
    fav = _favorite_tracks(limit=50)
    if not fav.get("ok"):
        return fav

    tracks = (fav.get("result") or {}).get("tracks") or []
    if not tracks:
        return {
            "ok": False,
            "result": fav.get("result"),
            "error": "favorites_library_empty_or_unavailable",
        }

    selected = None
    q = str(track_query or "").strip().lower()
    if q:
        for t in tracks:
            if not isinstance(t, dict):
                continue
            name = str(t.get("name") or "").lower()
            artists = " ".join(str(a or "") for a in (t.get("artists") or [])).lower()
            album = str(t.get("album") or "").lower()
            if q in name or q in artists or q in album:
                selected = t
                break

    if selected is None and pick_random and tracks:
        selected = random.choice(tracks)

    # If user asked for a specific favorite and we could not match it,
    # do not silently fall back to another track.
    if selected is None and q:
        return {
            "ok": False,
            "result": {
                "query": track_query,
                "count": len(tracks),
            },
            "error": "favorite_track_not_found",
        }

    if selected is None:
        selected = tracks[0] if tracks else None

    if not isinstance(selected, dict):
        return {
            "ok": False,
            "result": fav.get("result"),
            "error": "favorite_track_selection_failed",
        }

    uri = str(selected.get("uri") or "").strip()
    if not uri:
        return {
            "ok": False,
            "result": selected,
            "error": "favorite_track_missing_uri",
        }

    play = _play_track(track_uri=uri, device_id=device_id)
    if not play.get("ok"):
        return play

    return {
        "ok": True,
        "result": {
            "played": {
                "name": selected.get("name"),
                "uri": selected.get("uri"),
                "artists": selected.get("artists"),
                "album": selected.get("album"),
            },
            "source": "favorites_library",
        },
        "error": None,
    }


def _split_pipe_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    return [p.strip() for p in text.split("|") if p.strip()]


def tool_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    in_args = args if isinstance(args, dict) else {}
    action = str(in_args.get("action") or "").strip().lower()

    # Some models emit shorthand calls such as:
    # call:spotify_link{library:'true', track_selection:'random'}
    # Infer the intended action instead of hard-failing.
    if not action:
        library_hint = str(in_args.get("library") or "").strip().lower()
        selection_hint = str(in_args.get("track_selection") or "").strip().lower()
        track_hint = str(in_args.get("track_query") or in_args.get("query") or "").strip()
        if library_hint in {"1", "true", "yes", "on", "favorites", "favourites", "liked", "liked_songs", "liked songs"}:
            action = "play_favorite"
        elif selection_hint in {"random", "shuffle"}:
            action = "play_favorite"
        elif track_hint:
            action = "play_track"
        else:
            # Automation default: if no action/args are clear, try playing a random liked track.
            action = "play_favorite"
            in_args = dict(in_args)
            in_args.setdefault("track_selection", "random")

    if not action:
        return {
            "ok": False,
            "result": None,
            "error": "action_required",
        }

    device_id = str(in_args.get("device_id") or _default_device_id() or "").strip()

    if action in {"play", "play_spotify", "resume"}:
        query = str(in_args.get("track_query") or in_args.get("query") or "").strip()
        if query:
            found = _search_track(query=query, limit=1)
            if not found.get("ok"):
                return found
            tracks = (found.get("result") or {}).get("tracks") or []
            if not tracks:
                return {
                    "ok": False,
                    "result": found.get("result"),
                    "error": "track_not_found",
                }
            return _play_searched_track(track_row=(tracks[0] or {}), device_id=device_id, requested_query=query)

        if action == "resume":
            return _play(payload={}, device_id=device_id)

        selection_hint = str(in_args.get("track_selection") or "").strip().lower()
        pick_random = selection_hint in {"random", "shuffle"}
        return _play_favorite(track_query="", device_id=device_id, pick_random=True)

    if action == "connect_status":
        me = _current_user()
        if not me.get("ok"):
            return me
        return {
            "ok": True,
            "result": {
                "connected": True,
                "user": me.get("result"),
            },
            "error": None,
        }

    if action == "search_track":
        query = str(in_args.get("track_query") or in_args.get("query") or "").strip()
        limit = int(in_args.get("limit") or 5)
        return _search_track(query=query, limit=limit)

    if action == "create_playlist":
        playlist_name = str(in_args.get("playlist_name") or in_args.get("name") or "").strip()
        description = str(in_args.get("description") or "").strip()
        public = in_args.get("public")
        created = _create_playlist(
            name=playlist_name,
            description=description,
            public=_truthy(public) if public is not None else None,
        )
        if not created.get("ok"):
            return created

        # Optional seed track list by query list (pipe-separated).
        seeds = _split_pipe_list(in_args.get("track_queries"))
        if not seeds:
            return created

        added_uris = []
        for q in seeds[:20]:
            sr = _search_track(query=q, limit=1)
            if not sr.get("ok"):
                continue
            tracks = (sr.get("result") or {}).get("tracks") or []
            if tracks and isinstance(tracks[0], dict):
                uri = str(tracks[0].get("uri") or "").strip()
                if uri:
                    added_uris.append(uri)

        if not added_uris:
            return created

        playlist_id = str((created.get("result") or {}).get("id") or "")
        add_res = _add_tracks_to_playlist(playlist_id=playlist_id, uris=added_uris)
        result = dict(created.get("result") or {})
        result["seeded_tracks"] = len(added_uris)
        result["seed_add_result"] = add_res
        return {
            "ok": bool(add_res.get("ok")),
            "result": result,
            "error": None if add_res.get("ok") else add_res.get("error"),
        }

    if action == "add_track_to_playlist":
        playlist_id = str(in_args.get("playlist_id") or "").strip()
        direct_uri = str(in_args.get("track_uri") or "").strip()
        if direct_uri:
            uris = [direct_uri]
        else:
            query = str(in_args.get("track_query") or "").strip()
            if not query:
                return {
                    "ok": False,
                    "result": None,
                    "error": "track_uri_or_track_query_required",
                }
            found = _search_track(query=query, limit=1)
            if not found.get("ok"):
                return found
            tracks = (found.get("result") or {}).get("tracks") or []
            if not tracks:
                return {
                    "ok": False,
                    "result": found.get("result"),
                    "error": "track_not_found",
                }
            uris = [str((tracks[0] or {}).get("uri") or "").strip()]

        return _add_tracks_to_playlist(playlist_id=playlist_id, uris=uris)

    if action == "play_track":
        library_hint = str(in_args.get("library") or "").strip().lower()
        if library_hint in {"favorites", "favourites", "liked", "liked_songs", "liked songs"}:
            query = str(in_args.get("track_query") or in_args.get("query") or "").strip()
            return _play_favorite(track_query=query, device_id=device_id)

        if library_hint in {"1", "true", "yes", "on"}:
            query = str(in_args.get("track_query") or in_args.get("query") or "").strip()
            selection_hint = str(in_args.get("track_selection") or "").strip().lower()
            pick_random = selection_hint in {"random", "shuffle"}
            return _play_favorite(track_query=query, device_id=device_id, pick_random=pick_random)

        direct_uri = str(in_args.get("track_uri") or "").strip()
        if direct_uri:
            return _play_track(track_uri=direct_uri, device_id=device_id)

        query = str(in_args.get("track_query") or in_args.get("query") or "").strip()
        if not query:
            return {
                "ok": False,
                "result": None,
                "error": "track_uri_or_track_query_required",
            }
        found = _search_track(query=query, limit=1)
        if not found.get("ok"):
            return found
        tracks = (found.get("result") or {}).get("tracks") or []
        if not tracks:
            return {
                "ok": False,
                "result": found.get("result"),
                "error": "track_not_found",
            }
        return _play_searched_track(track_row=(tracks[0] or {}), device_id=device_id, requested_query=query)

    if action == "play_playlist":
        playlist_uri = str(in_args.get("playlist_uri") or "").strip()
        shuffle_hint = str(in_args.get("track_selection") or in_args.get("shuffle") or "").strip().lower()
        if shuffle_hint in {"random", "shuffle", "true", "on", "1"}:
            _set_shuffle(state=True, device_id=device_id)
        return _play_playlist(playlist_uri=playlist_uri, device_id=device_id)

    if action == "pause":
        return _pause(device_id=device_id)

    if action == "stop":
        # Spotify Web API supports pause as the stop-equivalent.
        return _pause(device_id=device_id)

    if action == "shuffle_on":
        return _set_shuffle(state=True, device_id=device_id)

    if action == "shuffle_off":
        return _set_shuffle(state=False, device_id=device_id)

    if action == "set_shuffle":
        state = _truthy(args.get("state"), default=False)
        return _set_shuffle(state=state, device_id=device_id)

    if action == "list_playlists":
        limit = int(in_args.get("limit") or 20)
        return _list_playlists(limit=limit)

    if action == "current_playback":
        return _current_playback()

    if action == "favorites_list":
        limit = int(in_args.get("limit") or 20)
        return _favorite_tracks(limit=limit)

    if action == "liked_songs":
        limit = int(in_args.get("limit") or 20)
        return _favorite_tracks(limit=limit)

    if action == "play_favorite":
        query = str(in_args.get("track_query") or in_args.get("query") or "").strip()
        selection_hint = str(in_args.get("track_selection") or "").strip().lower()
        pick_random = selection_hint in {"random", "shuffle"}
        return _play_favorite(track_query=query, device_id=device_id, pick_random=pick_random)

    if action == "add_tracks_to_playlist":
        playlist_id = str(in_args.get("playlist_id") or "").strip()
        uris = _split_pipe_list(in_args.get("track_uris"))
        return _add_tracks_to_playlist(playlist_id=playlist_id, uris=uris)

    if action == "playlist_tracks":
        playlist_id = str(in_args.get("playlist_id") or "").strip()
        playlist_name = str(in_args.get("playlist_name") or in_args.get("name") or "").strip()
        limit = int(in_args.get("limit") or 50)
        if not playlist_id and playlist_name:
            found = _find_playlist_by_name(playlist_name)
            if not found.get("ok"):
                return found
            playlist_id = str((found.get("result") or {}).get("id") or "").strip()
        return _list_playlist_tracks(playlist_id=playlist_id, limit=limit)

    return {
        "ok": False,
        "result": None,
        "error": f"unsupported_action: {action}",
    }


tool_entry.schema = {
    "description": "Control Spotify playback and playlists using your connected account.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action: connect_status, search_track, create_playlist, add_track_to_playlist, add_tracks_to_playlist, play, play_spotify, resume, play_track, play_playlist, pause, stop, shuffle_on, shuffle_off, set_shuffle, list_playlists, playlist_tracks, current_playback, favorites_list, liked_songs, play_favorite",
            },
            "playlist_name": {
                "type": "string",
                "description": "Playlist name for create_playlist.",
            },
            "description": {
                "type": "string",
                "description": "Playlist description.",
            },
            "public": {
                "type": "boolean",
                "description": "Whether playlist is public.",
            },
            "track_query": {
                "type": "string",
                "description": "Natural language track search query.",
            },
            "track_queries": {
                "type": "string",
                "description": "Pipe-separated track queries for seeding new playlist. Example: song one | song two",
            },
            "track_uri": {
                "type": "string",
                "description": "Explicit Spotify track URI (spotify:track:...).",
            },
            "track_uris": {
                "type": "string",
                "description": "Pipe-separated Spotify track URIs.",
            },
            "playlist_id": {
                "type": "string",
                "description": "Spotify playlist id.",
            },
            "playlist_uri": {
                "type": "string",
                "description": "Spotify playlist URI (spotify:playlist:...).",
            },
            "device_id": {
                "type": "string",
                "description": "Spotify target device id. Optional if one device is active.",
            },
            "state": {
                "type": "boolean",
                "description": "Used by set_shuffle to enable or disable shuffle.",
            },
            "limit": {
                "type": "integer",
                "description": "Result limit for list/search actions.",
                "minimum": 1,
                "maximum": 50,
            },
        },
        "required": ["action"],
    },
}
