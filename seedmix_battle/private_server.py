"""
Seedmix Battle — Embedded private match server.

Lightweight WebSocket server that runs in a background thread.
Handles a 2-player lobby with song selection and score relay.
"""

import json
import threading
import socket
from http.server import HTTPServer, BaseHTTPRequestHandler

_server_instance = None
_server_thread = None

DEFAULT_PORT = 7215


class PrivateMatchState:
    """Shared lobby state for the embedded server."""

    def __init__(self):
        self.lock = threading.Lock()
        self.clients: dict[socket.socket, dict] = {}
        self.host_id: int | None = None
        self.selected_song: str | None = None
        self.selected_bms: str | None = None
        self.match_settings: dict = {}
        self.ready_players: set[int] = set()
        self._next_id = 1

    def add_player(self, ws) -> int:
        with self.lock:
            pid = self._next_id
            self._next_id += 1
            self.clients[ws] = {"id": pid, "name": f"Player {pid}"}
            if self.host_id is None:
                self.host_id = pid
            return pid

    def remove_player(self, ws):
        with self.lock:
            info = self.clients.pop(ws, None)
            if info and info["id"] == self.host_id:
                # Transfer host
                if self.clients:
                    self.host_id = next(iter(self.clients.values()))["id"]
                else:
                    self.host_id = None
            self.ready_players.discard(info["id"] if info else -1)

    def get_lobby_dict(self) -> dict:
        with self.lock:
            return {
                "type": "lobby_state",
                "players": [{"id": v["id"], "name": v["name"]} for v in self.clients.values()],
                "host_id": self.host_id,
                "selected_song_id": self.selected_song,
                "selected_bms_file": self.selected_bms,
                "match_settings": self.match_settings,
                "ready_players": list(self.ready_players),
            }

    @property
    def player_count(self) -> int:
        return len(self.clients)


# ---------------------------------------------------------------------------
# Simple WebSocket helpers (minimal RFC 6455 framing)
# ---------------------------------------------------------------------------

def _ws_handshake(conn: socket.socket, request_line: bytes) -> bool:
    """Perform the WebSocket upgrade handshake."""
    import hashlib
    import base64

    data = request_line
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(4096)
        if not chunk:
            return False
        data += chunk

    headers = {}
    for line in data.decode(errors="replace").split("\r\n")[1:]:
        if ": " in line:
            k, v = line.split(": ", 1)
            headers[k.lower()] = v

    key = headers.get("sec-websocket-key", "")
    if not key:
        return False

    GUID = "258EAFA5-E914-47DA-95CA-5AB5-11D85B9A"
    accept = base64.b64encode(
        hashlib.sha1((key + GUID).encode()).digest()
    ).decode()

    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    )
    conn.sendall(response.encode())
    return True


def _ws_recv(conn: socket.socket) -> str | None:
    """Read one WebSocket text frame. Returns None on close/error."""
    try:
        header = conn.recv(2)
        if len(header) < 2:
            return None

        opcode = header[0] & 0x0F
        if opcode == 0x8:  # close
            return None

        masked = (header[1] & 0x80) != 0
        length = header[1] & 0x7F

        if length == 126:
            raw = conn.recv(2)
            length = int.from_bytes(raw, "big")
        elif length == 127:
            raw = conn.recv(8)
            length = int.from_bytes(raw, "big")

        mask = conn.recv(4) if masked else b"\x00" * 4
        payload = bytearray()
        while len(payload) < length:
            chunk = conn.recv(length - len(payload))
            if not chunk:
                return None
            payload.extend(chunk)

        if masked:
            payload = bytearray(b ^ mask[i % 4] for i, b in enumerate(payload))

        return payload.decode("utf-8", errors="replace")
    except (ConnectionError, OSError):
        return None


def _ws_send(conn: socket.socket, text: str):
    """Send a WebSocket text frame."""
    data = text.encode("utf-8")
    frame = bytearray()
    frame.append(0x81)  # FIN + text opcode

    if len(data) < 126:
        frame.append(len(data))
    elif len(data) < 65536:
        frame.append(126)
        frame.extend(len(data).to_bytes(2, "big"))
    else:
        frame.append(127)
        frame.extend(len(data).to_bytes(8, "big"))

    frame.extend(data)
    try:
        conn.sendall(frame)
    except (ConnectionError, OSError):
        pass


def _broadcast(state: PrivateMatchState, msg: str, exclude=None):
    with state.lock:
        for ws in list(state.clients):
            if ws is not exclude:
                _ws_send(ws, msg)


def _broadcast_lobby(state: PrivateMatchState):
    _broadcast(state, json.dumps(state.get_lobby_dict()))


# ---------------------------------------------------------------------------
# Client handler
# ---------------------------------------------------------------------------

def _handle_client(conn: socket.socket, state: PrivateMatchState):
    pid = state.add_player(conn)
    _ws_send(conn, json.dumps({
        "type": "join_success",
        "player_id": pid,
        "host_id": state.host_id,
    }))
    _broadcast_lobby(state)

    try:
        while True:
            raw = _ws_recv(conn)
            if raw is None:
                break

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "select_song":
                with state.lock:
                    if state.clients.get(conn, {}).get("id") != state.host_id:
                        continue
                    state.selected_song = msg.get("song_id")
                    state.selected_bms = msg.get("bms_file")
                    state.match_settings = msg.get("match_settings", {})
                    state.ready_players.clear()
                _broadcast_lobby(state)

            elif msg_type == "ready":
                with state.lock:
                    info = state.clients.get(conn)
                    if info:
                        state.ready_players.add(info["id"])
                    all_ready = len(state.ready_players) >= len(state.clients) >= 2
                _broadcast_lobby(state)

                if all_ready:
                    import time
                    start_msg = json.dumps({
                        "type": "start_game",
                        "start_time_offset": 3000,
                        "song_id": state.selected_song,
                        "bms_file": state.selected_bms,
                        "match_settings": state.match_settings,
                    })
                    _broadcast(state, start_msg)
                    with state.lock:
                        state.ready_players.clear()

            elif msg_type == "sync_score":
                with state.lock:
                    info = state.clients.get(conn)
                relay = json.dumps({
                    "type": "opponent_score",
                    "player_id": info["id"] if info else 0,
                    "judgments": msg.get("judgments", {}),
                    "combo": msg.get("combo", 0),
                    "in_game_ready": msg.get("in_game_ready", False),
                })
                _broadcast(state, relay, exclude=conn)

    finally:
        state.remove_player(conn)
        _broadcast_lobby(state)
        conn.close()


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def start_server(port: int = DEFAULT_PORT) -> tuple[str, int]:
    """Start the embedded WebSocket server. Returns (host_ip, port)."""
    global _server_instance, _server_thread

    stop_server()  # clean up any previous instance

    state = PrivateMatchState()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(4)
    srv.settimeout(1.0)

    _server_instance = srv

    def accept_loop():
        while _server_instance is srv:
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            # Read first bytes to detect WebSocket upgrade
            try:
                first = conn.recv(4096, socket.MSG_PEEK)
                if b"Upgrade: websocket" in first or b"upgrade: websocket" in first:
                    full = conn.recv(4096)
                    if _ws_handshake(conn, full):
                        threading.Thread(
                            target=_handle_client,
                            args=(conn, state),
                            daemon=True,
                        ).start()
                    else:
                        conn.close()
                else:
                    conn.close()
            except (ConnectionError, OSError):
                conn.close()

    _server_thread = threading.Thread(target=accept_loop, daemon=True)
    _server_thread.start()

    # Detect local IP
    host_ip = _get_local_ip()
    return host_ip, port


def stop_server():
    """Stop the embedded server if running."""
    global _server_instance, _server_thread
    if _server_instance:
        try:
            _server_instance.close()
        except OSError:
            pass
        _server_instance = None
    _server_thread = None


def is_running() -> bool:
    return _server_instance is not None


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"
