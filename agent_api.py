"""
LGHUB Agent Websocket API - live config editing without stopping LGHUB.

lghub_agent.exe serves a JSON-protobuf websocket API on 127.0.0.1:9010 (the
same one the G HUB UI uses). Messages look like:

    {"msgId": "<uuid>", "verb": "GET|SET|POST|DELETE", "path": "/applications",
     "payload": {"@type": "type.googleapis.com/logi.protocol...", ...}}

Responses echo the msgId. Unsolicited broadcasts arrive on the same socket and
must be skipped while waiting for a reply.

Key facts discovered by probing (2026-07-16):
- GET /applications returns the full app list, including custom entries.
- SET /application accepts a partial Application payload and merges it by
  applicationId. `userPaths` is writable; `applicationPath` is READ-ONLY and
  silently ignored.
- `userPaths` is G HUB's official "link additional executables" feature: the
  agent matches running processes against applicationPath AND every userPaths
  entry (verified live: a linked exe was associated within ~3s of launch).
- Changes made through SET are persisted to settings.db by the agent itself
  within ~1 second, while LGHUB keeps running.

So to keep a profile working across Roblox updates we only need to keep the
newest RobloxPlayerBeta.exe present in userPaths. No LGHUB restart required.

Pure stdlib (socket-level websocket client) - no pip dependencies.
"""

import base64
import json
import os
import socket
import struct
import time
import uuid

AGENT_HOST = "127.0.0.1"
AGENT_PORT = 9010
APPLICATION_TYPE = "type.googleapis.com/logi.protocol.applications.Application"


class AgentError(Exception):
    """Base error for agent API operations."""


class AgentUnavailable(AgentError):
    """The lghub_agent websocket is not reachable (LGHUB not running?)."""


# ---------------------------------------------------------------------------
# Minimal websocket client (stdlib only)
# ---------------------------------------------------------------------------

class AgentConnection:
    """Websocket connection to the LGHUB agent with msgId-correlated requests."""

    def __init__(self, host: str = AGENT_HOST, port: int = AGENT_PORT, timeout: float = 6.0):
        self.timeout = timeout
        try:
            self._sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as e:
            raise AgentUnavailable(f"Cannot reach LGHUB agent at {host}:{port}: {e}") from e
        self._sock.settimeout(timeout)
        self._buf = b""
        self._handshake(host, port)

    def _handshake(self, host: str, port: int) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET / HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"Sec-WebSocket-Protocol: json\r\n"
            f"Origin: file://\r\n"
            f"\r\n"
        )
        self._sock.sendall(request.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise AgentUnavailable("Websocket handshake failed (connection closed).")
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        status_line = head.split(b"\r\n", 1)[0].decode(errors="replace")
        if " 101" not in status_line:
            raise AgentUnavailable(f"Websocket handshake rejected: {status_line}")
        self._buf = rest

    # --- frame level -------------------------------------------------------

    def _recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise AgentError("Connection closed by agent.")
            self._buf += chunk
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    def _read_frame(self) -> tuple[bool, int, bytes]:
        b1, b2 = self._recv_exact(2)
        fin = bool(b1 & 0x80)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            (length,) = struct.unpack(">H", self._recv_exact(2))
        elif length == 127:
            (length,) = struct.unpack(">Q", self._recv_exact(8))
        mask = self._recv_exact(4) if masked else None
        data = self._recv_exact(length)
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        return fin, opcode, data

    def _send_frame(self, opcode: int, data: bytes) -> None:
        mask = os.urandom(4)
        header = bytes([0x80 | opcode])
        n = len(data)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self._sock.sendall(header + mask + masked)

    # --- message level -----------------------------------------------------

    def send_text(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def recv_text(self) -> str:
        parts: list[bytes] = []
        have_start = False
        while True:
            fin, opcode, data = self._read_frame()
            if opcode == 0x9:            # ping -> pong
                self._send_frame(0xA, data)
                continue
            if opcode == 0xA:            # pong
                continue
            if opcode == 0x8:            # close
                raise AgentError("Agent closed the websocket.")
            if opcode in (0x1, 0x2):     # text/binary start
                parts = [data]
                have_start = True
            elif opcode == 0x0 and have_start:   # continuation
                parts.append(data)
            if fin and have_start:
                return b"".join(parts).decode("utf-8")

    def request(self, verb: str, path: str, payload: dict | None = None,
                timeout: float | None = None) -> dict:
        """Send a request and wait for the msgId-matched response,
        skipping unsolicited broadcasts."""
        msg_id = str(uuid.uuid4())
        msg = {"msgId": msg_id, "verb": verb, "path": path}
        if payload is not None:
            msg["payload"] = payload
        self.send_text(json.dumps(msg))
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            try:
                resp = json.loads(self.recv_text())
            except socket.timeout:
                continue
            if resp.get("msgId") == msg_id:
                return resp
        raise AgentError(f"No response to {verb} {path} within timeout.")

    def close(self) -> None:
        try:
            self._send_frame(0x8, b"")
        except Exception:
            pass
        try:
            self._sock.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# ---------------------------------------------------------------------------
# High-level helpers
# ---------------------------------------------------------------------------

def agent_available(timeout: float = 2.0) -> bool:
    """True if the LGHUB agent websocket answers on port 9010."""
    try:
        with socket.create_connection((AGENT_HOST, AGENT_PORT), timeout=timeout):
            return True
    except OSError:
        return False


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def get_applications(conn: AgentConnection) -> list[dict]:
    resp = conn.request("GET", "/applications")
    result = resp.get("result", {})
    if result.get("code") != "SUCCESS":
        raise AgentError(f"GET /applications failed: {result}")
    return resp.get("payload", {}).get("applications", [])


def find_roblox_app(conn: AgentConnection) -> dict | None:
    for app in get_applications(conn):
        hay = (app.get("name", "") + app.get("applicationPath", "")).lower()
        if "roblox" in hay:
            return app
    return None


def compute_desired_user_paths(app: dict, new_path: str) -> list[str]:
    """Replace the linked-executable list outright: only the newest exe."""
    return [str(new_path)]


def set_user_paths(conn: AgentConnection, application_id: str, user_paths: list[str]) -> None:
    """Partial-update the app entry; the agent merges by applicationId and
    persists to settings.db itself (~1s). Same call the G HUB UI makes for
    'link additional executable'."""
    payload = {
        "@type": APPLICATION_TYPE,
        "applicationId": application_id,
        "userPaths": user_paths,
    }
    resp = conn.request("SET", "/application", payload)
    result = resp.get("result", {})
    if result.get("code") != "SUCCESS":
        raise AgentError(f"SET /application failed: {result}")


def is_path_effective(app: dict, exe_path: str) -> bool:
    """True if the given exe is already covered by applicationPath or userPaths."""
    if _same_path(app.get("applicationPath", ""), exe_path):
        return True
    return any(_same_path(p, exe_path) for p in app.get("userPaths", []))


def live_update_roblox(new_path: str) -> dict:
    """
    Ensure the newest Roblox exe is linked to the LGHUB Roblox entry, live.

    Returns a summary dict:
      {"changed": bool, "applicationId": ..., "applicationPath": ...,
       "userPaths": [...], "note": ...}
    Raises AgentUnavailable if the agent isn't running, AgentError on API
    failure - callers should fall back to the offline (stop/write/start) path.
    """
    new_path = str(new_path).replace("/", "\\")
    with AgentConnection() as conn:
        app = find_roblox_app(conn)
        if app is None:
            raise AgentError("No Roblox entry found in LGHUB (add it once via G HUB UI).")

        desired = compute_desired_user_paths(app, new_path)
        current = app.get("userPaths", [])
        already = (
            len(desired) == len(current)
            and all(_same_path(a, b) for a, b in zip(desired, current))
        )
        if already and is_path_effective(app, new_path):
            return {
                "changed": False,
                "applicationId": app["applicationId"],
                "applicationPath": app.get("applicationPath", ""),
                "userPaths": current,
                "note": "Already up to date.",
            }

        set_user_paths(conn, app["applicationId"], desired)

        # Read back to confirm the agent accepted the change
        updated = find_roblox_app(conn)
        if updated is None or not is_path_effective(updated, new_path):
            raise AgentError("Agent did not accept the userPaths update.")

        note = ""
        if not _same_path(updated.get("applicationPath", ""), new_path):
            note = (
                "applicationPath is read-only via the live API and may point at an "
                "old version folder; detection works through the linked userPaths."
            )
        return {
            "changed": True,
            "applicationId": updated["applicationId"],
            "applicationPath": updated.get("applicationPath", ""),
            "userPaths": updated.get("userPaths", []),
            "note": note,
        }
