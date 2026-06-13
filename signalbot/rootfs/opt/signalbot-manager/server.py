#!/usr/bin/env python3
"""Signalbot Manager — management web app for the Signalbot Home Assistant add-on.

A single self-contained HTTP server (Python standard library only, no third-party
dependencies) that:

  * Serves a sleek single-page management UI (inlined HTML/CSS/JS) over HA ingress.
  * Proxies the device-linking QR code from the bundled signal-cli-rest-api.
  * Exposes a small JSON API for the companion integration and for the UI itself
    (status, config, recipients/chat-partners CRUD, settings).
  * Persists configuration to ``/data/signalbot.json`` using atomic writes guarded
    by a lock.
  * Announces itself to Home Assistant via Supervisor discovery in a background
    thread at startup (best-effort, with retry/backoff).

The manager is intentionally *config/UI/QR only* — it never sends or receives
Signal messages (the companion integration owns that).

Runtime environment (env vars):
  SIGNAL_API_URL            default http://127.0.0.1:8080 (bundled signal-cli-rest-api)
  SIGNALBOT_MANAGER_PORT    default 8099
  SUPERVISOR_TOKEN          bearer token for the Supervisor API (optional)
  MODE                      signal-cli mode hint (default "normal")
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

# --------------------------------------------------------------------------- #
# Configuration / constants
# --------------------------------------------------------------------------- #

SIGNAL_API_URL: str = os.environ.get("SIGNAL_API_URL", "http://127.0.0.1:8080").rstrip("/")
MANAGER_PORT: int = int(os.environ.get("SIGNALBOT_MANAGER_PORT", "8099"))
SUPERVISOR_TOKEN: str | None = os.environ.get("SUPERVISOR_TOKEN") or None
DEFAULT_MODE: str = os.environ.get("MODE", "normal")
SUPERVISOR_URL: str = "http://supervisor"

CONFIG_PATH: str = "/data/signalbot.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "device_name": "Home Assistant",
    "known_senders_only": True,
    "poll_interval": 5,
    "recipients": [],
}

# Hostname (e.g. "local-signalbot") resolved once from Supervisor at startup.
# Used to derive the api_url advertised to the integration.
_ADDON_HOSTNAME: str | None = None

_CONFIG_LOCK = threading.Lock()


def log(message: str) -> None:
    """Log to stdout (captured by supervisord)."""
    print(f"[signalbot-manager] {message}", flush=True)


# --------------------------------------------------------------------------- #
# Config persistence
# --------------------------------------------------------------------------- #

def _normalize_recipient(raw: Any) -> dict[str, Any] | None:
    """Coerce a stored/loaded recipient into a clean dict, or None if invalid."""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name", "")).strip()
    phone = str(raw.get("phone_number", "") or "").strip()
    username = str(raw.get("username", "") or "").strip()
    if not name or (not phone and not username):
        return None
    prefer = raw.get("prefer", "phone")
    if prefer not in ("phone", "username"):
        prefer = "phone"
    rid = str(raw.get("id") or uuid.uuid4())
    return {
        "id": rid,
        "name": name,
        "phone_number": phone,
        "username": username,
        "prefer": prefer,
    }


def load_config() -> dict[str, Any]:
    """Load config from disk, applying defaults for anything missing/invalid."""
    cfg = dict(DEFAULT_CONFIG)
    cfg["recipients"] = []
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return cfg
    except (OSError, ValueError) as exc:
        log(f"WARNING could not read config ({exc!r}); using defaults")
        return cfg

    if not isinstance(data, dict):
        return cfg

    dn = data.get("device_name")
    if isinstance(dn, str) and dn.strip():
        cfg["device_name"] = dn.strip()

    if isinstance(data.get("known_senders_only"), bool):
        cfg["known_senders_only"] = data["known_senders_only"]

    try:
        pi = int(data.get("poll_interval", DEFAULT_CONFIG["poll_interval"]))
        cfg["poll_interval"] = max(2, pi)
    except (TypeError, ValueError):
        pass

    recipients: list[dict[str, Any]] = []
    for raw in data.get("recipients", []) or []:
        norm = _normalize_recipient(raw)
        if norm is not None:
            recipients.append(norm)
    cfg["recipients"] = recipients
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    """Atomically persist the config to disk (write temp + os.replace)."""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    tmp_path = f"{CONFIG_PATH}.tmp.{os.getpid()}"
    payload = json.dumps(cfg, indent=2, ensure_ascii=False)
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, CONFIG_PATH)


# --------------------------------------------------------------------------- #
# signal-cli-rest-api helpers
# --------------------------------------------------------------------------- #

def _http_get_json(url: str, timeout: float = 5.0) -> Any:
    req = urlrequest.Request(url, method="GET")
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def signal_about() -> dict[str, Any] | None:
    """Return parsed /v1/about, or None if unreachable."""
    try:
        data = _http_get_json(f"{SIGNAL_API_URL}/v1/about", timeout=4.0)
        return data if isinstance(data, dict) else None
    except (urlerror.URLError, OSError, ValueError) as exc:
        log(f"signal about unreachable: {exc!r}")
        return None


def signal_accounts() -> list[str] | None:
    """Return linked accounts list (possibly empty), or None if unreachable."""
    try:
        data = _http_get_json(f"{SIGNAL_API_URL}/v1/accounts", timeout=4.0)
        if isinstance(data, list):
            return [str(x) for x in data]
        return None
    except (urlerror.URLError, OSError, ValueError) as exc:
        log(f"signal accounts unreachable: {exc!r}")
        return None


def signal_status() -> dict[str, Any]:
    """Aggregate linked/number/mode/version for /api/status."""
    accounts = signal_accounts()
    about = signal_about()
    if accounts is None and about is None:
        # signal-cli unreachable
        return {"linked": False, "number": None, "mode": "unknown", "version": ""}

    linked = bool(accounts)
    number = accounts[0] if accounts else None
    mode = DEFAULT_MODE
    version = ""
    if about:
        mode = str(about.get("mode") or mode)
        versions = about.get("versions")
        if isinstance(versions, list) and versions:
            version = str(versions[0])
        elif isinstance(about.get("version"), (str, int, float)):
            version = str(about.get("version"))
    return {"linked": linked, "number": number, "mode": mode, "version": version}


def signal_qrcode(device_name: str) -> bytes | None:
    """Fetch a fresh device-linking QR PNG; None if unreachable."""
    qs = urlparse.urlencode({"device_name": device_name})
    url = f"{SIGNAL_API_URL}/v1/qrcodelink?{qs}"
    try:
        req = urlrequest.Request(url, method="GET")
        with urlrequest.urlopen(req, timeout=8.0) as resp:
            return resp.read()
    except (urlerror.URLError, OSError) as exc:
        log(f"qrcode unreachable: {exc!r}")
        return None


# --------------------------------------------------------------------------- #
# Supervisor discovery (background thread)
# --------------------------------------------------------------------------- #

def _supervisor_request(path: str, method: str = "GET",
                        body: dict[str, Any] | None = None,
                        timeout: float = 8.0) -> Any:
    assert SUPERVISOR_TOKEN is not None
    data = None
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urlrequest.Request(f"{SUPERVISOR_URL}{path}", data=data,
                             method=method, headers=headers)
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _resolve_hostname() -> str | None:
    try:
        info = _supervisor_request("/addons/self/info")
        data = info.get("data") if isinstance(info, dict) else None
        if isinstance(data, dict):
            host = data.get("hostname")
            if isinstance(host, str) and host:
                return host
    except (urlerror.URLError, OSError, ValueError) as exc:
        log(f"resolve hostname failed: {exc!r}")
    return None


def _announce_discovery(hostname: str) -> bool:
    try:
        body = {
            "service": "signalbot",
            "config": {"host": hostname, "port": int(MANAGER_PORT)},
        }
        resp = _supervisor_request("/discovery", method="POST", body=body)
        log(f"discovery announced: {resp}")
        return True
    except (urlerror.URLError, OSError, ValueError) as exc:
        log(f"discovery announce failed: {exc!r}")
        return False


def discovery_worker() -> None:
    """Resolve hostname + announce to Supervisor, with retry/backoff."""
    global _ADDON_HOSTNAME
    if not SUPERVISOR_TOKEN:
        log("SUPERVISOR_TOKEN absent; skipping Supervisor discovery")
        return

    backoff = 2.0
    # Resolve hostname (retry until success or give up after many tries).
    for _ in range(30):
        host = _resolve_hostname()
        if host:
            _ADDON_HOSTNAME = host
            log(f"resolved addon hostname: {host}")
            break
        time.sleep(backoff)
        backoff = min(backoff * 1.5, 30.0)

    if not _ADDON_HOSTNAME:
        log("could not resolve addon hostname; api_url will be null")
        return

    backoff = 2.0
    while True:
        if _announce_discovery(_ADDON_HOSTNAME):
            return
        time.sleep(backoff)
        backoff = min(backoff * 1.5, 60.0)


# --------------------------------------------------------------------------- #
# Recipient / settings business logic
# --------------------------------------------------------------------------- #

def _validate_recipient_payload(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Validate a recipient create/update body. Returns (clean_fields, error)."""
    if not isinstance(payload, dict):
        return None, "Invalid JSON body"
    name = str(payload.get("name", "") or "").strip()
    phone = str(payload.get("phone_number", "") or "").strip()
    username = str(payload.get("username", "") or "").strip()
    prefer = payload.get("prefer", "phone")

    if not name:
        return None, "Name is required"
    if not phone and not username:
        return None, "Provide at least a phone number or a username"
    if prefer not in ("phone", "username"):
        return None, "prefer must be 'phone' or 'username'"
    return {
        "name": name,
        "phone_number": phone,
        "username": username,
        "prefer": prefer,
    }, None


# --------------------------------------------------------------------------- #
# HTTP request handler
# --------------------------------------------------------------------------- #

class Handler(BaseHTTPRequestHandler):
    server_version = "SignalbotManager/1.0"
    protocol_version = "HTTP/1.1"

    # ---- low level helpers -------------------------------------------- #

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
        log("%s - %s" % (self.address_string(), fmt % args))

    def _no_cache_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")

    def _send_json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._no_cache_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_no_content(self) -> None:
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._no_cache_headers()
        self.end_headers()

    def _send_bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self._no_cache_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._no_cache_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _read_json_body(self) -> Any:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _clean_path(self) -> str:
        """Path with query stripped, trailing slash removed (except root)."""
        path = urlparse.urlparse(self.path).path
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")
        return path or "/"

    # ---- dispatch ----------------------------------------------------- #

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_HEAD(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        try:
            self._route(method, self._clean_path())
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001 - never let a request crash the server
            log(f"ERROR handling {method} {self.path}: {exc!r}")
            try:
                self._send_json({"error": "internal server error"}, status=500)
            except Exception:  # noqa: BLE001
                pass

    def _route(self, method: str, path: str) -> None:
        # Static UI
        if path == "/" and method == "GET":
            self._send_html(INDEX_HTML)
            return

        # ----- /api/status ------------------------------------------- #
        if path == "/api/status" and method == "GET":
            self._send_json(signal_status())
            return

        # ----- /api/qrcode ------------------------------------------- #
        if path == "/api/qrcode" and method == "GET":
            with _CONFIG_LOCK:
                device_name = load_config()["device_name"]
            png = signal_qrcode(device_name)
            if png is None:
                self._send_json({"error": "signal-cli unreachable"}, status=503)
                return
            self._send_bytes(png, "image/png")
            return

        # ----- /api/config (for the integration) --------------------- #
        if path == "/api/config" and method == "GET":
            self._send_json(self._build_config_response())
            return

        # ----- /api/recipients --------------------------------------- #
        if path == "/api/recipients":
            if method == "GET":
                with _CONFIG_LOCK:
                    self._send_json(load_config()["recipients"])
                return
            if method == "POST":
                self._create_recipient()
                return

        if path.startswith("/api/recipients/"):
            rid = path[len("/api/recipients/"):]
            if rid:
                if method == "PUT":
                    self._update_recipient(rid)
                    return
                if method == "DELETE":
                    self._delete_recipient(rid)
                    return

        # ----- /api/settings ----------------------------------------- #
        if path == "/api/settings" and method == "POST":
            self._update_settings()
            return

        # ----- /api/unlink (stub) ------------------------------------ #
        if path == "/api/unlink" and method == "POST":
            self._send_json({"error": "not supported"}, status=501)
            return

        self._send_json({"error": "not found"}, status=404)

    # ---- handlers ----------------------------------------------------- #

    def _build_config_response(self) -> dict[str, Any]:
        with _CONFIG_LOCK:
            cfg = load_config()
        status = signal_status()
        api_url: str | None = None
        if _ADDON_HOSTNAME:
            api_url = f"http://{_ADDON_HOSTNAME}:8080"
        return {
            "linked": status["linked"],
            "number": status["number"],
            "mode": status["mode"],
            "version": status["version"],
            "api_url": api_url,
            "recipients": cfg["recipients"],
            "known_senders_only": cfg["known_senders_only"],
            "poll_interval": cfg["poll_interval"],
            "device_name": cfg["device_name"],
        }

    def _create_recipient(self) -> None:
        try:
            payload = self._read_json_body()
        except ValueError:
            self._send_json({"error": "Invalid JSON body"}, status=400)
            return
        clean, err = _validate_recipient_payload(payload)
        if err:
            self._send_json({"error": err}, status=400)
            return
        assert clean is not None
        recipient = {"id": str(uuid.uuid4()), **clean}
        with _CONFIG_LOCK:
            cfg = load_config()
            cfg["recipients"].append(recipient)
            save_config(cfg)
        self._send_json(recipient, status=201)

    def _update_recipient(self, rid: str) -> None:
        try:
            payload = self._read_json_body()
        except ValueError:
            self._send_json({"error": "Invalid JSON body"}, status=400)
            return
        clean, err = _validate_recipient_payload(payload)
        if err:
            self._send_json({"error": err}, status=400)
            return
        assert clean is not None
        with _CONFIG_LOCK:
            cfg = load_config()
            for idx, rec in enumerate(cfg["recipients"]):
                if rec["id"] == rid:
                    updated = {"id": rid, **clean}
                    cfg["recipients"][idx] = updated
                    save_config(cfg)
                    self._send_json(updated, status=200)
                    return
        self._send_json({"error": "recipient not found"}, status=404)

    def _delete_recipient(self, rid: str) -> None:
        with _CONFIG_LOCK:
            cfg = load_config()
            new_list = [r for r in cfg["recipients"] if r["id"] != rid]
            if len(new_list) == len(cfg["recipients"]):
                self._send_json({"error": "recipient not found"}, status=404)
                return
            cfg["recipients"] = new_list
            save_config(cfg)
        self._send_no_content()

    def _update_settings(self) -> None:
        try:
            payload = self._read_json_body()
        except ValueError:
            self._send_json({"error": "Invalid JSON body"}, status=400)
            return
        if not isinstance(payload, dict):
            self._send_json({"error": "Invalid JSON body"}, status=400)
            return

        known = payload.get("known_senders_only")
        if not isinstance(known, bool):
            self._send_json({"error": "known_senders_only must be a boolean"}, status=400)
            return

        try:
            poll = int(payload.get("poll_interval"))
        except (TypeError, ValueError):
            self._send_json({"error": "poll_interval must be an integer"}, status=400)
            return
        if poll < 2:
            self._send_json({"error": "poll_interval must be >= 2"}, status=400)
            return

        device_name = str(payload.get("device_name", "") or "").strip()
        if not device_name:
            self._send_json({"error": "device_name is required"}, status=400)
            return

        with _CONFIG_LOCK:
            cfg = load_config()
            cfg["known_senders_only"] = known
            cfg["poll_interval"] = poll
            cfg["device_name"] = device_name
            save_config(cfg)
            result = {
                "known_senders_only": cfg["known_senders_only"],
                "poll_interval": cfg["poll_interval"],
                "device_name": cfg["device_name"],
            }
        self._send_json(result, status=200)


# --------------------------------------------------------------------------- #
# Inlined single-page UI
# --------------------------------------------------------------------------- #

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Signalbot</title>
<style>
  :root {
    --bg: #f4f6f9;
    --card: #ffffff;
    --text: #1c2533;
    --muted: #5b6877;
    --border: #e1e6ec;
    --accent: #2f7be9;
    --accent-text: #ffffff;
    --ok-bg: #e3f6ea; --ok-fg: #1c7a42; --ok-dot: #21a05a;
    --warn-bg: #fdf2dc; --warn-fg: #936314; --warn-dot: #e0a73a;
    --err-bg: #fceaea; --err-fg: #a32626; --err-dot: #d94646;
    --danger: #d94646;
    --radius: 14px;
    --shadow: 0 1px 3px rgba(20,30,50,.07), 0 6px 18px rgba(20,30,50,.05);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #11151b;
      --card: #1a2029;
      --text: #e7edf4;
      --muted: #9aa7b6;
      --border: #2a323d;
      --accent: #4a90f0;
      --ok-bg: #16321f; --ok-fg: #6fd99a; --ok-dot: #34c873;
      --warn-bg: #342a13; --warn-fg: #e6bf6e; --warn-dot: #e0a73a;
      --err-bg: #361a1a; --err-fg: #f08a8a; --err-dot: #e06464;
      --danger: #e06464;
      --shadow: 0 1px 3px rgba(0,0,0,.3), 0 6px 18px rgba(0,0,0,.3);
    }
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen,
      Ubuntu, Cantarell, "Helvetica Neue", Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }
  .wrap { max-width: 720px; margin: 0 auto; padding: 20px 16px 64px; }
  header.app {
    display: flex; align-items: center; justify-content: space-between;
    gap: 12px; margin-bottom: 20px; flex-wrap: wrap;
  }
  .brand { display: flex; align-items: center; gap: 10px; }
  .brand .logo {
    width: 34px; height: 34px; border-radius: 9px;
    background: linear-gradient(135deg, #2f7be9, #5aa2ff);
    display: grid; place-items: center; color: #fff; font-weight: 700;
    box-shadow: var(--shadow);
  }
  h1 { font-size: 1.3rem; margin: 0; letter-spacing: -.01em; }
  .pill {
    display: inline-flex; align-items: center; gap: 7px;
    font-size: .82rem; font-weight: 600; padding: 6px 12px;
    border-radius: 999px; background: var(--warn-bg); color: var(--warn-fg);
    max-width: 100%;
  }
  .pill .dot {
    width: 9px; height: 9px; border-radius: 50%;
    background: var(--warn-dot); flex: 0 0 auto;
  }
  .pill.ok { background: var(--ok-bg); color: var(--ok-fg); }
  .pill.ok .dot { background: var(--ok-dot); }
  .pill.err { background: var(--err-bg); color: var(--err-fg); }
  .pill.err .dot { background: var(--err-dot); }
  .pill .txt { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: var(--radius); box-shadow: var(--shadow);
    padding: 20px; margin-bottom: 18px;
  }
  .card h2 { margin: 0 0 4px; font-size: 1.08rem; letter-spacing: -.01em; }
  .card .sub { color: var(--muted); font-size: .9rem; margin: 0 0 16px; }

  ol.steps { margin: 0 0 18px; padding-left: 20px; color: var(--muted); font-size: .92rem; }
  ol.steps li { margin: 4px 0; }

  .qrbox {
    display: grid; place-items: center; gap: 10px;
    padding: 16px; border: 1px dashed var(--border); border-radius: var(--radius);
    background: var(--bg);
  }
  .qrbox img {
    width: 240px; height: 240px; max-width: 100%;
    background: #fff; border-radius: 10px; padding: 8px;
    image-rendering: pixelated;
  }
  .qrbox .hint { color: var(--muted); font-size: .82rem; }

  label { display: block; font-size: .85rem; font-weight: 600; margin: 0 0 5px; }
  input[type=text], input[type=tel], input[type=number], select {
    width: 100%; padding: 10px 12px; font-size: .95rem;
    border: 1px solid var(--border); border-radius: 10px;
    background: var(--bg); color: var(--text); outline: none;
  }
  input:focus, select:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(47,123,233,.18); }
  .field { margin-bottom: 12px; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  @media (max-width: 520px) { .grid2 { grid-template-columns: 1fr; } }
  .helper { color: var(--muted); font-size: .78rem; margin-top: 5px; }

  button {
    font: inherit; cursor: pointer; border: 1px solid transparent;
    border-radius: 10px; padding: 9px 16px; font-weight: 600; font-size: .9rem;
    background: var(--accent); color: var(--accent-text); transition: filter .15s;
  }
  button:hover { filter: brightness(1.06); }
  button:active { filter: brightness(.95); }
  button.ghost { background: transparent; color: var(--text); border-color: var(--border); }
  button.danger { background: transparent; color: var(--danger); border-color: var(--border); }
  button.small { padding: 6px 11px; font-size: .82rem; }
  .row-actions { display: flex; gap: 8px; }

  .rec-list { display: flex; flex-direction: column; gap: 10px; margin-bottom: 18px; }
  .rec {
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px;
    background: var(--bg);
  }
  .rec .info { min-width: 0; }
  .rec .name { font-weight: 600; }
  .rec .badges { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }
  .badge {
    font-size: .74rem; font-weight: 600; padding: 2px 8px; border-radius: 999px;
    background: var(--card); border: 1px solid var(--border); color: var(--muted);
  }
  .badge.pref { color: var(--accent); border-color: var(--accent); }
  .empty { color: var(--muted); font-size: .9rem; padding: 8px 0; }

  .inline-err { color: var(--err-fg); font-size: .82rem; margin-top: 8px; min-height: 1em; }

  .switch { display: flex; align-items: center; gap: 10px; cursor: pointer; }
  .switch input { position: absolute; opacity: 0; width: 0; height: 0; }
  .track {
    width: 44px; height: 26px; border-radius: 999px; background: var(--border);
    position: relative; transition: background .15s; flex: 0 0 auto;
  }
  .track::after {
    content: ""; position: absolute; top: 3px; left: 3px; width: 20px; height: 20px;
    border-radius: 50%; background: #fff; transition: transform .15s;
    box-shadow: 0 1px 2px rgba(0,0,0,.3);
  }
  .switch input:checked + .track { background: var(--accent); }
  .switch input:checked + .track::after { transform: translateX(18px); }

  .toast-wrap {
    position: fixed; bottom: 18px; left: 50%; transform: translateX(-50%);
    display: flex; flex-direction: column; gap: 8px; z-index: 50; width: calc(100% - 32px); max-width: 420px;
  }
  .toast {
    background: var(--card); border: 1px solid var(--border); border-left: 4px solid var(--accent);
    border-radius: 10px; padding: 11px 14px; box-shadow: var(--shadow); font-size: .88rem;
    animation: slidein .18s ease;
  }
  .toast.ok { border-left-color: var(--ok-dot); }
  .toast.err { border-left-color: var(--danger); }
  @keyframes slidein { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }

  .hidden { display: none !important; }
  .edit-form { margin-top: 10px; padding-top: 12px; border-top: 1px solid var(--border); }
</style>
</head>
<body>
<div class="wrap">
  <header class="app">
    <div class="brand">
      <div class="logo" aria-hidden="true">S</div>
      <h1>Signalbot</h1>
    </div>
    <div id="statusPill" class="pill" role="status" aria-live="polite">
      <span class="dot"></span><span class="txt">Checking…</span>
    </div>
  </header>

  <!-- Linking card (not linked) -->
  <section id="linkCard" class="card hidden">
    <h2>Link your Signal account</h2>
    <p class="sub">Connect this add-on to your Signal account as a linked device.</p>
    <ol class="steps">
      <li>Open <strong>Signal</strong> on your phone.</li>
      <li>Go to <strong>Settings → Linked devices</strong>.</li>
      <li>Tap <strong>Link new device</strong> (the “+” / camera).</li>
      <li>Scan the QR code below.</li>
    </ol>
    <div class="qrbox">
      <img id="qrImg" alt="Signal device-linking QR code" src="api/qrcode">
      <div class="hint">The code refreshes automatically every 30 seconds.</div>
    </div>
  </section>

  <!-- Chat partners (linked) -->
  <section id="partnersCard" class="card hidden">
    <h2>Chat partners</h2>
    <p class="sub">People the bot can talk to. Incoming-message matching uses the phone number (Signal limitation).</p>
    <div id="recList" class="rec-list"></div>

    <div class="edit-form" style="border-top:none;padding-top:0">
      <h2 style="font-size:.98rem;margin-bottom:10px">Add chat partner</h2>
      <div class="field">
        <label for="addName">Name <span style="color:var(--danger)">*</span></label>
        <input id="addName" type="text" placeholder="Alice" autocomplete="off">
      </div>
      <div class="grid2">
        <div class="field">
          <label for="addPhone">Phone number</label>
          <input id="addPhone" type="tel" placeholder="+4915123456789" autocomplete="off">
          <div class="helper">E.164 format, e.g. +49…</div>
        </div>
        <div class="field">
          <label for="addUser">Username</label>
          <input id="addUser" type="text" placeholder="alice.42" autocomplete="off">
        </div>
      </div>
      <div class="field">
        <label for="addPrefer">Prefer</label>
        <select id="addPrefer">
          <option value="phone">Phone</option>
          <option value="username">Username</option>
        </select>
      </div>
      <div class="row-actions">
        <button id="addBtn" type="button">Add chat partner</button>
      </div>
      <div id="addErr" class="inline-err"></div>
    </div>
  </section>

  <!-- Settings (linked) -->
  <section id="settingsCard" class="card hidden">
    <h2>Settings</h2>
    <p class="sub">Behaviour of the Signalbot.</p>
    <div class="field">
      <label class="switch" for="setKnown">
        <input id="setKnown" type="checkbox">
        <span class="track"></span>
        <span>Only react to known senders</span>
      </label>
      <div class="helper">When on, messages from anyone not in your chat partners list are ignored.</div>
    </div>
    <div class="grid2">
      <div class="field">
        <label for="setPoll">Receive poll interval (seconds)</label>
        <input id="setPoll" type="number" min="2" step="1" value="5">
      </div>
      <div class="field">
        <label for="setDevice">Device name</label>
        <input id="setDevice" type="text" placeholder="Home Assistant">
        <div class="helper">Label shown for this linked device.</div>
      </div>
    </div>
    <div class="row-actions">
      <button id="saveSettings" type="button">Save settings</button>
    </div>
    <div id="settingsErr" class="inline-err"></div>
  </section>
</div>

<div id="toasts" class="toast-wrap" aria-live="polite"></div>

<script>
(function () {
  "use strict";

  var QR_REFRESH_MS = 30000;
  var STATUS_POLL_MS = 4000;

  var qrTimer = null;
  var wasLinked = null; // null = unknown yet

  function $(id) { return document.getElementById(id); }

  function toast(msg, kind) {
    var wrap = $("toasts");
    var el = document.createElement("div");
    el.className = "toast" + (kind ? " " + kind : "");
    el.textContent = msg;
    wrap.appendChild(el);
    setTimeout(function () {
      el.style.transition = "opacity .25s";
      el.style.opacity = "0";
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 260);
    }, 3200);
  }

  function api(path, opts) {
    opts = opts || {};
    return fetch(path, opts).then(function (resp) {
      var ct = resp.headers.get("content-type") || "";
      var p = ct.indexOf("application/json") >= 0 ? resp.json() : resp.text();
      return p.then(function (data) {
        if (!resp.ok) {
          var msg = (data && data.error) ? data.error : ("Request failed (" + resp.status + ")");
          var err = new Error(msg); err.status = resp.status; throw err;
        }
        return data;
      });
    });
  }

  // ---- status pill + linked/unlinked switching ---------------------- //

  function setPill(cls, text) {
    var pill = $("statusPill");
    pill.className = "pill" + (cls ? " " + cls : "");
    pill.querySelector(".txt").textContent = text;
  }

  function applyStatus(st) {
    if (!st) {
      setPill("err", "signal-cli unreachable");
      return;
    }
    if (st.mode === "unknown" && !st.linked) {
      setPill("err", "signal-cli unreachable");
    } else if (st.linked) {
      setPill("ok", "Connected as " + (st.number || "Signal"));
    } else {
      setPill("", "Not linked");
    }

    var linked = !!st.linked;
    if (linked !== wasLinked) {
      var firstLoad = (wasLinked === null);
      wasLinked = linked;
      if (linked) {
        showLinked();
      } else {
        showUnlinked();
      }
      // Smooth transition: if we just became linked after being unlinked,
      // (not the very first paint) re-pull data already handled in showLinked.
      void firstLoad;
    }
  }

  function startQrRefresh() {
    if (qrTimer) return;
    refreshQr();
    qrTimer = setInterval(refreshQr, QR_REFRESH_MS);
  }
  function stopQrRefresh() {
    if (qrTimer) { clearInterval(qrTimer); qrTimer = null; }
  }
  function refreshQr() {
    var img = $("qrImg");
    if (img) img.src = "api/qrcode?ts=" + Date.now();
  }

  function showUnlinked() {
    $("linkCard").classList.remove("hidden");
    $("partnersCard").classList.add("hidden");
    $("settingsCard").classList.add("hidden");
    startQrRefresh();
  }

  function showLinked() {
    stopQrRefresh();
    $("linkCard").classList.add("hidden");
    $("partnersCard").classList.remove("hidden");
    $("settingsCard").classList.remove("hidden");
    loadRecipients();
    loadSettings();
  }

  function pollStatus() {
    api("api/status")
      .then(applyStatus)
      .catch(function () { applyStatus(null); });
  }

  // ---- recipients --------------------------------------------------- //

  function badge(text, cls) {
    var b = document.createElement("span");
    b.className = "badge" + (cls ? " " + cls : "");
    b.textContent = text;
    return b;
  }

  function renderRecipients(list) {
    var container = $("recList");
    container.innerHTML = "";
    if (!list || !list.length) {
      var e = document.createElement("div");
      e.className = "empty";
      e.textContent = "No chat partners yet. Add one below.";
      container.appendChild(e);
      return;
    }
    list.forEach(function (r) {
      container.appendChild(buildRecRow(r));
    });
  }

  function buildRecRow(r) {
    var row = document.createElement("div");
    row.className = "rec";

    var info = document.createElement("div");
    info.className = "info";
    var name = document.createElement("div");
    name.className = "name";
    name.textContent = r.name;
    info.appendChild(name);

    var badges = document.createElement("div");
    badges.className = "badges";
    if (r.phone_number) badges.appendChild(badge(r.phone_number, r.prefer === "phone" ? "pref" : ""));
    if (r.username) badges.appendChild(badge("@" + r.username, r.prefer === "username" ? "pref" : ""));
    info.appendChild(badges);
    row.appendChild(info);

    var actions = document.createElement("div");
    actions.className = "row-actions";
    var editBtn = document.createElement("button");
    editBtn.className = "ghost small";
    editBtn.textContent = "Edit";
    editBtn.onclick = function () { toggleEdit(row, r); };
    var delBtn = document.createElement("button");
    delBtn.className = "danger small";
    delBtn.textContent = "Delete";
    delBtn.onclick = function () { deleteRecipient(r, row); };
    actions.appendChild(editBtn);
    actions.appendChild(delBtn);
    row.appendChild(actions);
    return row;
  }

  function toggleEdit(row, r) {
    var existing = row.parentNode.querySelector(".edit-inline[data-for='" + r.id + "']");
    if (existing) { existing.parentNode.removeChild(existing); return; }
    var form = document.createElement("div");
    form.className = "card edit-inline";
    form.setAttribute("data-for", r.id);
    form.style.marginTop = "-4px";
    form.innerHTML =
      '<div class="field"><label>Name</label><input type="text" data-f="name"></div>' +
      '<div class="grid2">' +
        '<div class="field"><label>Phone number</label><input type="tel" data-f="phone"></div>' +
        '<div class="field"><label>Username</label><input type="text" data-f="user"></div>' +
      '</div>' +
      '<div class="field"><label>Prefer</label><select data-f="prefer">' +
        '<option value="phone">Phone</option><option value="username">Username</option>' +
      '</select></div>' +
      '<div class="row-actions"><button data-f="save">Save</button>' +
      '<button class="ghost" data-f="cancel">Cancel</button></div>' +
      '<div class="inline-err" data-f="err"></div>';
    form.querySelector('[data-f=name]').value = r.name || "";
    form.querySelector('[data-f=phone]').value = r.phone_number || "";
    form.querySelector('[data-f=user]').value = r.username || "";
    form.querySelector('[data-f=prefer]').value = r.prefer || "phone";
    form.querySelector('[data-f=cancel]').onclick = function () { form.parentNode.removeChild(form); };
    form.querySelector('[data-f=save]').onclick = function () {
      var body = {
        name: form.querySelector('[data-f=name]').value.trim(),
        phone_number: form.querySelector('[data-f=phone]').value.trim(),
        username: form.querySelector('[data-f=user]').value.trim(),
        prefer: form.querySelector('[data-f=prefer]').value
      };
      var errEl = form.querySelector('[data-f=err]');
      errEl.textContent = "";
      if (!body.name) { errEl.textContent = "Name is required"; return; }
      if (!body.phone_number && !body.username) { errEl.textContent = "Provide a phone number or username"; return; }
      api("api/recipients/" + encodeURIComponent(r.id), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      }).then(function () {
        toast("Chat partner updated", "ok");
        loadRecipients();
      }).catch(function (e) { errEl.textContent = e.message; });
    };
    row.parentNode.insertBefore(form, row.nextSibling);
  }

  function deleteRecipient(r, row) {
    if (!confirm('Delete "' + r.name + '"?')) return;
    api("api/recipients/" + encodeURIComponent(r.id), { method: "DELETE" })
      .then(function () { toast("Chat partner removed", "ok"); loadRecipients(); })
      .catch(function (e) { toast(e.message, "err"); });
  }

  function loadRecipients() {
    api("api/recipients")
      .then(renderRecipients)
      .catch(function (e) { toast("Could not load chat partners: " + e.message, "err"); });
  }

  function addRecipient() {
    var errEl = $("addErr");
    errEl.textContent = "";
    var body = {
      name: $("addName").value.trim(),
      phone_number: $("addPhone").value.trim(),
      username: $("addUser").value.trim(),
      prefer: $("addPrefer").value
    };
    if (!body.name) { errEl.textContent = "Name is required"; return; }
    if (!body.phone_number && !body.username) {
      errEl.textContent = "Provide at least a phone number or a username";
      return;
    }
    api("api/recipients", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function () {
      $("addName").value = "";
      $("addPhone").value = "";
      $("addUser").value = "";
      $("addPrefer").value = "phone";
      toast("Chat partner added", "ok");
      loadRecipients();
    }).catch(function (e) { errEl.textContent = e.message; });
  }

  // ---- settings ----------------------------------------------------- //

  function loadSettings() {
    api("api/config")
      .then(function (cfg) {
        $("setKnown").checked = !!cfg.known_senders_only;
        $("setPoll").value = cfg.poll_interval;
        $("setDevice").value = cfg.device_name || "";
      })
      .catch(function (e) { toast("Could not load settings: " + e.message, "err"); });
  }

  function saveSettings() {
    var errEl = $("settingsErr");
    errEl.textContent = "";
    var poll = parseInt($("setPoll").value, 10);
    if (isNaN(poll) || poll < 2) { errEl.textContent = "Poll interval must be at least 2"; return; }
    var device = $("setDevice").value.trim();
    if (!device) { errEl.textContent = "Device name is required"; return; }
    api("api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        known_senders_only: $("setKnown").checked,
        poll_interval: poll,
        device_name: device
      })
    }).then(function () { toast("Settings saved", "ok"); })
      .catch(function (e) { errEl.textContent = e.message; });
  }

  // ---- wire up ------------------------------------------------------ //

  $("addBtn").addEventListener("click", addRecipient);
  $("saveSettings").addEventListener("click", saveSettings);
  $("qrImg").addEventListener("error", function () { /* keep last good image */ });

  pollStatus();
  setInterval(pollStatus, STATUS_POLL_MS);
})();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def main() -> None:
    # Kick off Supervisor discovery in the background (best-effort).
    threading.Thread(target=discovery_worker, name="discovery", daemon=True).start()

    server = ThreadingHTTPServer(("0.0.0.0", MANAGER_PORT), Handler)
    server.daemon_threads = True
    log(f"listening on 0.0.0.0:{MANAGER_PORT} (signal api: {SIGNAL_API_URL})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
