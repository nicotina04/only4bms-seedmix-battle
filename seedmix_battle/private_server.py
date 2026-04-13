"""Seedmix Battle — embedded private match server (socket.io).

Implements the seed-distribution battle protocol described in
only4bms-server/documents/BACKEND_DESIGN.md §9. The server only
distributes deterministic seeds per turn and relays scores — clients
generate identical charts locally from those seeds.

Lifecycle:
    WAITING → BATTLE_READY → BATTLE_PLAYING → BATTLE_TRANSITION → ... → BATTLE_RESULT
"""

from __future__ import annotations

import random
import socket
import threading
import time
from wsgiref.simple_server import make_server, WSGIRequestHandler

import socketio

DEFAULT_PORT = 7215
TOTAL_TURNS = 10
DOUBLESCORE_THRESHOLD = 50_000

_THEMES = ("trill", "longnote", "chord", "speed_change")
_BPM_RANGE = (130, 180)

_server_thread: threading.Thread | None = None
_httpd = None
_stop_flag = threading.Event()


class _SilentHandler(WSGIRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        pass


# ---------------------------------------------------------------------------
# Shared battle state
# ---------------------------------------------------------------------------

class BattleState:
    """Server-side lobby/battle state for a single private match."""

    def __init__(self):
        self.lock = threading.Lock()
        self.clients: dict[str, dict] = {}  # sid -> {"id": int, "name": str}
        self.host_id: int | None = None
        self._next_pid = 1

        # Battle state
        self.phase = "WAITING"  # WAITING|BATTLE_READY|BATTLE_PLAYING|BATTLE_TRANSITION|BATTLE_RESULT
        self.turns: list[dict] = []  # distributed seed list (10 entries)
        self.match_settings: dict = {}
        self.current_turn: int = 0  # 1-based
        self.turn_ready: set[int] = set()
        self.turn_complete: dict[int, dict] = {}  # player_id -> score payload
        self.transition_ready: set[int] = set()
        self.standings: dict[int, int] = {}
        self.turn_history: list[dict] = []

    # -- lobby ---------------------------------------------------------------

    def add_player(self, sid: str) -> int:
        with self.lock:
            pid = self._next_pid
            self._next_pid += 1
            self.clients[sid] = {"id": pid, "name": f"Player {pid}"}
            if self.host_id is None:
                self.host_id = pid
            self.standings[pid] = 0
            return pid

    def remove_player(self, sid: str):
        with self.lock:
            info = self.clients.pop(sid, None)
            if not info:
                return
            pid = info["id"]
            self.turn_ready.discard(pid)
            self.transition_ready.discard(pid)
            self.turn_complete.pop(pid, None)
            if pid == self.host_id:
                self.host_id = next(iter(self.clients.values()), {}).get("id")

    def lobby_dict(self) -> dict:
        with self.lock:
            return {
                "type": "lobby_state",
                "players": [{"id": v["id"], "name": v["name"]} for v in self.clients.values()],
                "host_id": self.host_id,
                "phase": self.phase,
            }

    @property
    def player_count(self) -> int:
        return len(self.clients)

    # -- battle setup --------------------------------------------------------

    def generate_turns(self, seed_root: int) -> list[dict]:
        rng = random.Random(seed_root)
        player_ids = [v["id"] for v in self.clients.values()][:2]
        if len(player_ids) < 2:
            return []
        turns: list[dict] = []
        for i in range(TOTAL_TURNS):
            owner = player_ids[i % 2]
            turns.append({
                "turn": i + 1,
                "owner_id": owner,
                "seed": rng.randrange(1, 2**31),
                "bpm": rng.randint(*_BPM_RANGE),
                "theme": rng.choice(_THEMES),
            })
        return turns

    def reset_battle(self):
        self.current_turn = 0
        self.turn_ready.clear()
        self.turn_complete.clear()
        self.transition_ready.clear()
        self.standings = {v["id"]: 0 for v in self.clients.values()}
        self.turn_history = []
        self.turns = []


# ---------------------------------------------------------------------------
# Socket.IO server wiring
# ---------------------------------------------------------------------------

def _make_server_app() -> tuple[socketio.Server, BattleState]:
    sio = socketio.Server(async_mode="threading", cors_allowed_origins="*")
    state = BattleState()

    def _broadcast_lobby():
        sio.emit("lobby_state", state.lobby_dict())

    @sio.event
    def connect(sid, environ):
        pass

    @sio.event
    def disconnect(sid):
        state.remove_player(sid)
        _broadcast_lobby()

    @sio.on("join")
    def on_join(sid, data):
        pid = state.add_player(sid)
        sio.emit("join_success", {"player_id": pid, "host_id": state.host_id}, to=sid)
        _broadcast_lobby()

    # ---- battle start (host only) --------------------------------------
    @sio.on("start_battle")
    def on_start_battle(sid, data):
        with state.lock:
            info = state.clients.get(sid)
            if not info or info["id"] != state.host_id:
                return
            if state.player_count < 2:
                return
            state.match_settings = (data or {}).get("match_settings", {})
            state.reset_battle()
            state.turns = state.generate_turns(int(time.time() * 1000) & 0x7FFFFFFF)
            state.phase = "BATTLE_READY"
            state.current_turn = 1
        sio.emit("battle_start", {
            "total_turns": TOTAL_TURNS,
            "turns": state.turns,
            "match_settings": state.match_settings,
        })

    # ---- per-turn readiness -------------------------------------------
    @sio.on("turn_ready")
    def on_turn_ready(sid, data):
        info = state.clients.get(sid)
        if not info:
            return
        turn_no = (data or {}).get("turn")
        with state.lock:
            if turn_no != state.current_turn:
                return
            state.turn_ready.add(info["id"])
            if len(state.turn_ready) < state.player_count:
                return
            state.turn_ready.clear()
            state.phase = "BATTLE_PLAYING"
            turn_info = state.turns[turn_no - 1]
        sio.emit("turn_start", {
            **turn_info,
            "start_time_offset": 3000,
        })

    # ---- real-time score relay ----------------------------------------
    @sio.on("turn_score")
    def on_turn_score(sid, data):
        info = state.clients.get(sid)
        if not info:
            return
        payload = {
            "type": "opponent_score",
            "player_id": info["id"],
            "turn": (data or {}).get("turn"),
            "judgments": (data or {}).get("judgments", {}),
            "combo": (data or {}).get("combo", 0),
        }
        sio.emit("opponent_score", payload, skip_sid=sid)

    # ---- turn completion ----------------------------------------------
    @sio.on("turn_complete")
    def on_turn_complete(sid, data):
        info = state.clients.get(sid)
        if not info:
            return
        pid = info["id"]
        with state.lock:
            if (data or {}).get("turn") != state.current_turn:
                return
            state.turn_complete[pid] = data
            if len(state.turn_complete) < state.player_count:
                return

            scores = list(state.turn_complete.values())
            ranked = sorted(
                state.turn_complete.items(),
                key=lambda kv: kv[1].get("score", 0),
                reverse=True,
            )
            top_pid, top = ranked[0]
            second_score = ranked[1][1].get("score", 0) if len(ranked) > 1 else 0
            diff = top.get("score", 0) - second_score
            if diff == 0:
                winner_id = None
                points = 0
            else:
                winner_id = top_pid
                points = 2 if diff >= DOUBLESCORE_THRESHOLD else 1
                state.standings[winner_id] = state.standings.get(winner_id, 0) + points

            state.turn_history.append({
                "turn": state.current_turn,
                "winner_id": winner_id,
                "scores": [s.get("score", 0) for s in scores],
            })

            turn_result = {
                "turn": state.current_turn,
                "scores": [
                    {
                        "player_id": p,
                        "score": v.get("score", 0),
                        "accuracy": v.get("accuracy", 0),
                        "max_combo": v.get("max_combo", 0),
                        "judgments": v.get("judgments", {}),
                    }
                    for p, v in state.turn_complete.items()
                ],
                "turn_winner_id": winner_id,
                "turn_points": points,
                "standings": dict(state.standings),
            }

            state.turn_complete.clear()
            is_last = state.current_turn >= TOTAL_TURNS

            if is_last:
                state.phase = "BATTLE_RESULT"
                final_winner = max(state.standings.items(), key=lambda kv: kv[1])[0]
                battle_result = {
                    "winner_id": final_winner,
                    "final_standings": dict(state.standings),
                    "turn_history": list(state.turn_history),
                }
            else:
                state.phase = "BATTLE_TRANSITION"
                cur = state.turns[state.current_turn - 1]
                nxt = state.turns[state.current_turn]
                transition = {
                    "from_bpm": cur["bpm"],
                    "to_bpm": nxt["bpm"],
                    "duration_ms": 5000,
                    "next_turn": nxt["turn"],
                    "next_owner_id": nxt["owner_id"],
                    "next_theme": nxt["theme"],
                }
                state.current_turn += 1

        sio.emit("turn_result", turn_result)
        if is_last:
            sio.emit("battle_result", battle_result)
        else:
            sio.emit("transition_start", transition)

    # ---- transition completion ----------------------------------------
    @sio.on("transition_ready")
    def on_transition_ready(sid, data):
        info = state.clients.get(sid)
        if not info:
            return
        with state.lock:
            state.transition_ready.add(info["id"])
            if len(state.transition_ready) < state.player_count:
                return
            state.transition_ready.clear()
            state.phase = "BATTLE_READY"
            turn_info = state.turns[state.current_turn - 1]
        # Clients then emit `turn_ready` and the flow resumes from there.
        sio.emit("turn_prepare", turn_info)

    return sio, state


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def start_server(port: int = DEFAULT_PORT) -> tuple[str, int]:
    """Start the embedded socket.io server in a background thread."""
    global _server_thread, _httpd

    stop_server()

    sio, _state = _make_server_app()
    app = socketio.WSGIApp(sio)

    _httpd = make_server("0.0.0.0", port, app, handler_class=_SilentHandler)
    _stop_flag.clear()

    def _serve():
        while not _stop_flag.is_set():
            _httpd.handle_request()

    _httpd.timeout = 0.5
    _server_thread = threading.Thread(target=_serve, daemon=True)
    _server_thread.start()

    return _get_local_ip(), port


def stop_server():
    global _server_thread, _httpd
    _stop_flag.set()
    if _httpd is not None:
        try:
            _httpd.server_close()
        except OSError:
            pass
        _httpd = None
    _server_thread = None


def is_running() -> bool:
    return _httpd is not None


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"
