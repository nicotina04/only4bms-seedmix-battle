"""
Seedmix Battle — GameExtension for battle mode.

Dual-lane layout with live opponent score sync.
Works with both private and official match modes.
"""

import time
import pygame
from pygame._sdl2.video import Texture

from only4bms.game.game_extension import GameExtension
from only4bms.game.constants import NUM_LANES, JUDGMENT_ORDER, JUDGMENT_DEFS
from .i18n import t as _t

_AI_DT = 1.0 / 120.0  # opponent engine update rate


class SeedmixBattleExtension(GameExtension):
    """Drives opponent engine from network state and renders dual-player HUD."""

    def __init__(self, net_manager=None):
        self._net = net_manager
        self._update_timer = 0.0

    # -- Lifecycle ----------------------------------------------------------

    def attach(self, game) -> None:
        super().attach(game)
        import only4bms.i18n as _i18n
        w, h = game.window.size
        sy = h / 600.0
        self._font_lg = _i18n.font("hud_bold", sy, bold=True)
        self._font_sm = _i18n.font("menu_small", sy)
        self._sy = sy
        self._sx_ratio = w / 800.0
        self._width = w
        self._height = h

    def on_attach_init(self, game) -> None:
        from only4bms.game.engine import GameEngine

        w, h = game.width, game.height

        # Dual lane layout: P1 left quarter, P2 right quarter
        p1_start = w // 4 - game.lane_total_w // 2
        game.p1_lane_x = [p1_start + i * game.lane_w for i in range(NUM_LANES)]
        p2_start = (w * 3) // 4 - game.lane_total_w // 2
        game.p2_lane_x = [p2_start + i * game.lane_w for i in range(NUM_LANES)]
        game.lane_x = game.p1_lane_x

        # Opponent engine (mirrors P1 chart)
        game.ai_notes = [n.copy() for n in game.engine.notes]
        game.ai_engine = GameEngine(
            game.ai_notes, [], [], game.hw_mult,
            lambda s: None,
            lambda *a: None,
            game.engine.max_time,
            game.visual_timing_map,
            game.engine.last_note_time,
            lambda *a: None,
        )
        game.ai_lane_pressed = [False] * NUM_LANES

        # Opponent judgment state
        game.ai_judgments = {k: 0 for k in JUDGMENT_ORDER}
        game.ai_combo = 0
        game.ai_max_combo = 0
        game.ai_judgment_text = ""
        game.ai_judgment_key = ""
        game.ai_judgment_timer = 0
        game.ai_combo_timer = 0
        game.ai_judgment_color = (255, 255, 255)
        game.ai_hit_history = []

    # -- Per-note: send score -----------------------------------------------

    def on_judgment(self, key: str, lane: int, t_ms: float) -> None:
        if not self._net:
            return
        game = self._game
        self._net.send_score(game.judgments, game.combo)

    # -- Per-frame: sync opponent -------------------------------------------

    def on_tick(self, sim_time_ms: float) -> None:
        game = self._game
        t_now = time.perf_counter()
        if t_now - self._update_timer < _AI_DT:
            return
        self._update_timer = t_now

        game.ai_engine.update(sim_time_ms)
        if not self._net:
            return
        opp = self._net.opponent_state
        if opp:
            game.ai_judgments = opp.get("judgments", game.ai_judgments)
            new_combo = opp.get("combo", game.ai_combo)
            if new_combo > game.ai_combo:
                game.ai_judgment_text = "HIT"
                game.ai_judgment_color = (0, 255, 255)
                game.ai_judgment_timer = sim_time_ms
            elif new_combo == 0 and game.ai_combo > 0:
                game.ai_judgment_text = "MISS"
                game.ai_judgment_color = (255, 0, 0)
                game.ai_judgment_timer = sim_time_ms
            game.ai_combo = new_combo
            game.ai_max_combo = max(game.ai_max_combo, game.ai_combo)
            self._net.opponent_state = None

    # -- Rendering ----------------------------------------------------------

    def get_all_lanes(self, game) -> list:
        return game.p1_lane_x + game.p2_lane_x

    def get_p1_lane_x(self, game) -> list:
        return game.p1_lane_x

    def get_p1_draw_extras(self, game) -> dict:
        return {
            "ai_judgments": game.ai_judgments,
            "ai_hit_history": game.ai_hit_history,
        }

    def draw_mid_hud(self, renderer, window, t, game,
                     p1_ratio, max_ex, gy, gw, gh, ga) -> None:
        gr = game.game_renderer

        # P1 gauge — right of P1 lanes
        gx_p1 = game.p1_lane_x[-1] + game.lane_w + gr._sx(5)
        gr.draw_vertical_gauge(gx_p1, gy, gw, gh, p1_ratio, (0, 255, 255), ga)

        # Opponent note field
        ai_state = game._get_draw_state("ai", t)
        gr.draw_playing(t, ai_state)
        gr.draw_score_bar(game.judgments, game.ai_judgments)

        # Opponent gauge — left of P2 lanes
        ai_ex = game.ai_judgments["PERFECT"] * 2 + game.ai_judgments["GREAT"]
        ai_ratio = min(1.0, ai_ex / max_ex) if max_ex > 0 else 0
        gx_ai = game.p2_lane_x[0] - gw - gr._sx(5)
        gr.draw_vertical_gauge(gx_ai, gy, gw, gh, ai_ratio, (255, 80, 80), ga)

    def draw_overlay(self, renderer, window, game_state, phase) -> None:
        if phase != "result":
            return

        stats = game_state

        def _s(v):
            return max(1, int(v * self._sy))

        def _sx(v):
            return max(1, int(v * self._sx_ratio))

        def calc_score(judgs):
            if not judgs:
                return 0
            return judgs.get("PERFECT", 0) * 1000 + judgs.get("GREAT", 0) * 500 + judgs.get("GOOD", 0) * 200

        score_p1 = calc_score(stats["judgments"])
        score_p2 = calc_score(stats.get("ai_judgments") or {})

        if score_p1 > score_p2:
            win_txt, win_color = "WIN", (0, 255, 255)
        elif score_p1 < score_p2:
            win_txt, win_color = "LOSE", (255, 50, 50)
        else:
            win_txt, win_color = "DRAW", (255, 255, 0)

        renderer.draw_color = (10, 10, 20, 255)
        renderer.fill_rect((0, 0, self._width, _s(80)))

        win_surf = self._font_lg.render(win_txt, True, win_color)
        win_tex = Texture.from_surface(renderer, win_surf)
        win_tex.alpha = 255
        renderer.blit(win_tex, pygame.Rect(
            self._width // 2 - win_tex.width // 2, _s(10),
            win_tex.width, win_tex.height))

    # -- Stats --------------------------------------------------------------

    def get_extra_stats(self) -> dict:
        game = self._game
        max_ex = max(1, game.total_judgments * 2)
        p1_ex = game.judgments.get("PERFECT", 0) * 2 + game.judgments.get("GREAT", 0)
        ai_ex = game.ai_judgments.get("PERFECT", 0) * 2 + game.ai_judgments.get("GREAT", 0)
        return {
            "ai_accuracy": (ai_ex / max_ex) * 100.0,
            "must_win": p1_ex > ai_ex,
            "failed": False,
            "ai_judgments": dict(game.ai_judgments),
            "ai_max_combo": game.ai_max_combo,
        }
