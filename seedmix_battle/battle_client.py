"""Client-side battle session — wraps socketio for the §9 protocol.

Independent from the host `NetworkManager` (which speaks the older
song-selection protocol). A single BattleClient instance handles join,
seed distribution, per-turn handshake, score relay and result delivery.
"""

from __future__ import annotations

import threading
import time

import socketio


class BattleClient:
    def __init__(self):
        self.sio = socketio.Client()
        self.server_url: str | None = None
        self.is_connected = False

        # Lobby
        self.player_id: int | None = None
        self.host_id: int | None = None
        self.lobby_state: dict = {}

        # Battle
        self.turns: list[dict] = []            # full seed list from battle_start
        self.match_settings: dict = {}
        self.current_turn_info: dict | None = None  # from turn_start
        self.opponent_state: dict | None = None     # live combo/judgments
        self.turn_result: dict | None = None
        self.transition_info: dict | None = None
        self.battle_result: dict | None = None
        self.phase: str = "LOBBY"  # LOBBY|WAITING_START|PLAYING|TRANSITION|RESULT

        self._register()

    # -- events --------------------------------------------------------------

    def _register(self):
        sio = self.sio

        @sio.event
        def connect():
            self.is_connected = True

        @sio.event
        def disconnect():
            self.is_connected = False
            self.player_id = None
            self.host_id = None
            self.lobby_state = {}
            self.turns = []
            self.opponent_state = None
            self.phase = "LOBBY"

        @sio.on("join_success")
        def on_join(data):
            self.player_id = data.get("player_id")
            self.host_id = data.get("host_id")

        @sio.on("lobby_state")
        def on_lobby(data):
            self.lobby_state = data
            self.host_id = data.get("host_id")

        @sio.on("battle_start")
        def on_battle_start(data):
            self.turns = data.get("turns", [])
            self.match_settings = data.get("match_settings", {})
            self.phase = "WAITING_START"
            self.current_turn_info = self.turns[0] if self.turns else None

        @sio.on("turn_prepare")
        def on_turn_prepare(data):
            self.current_turn_info = data
            self.phase = "WAITING_START"

        @sio.on("turn_start")
        def on_turn_start(data):
            self.current_turn_info = data
            self.opponent_state = None
            self.phase = "PLAYING"

        @sio.on("opponent_score")
        def on_opponent_score(data):
            self.opponent_state = data

        @sio.on("turn_result")
        def on_turn_result(data):
            self.turn_result = data

        @sio.on("transition_start")
        def on_transition_start(data):
            self.transition_info = data
            self.phase = "TRANSITION"

        @sio.on("battle_result")
        def on_battle_result(data):
            self.battle_result = data
            self.phase = "RESULT"

    # -- connection ----------------------------------------------------------

    def connect(self, url: str, timeout: float = 3.0) -> bool:
        if not url.startswith("http"):
            url = "http://" + url
        self.server_url = url
        try:
            self.sio.connect(url, wait_timeout=timeout)
            return True
        except Exception as e:
            print(f"[BattleClient] connect failed: {e}")
            return False

    def disconnect(self):
        if self.is_connected:
            try:
                self.sio.disconnect()
            except Exception:
                pass
        self.is_connected = False

    def join_lobby(self, name: str = "Player"):
        if self.is_connected:
            self.sio.emit("join", {"name": name})

    # -- battle commands -----------------------------------------------------

    def start_battle(self, match_settings: dict | None = None):
        if self.is_connected and self.player_id == self.host_id:
            self.sio.emit("start_battle", {"match_settings": match_settings or {}})

    def send_turn_ready(self, turn: int):
        self.sio.emit("turn_ready", {"turn": turn})

    def send_turn_score(self, turn: int, judgments: dict, combo: int):
        self.sio.emit("turn_score", {
            "turn": turn,
            "judgments": judgments,
            "combo": combo,
        })

    def send_turn_complete(self, turn: int, score: int, accuracy: float,
                           max_combo: int, judgments: dict):
        self.sio.emit("turn_complete", {
            "turn": turn,
            "score": score,
            "accuracy": accuracy,
            "max_combo": max_combo,
            "judgments": judgments,
        })

    def send_transition_ready(self, next_turn: int):
        self.sio.emit("transition_ready", {"turn": next_turn})

    # -- waits ---------------------------------------------------------------

    def wait_for(self, predicate, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False
