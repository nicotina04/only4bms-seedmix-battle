"""Seedmix Battle — GameExtension for continuous-play battle.

The full 10-turn battle runs inside one RhythmGame session. This extension
tracks turn boundaries by sim time, computes per-turn judgment deltas,
streams per-turn scores over the BattleClient, and overlays a live turn
HUD + per-turn result panel + bridge preview on top of the playfield so
the player never leaves the lanes view.
"""

from __future__ import annotations

import time

import pygame
from pygame._sdl2.video import Texture

from only4bms.game.game_extension import GameExtension
from only4bms.game.constants import JUDGMENT_ORDER

_AI_DT = 1.0 / 120.0
_RESULT_PANEL_MS = 2200  # how long the per-turn result panel lingers after play_end


class SeedmixBattleExtension(GameExtension):
    """Continuous-play extension. Drives turn-phase state off sim time."""

    def __init__(self, battle_client=None, turn_boundaries: list | None = None,
                 total_turns: int = 10):
        self._net = battle_client
        self._boundaries = list(turn_boundaries or [])
        self._total = total_turns

        # Per-turn deltas (cumulative counters are snapshotted at turn start).
        self._turn_idx = 0                   # 0-based index into _boundaries
        self._turn_baseline = {k: 0 for k in JUDGMENT_ORDER}
        self._opponent_baseline = {k: 0 for k in JUDGMENT_ORDER}
        self._turn_complete_fired: set[int] = set()
        self._last_result_payload: dict | None = None  # {"my": {...}, "opp": {...}}
        self._standings: dict[int, int] = {}
        self._last_sim_time_ms: float = 0.0

        self._update_timer = 0.0

    # -- Lifecycle -----------------------------------------------------------

    def attach(self, game) -> None:
        super().attach(game)
        import only4bms.i18n as _i18n
        w, h = game.window.size
        sy = h / 600.0
        self._font_lg = _i18n.font("hud_bold", sy, bold=True)
        self._font_md = _i18n.font("ui_body", sy)
        self._font_sm = _i18n.font("menu_small", sy)
        self._sy = sy
        self._sx_ratio = w / 800.0
        self._width = w
        self._height = h

    def on_attach_init(self, game) -> None:
        # Single centered lane playfield — we only render the local player's
        # own field (like SDVX Megamix Battle). Opponent performance arrives
        # over the network and is shown via the VS card at the top.
        game.ai_judgments = {k: 0 for k in JUDGMENT_ORDER}
        game.ai_combo = 0
        game.ai_max_combo = 0

    # -- Score streaming -----------------------------------------------------

    def on_judgment(self, key: str, lane: int, t_ms: float) -> None:
        if not self._net:
            return
        game = self._game
        current = self._current_boundary()
        if current is None:
            return
        delta = _judgment_delta(game.judgments, self._turn_baseline)
        combo = getattr(game, "combo", 0) or 0
        self._net.send_turn_score(current.turn, delta, combo)

    def on_tick(self, sim_time_ms: float) -> None:
        self._last_sim_time_ms = sim_time_ms

        # Advance turn phase based on sim time.
        self._update_turn_phase(sim_time_ms)

        # Throttled opponent state sync.
        t_now = time.perf_counter()
        if t_now - self._update_timer < _AI_DT:
            return
        self._update_timer = t_now

        if self._net is None:
            return

        # Read external standings if the client tracks them.
        ext_standings = getattr(self._net, "standings", None)
        if isinstance(ext_standings, dict) and ext_standings:
            self._standings = dict(ext_standings)

        opp = getattr(self._net, "opponent_state", None)
        if not opp:
            return

        game = self._game
        game.ai_judgments = opp.get("judgments", game.ai_judgments)
        new_combo = opp.get("combo", game.ai_combo)
        game.ai_combo = new_combo
        game.ai_max_combo = max(game.ai_max_combo, game.ai_combo)
        self._net.opponent_state = None

    # -- Turn phase state machine -------------------------------------------

    def _current_boundary(self):
        if not self._boundaries:
            return None
        i = min(self._turn_idx, len(self._boundaries) - 1)
        return self._boundaries[i]

    def _update_turn_phase(self, sim_time_ms: float) -> None:
        if not self._boundaries:
            return
        game = self._game

        cur = self._boundaries[self._turn_idx]

        # Crossed the play_end boundary? Snapshot turn stats and fire
        # send_turn_complete. The result panel will render over the bridge.
        if sim_time_ms >= cur.play_end_ms and cur.turn not in self._turn_complete_fired:
            my_delta = _judgment_delta(game.judgments, self._turn_baseline)
            opp_delta = _judgment_delta(game.ai_judgments, self._opponent_baseline)
            self._last_result_payload = {
                "turn": cur.turn,
                "my": my_delta,
                "my_score": _calc_score(my_delta),
                "my_acc": _calc_accuracy(my_delta),
                "opp": opp_delta,
                "opp_score": _calc_score(opp_delta),
                "opp_acc": _calc_accuracy(opp_delta),
                "shown_at_ms": sim_time_ms,
                "theme": cur.theme,
            }
            if self._net is not None:
                try:
                    self._net.send_turn_complete(
                        turn=cur.turn,
                        score=self._last_result_payload["my_score"],
                        accuracy=self._last_result_payload["my_acc"],
                        max_combo=getattr(game, "max_combo", 0) or 0,
                        judgments=dict(my_delta),
                    )
                except Exception:
                    pass
            else:
                # Solo: self-keep a pseudo standing so HUD has something.
                my_id = 0
                self._standings[my_id] = self._standings.get(my_id, 0) + 1
            self._turn_complete_fired.add(cur.turn)

        # Crossed bridge_end_ms? Advance to next turn, reset baselines.
        if sim_time_ms >= cur.bridge_end_ms and self._turn_idx + 1 < len(self._boundaries):
            self._turn_idx += 1
            self._turn_baseline = dict(game.judgments)
            self._opponent_baseline = dict(game.ai_judgments)
            self._last_result_payload = None  # cleared when next turn starts

    # Rendering: inherit default GameExtension behavior (single-player
    # centered gauge, default lane_x, no opponent note field). The VS
    # card in draw_overlay handles opponent presentation instead.

    # -- Overlays (live HUD + per-turn result panel) ------------------------

    def _draw_turn_hud(self, renderer, sim_time_ms: float) -> None:
        def _s(v):
            return max(1, int(v * self._sy))

        strip_h = _s(34)
        renderer.draw_color = (8, 10, 22, 220)
        renderer.fill_rect((0, 0, self._width, strip_h))
        renderer.draw_color = (0, 200, 255, 140)
        renderer.fill_rect((0, strip_h - _s(2), self._width, _s(2)))

        cur = self._current_boundary()
        if cur is None:
            return

        in_bridge = sim_time_ms >= cur.play_end_ms
        next_b = (
            self._boundaries[self._turn_idx + 1]
            if in_bridge and self._turn_idx + 1 < len(self._boundaries)
            else None
        )

        if in_bridge and next_b is not None:
            label = f"TURN {cur.turn}/{self._total}  →  TURN {next_b.turn}  ·  {next_b.theme.upper()}"
        else:
            label = f"TURN {cur.turn}/{self._total}  ·  {cur.theme.upper()}"

        lbl_surf = self._font_sm.render(label, True, (180, 230, 255))
        lbl_tex = Texture.from_surface(renderer, lbl_surf)
        renderer.blit(
            lbl_tex,
            pygame.Rect(_s(16), (strip_h - lbl_tex.height) // 2,
                        lbl_tex.width, lbl_tex.height),
        )

        if self._standings:
            my_id = getattr(self._net, "player_id", None) if self._net else 0
            ids = sorted(self._standings.keys())
            if my_id in ids:
                ids.remove(my_id)
                ids.insert(0, my_id)
            parts = [f"P{pid} {self._standings.get(pid, 0)}" for pid in ids]
            sc_surf = self._font_lg.render("   ".join(parts), True, (255, 255, 255))
            sc_tex = Texture.from_surface(renderer, sc_surf)
            renderer.blit(
                sc_tex,
                pygame.Rect(
                    self._width - sc_tex.width - _s(16),
                    (strip_h - sc_tex.height) // 2,
                    sc_tex.width, sc_tex.height,
                ),
            )

    def _draw_turn_result_panel(self, renderer, sim_time_ms: float) -> None:
        """Draw a translucent result card in the centre when a turn just ended."""
        pr = self._last_result_payload
        if pr is None:
            return

        cur = self._current_boundary()
        if cur is None or sim_time_ms < cur.play_end_ms:
            return

        age = sim_time_ms - pr["shown_at_ms"]
        if age > _RESULT_PANEL_MS:
            return

        def _s(v):
            return max(1, int(v * self._sy))

        # Fade in/out envelope
        fade = 1.0
        if age < 200:
            fade = age / 200.0
        elif age > _RESULT_PANEL_MS - 400:
            fade = max(0.0, (_RESULT_PANEL_MS - age) / 400.0)
        alpha = int(230 * fade)

        panel_w = _s(420)
        panel_h = _s(120)
        x = (self._width - panel_w) // 2
        y = _s(46)

        renderer.draw_color = (6, 12, 28, alpha)
        renderer.fill_rect((x, y, panel_w, panel_h))
        renderer.draw_color = (0, 200, 255, alpha)
        renderer.fill_rect((x, y, panel_w, _s(2)))
        renderer.fill_rect((x, y + panel_h - _s(2), panel_w, _s(2)))

        # Title
        title = f"TURN {pr['turn']} RESULT"
        t_surf = self._font_sm.render(title, True, (180, 230, 255))
        t_tex = Texture.from_surface(renderer, t_surf)
        t_tex.alpha = alpha
        renderer.blit(
            t_tex,
            pygame.Rect(x + (panel_w - t_tex.width) // 2, y + _s(10),
                        t_tex.width, t_tex.height),
        )

        # My / Opp score columns
        my_score = pr["my_score"]
        opp_score = pr["opp_score"]
        winner = None
        if self._net is None:
            winner = "my" if my_score > 0 else None
        else:
            if my_score > opp_score:
                winner = "my"
            elif opp_score > my_score:
                winner = "opp"

        def _col(col_x: int, label: str, score: int, acc: float, highlight: bool):
            col = (0, 255, 220) if highlight else (220, 230, 240)
            lbl = self._font_sm.render(label, True, (140, 170, 200))
            sc = self._font_lg.render(f"{score:,}", True, col)
            ac = self._font_sm.render(f"{acc:.1f}%", True, (160, 180, 200))
            for surf, oy in ((lbl, 42), (sc, 60), (ac, 94)):
                tex = Texture.from_surface(renderer, surf)
                tex.alpha = alpha
                renderer.blit(
                    tex,
                    pygame.Rect(col_x - tex.width // 2, y + _s(oy),
                                tex.width, tex.height),
                )

        left_cx = x + panel_w // 4
        right_cx = x + (panel_w * 3) // 4
        _col(left_cx, "YOU", my_score, pr["my_acc"], winner == "my")
        _col(right_cx, "OPP", opp_score, pr["opp_acc"], winner == "opp")

    # -- Player cards (side panels flanking the centered lane) -------------

    def _draw_player_cards(self, renderer) -> None:
        game = self._game
        if not hasattr(game, "lane_x") or not game.lane_x:
            return

        def _s(v):
            return max(1, int(v * self._sy))

        strip_h = _s(34)
        margin = _s(14)
        top = strip_h + _s(24)
        bottom = self._height - _s(60)
        height = max(_s(160), bottom - top)

        lane_left = game.lane_x[0]
        lane_right = game.lane_x[-1] + game.lane_w

        left_avail = lane_left - margin * 2
        right_avail = (self._width - lane_right) - margin * 2
        card_w = max(_s(140), min(_s(240), left_avail, right_avail))

        left_x = margin
        right_x = self._width - margin - card_w

        my_delta = _judgment_delta(game.judgments, self._turn_baseline)
        opp_delta = _judgment_delta(game.ai_judgments, self._opponent_baseline)

        my_score = _calc_score(my_delta)
        opp_score = _calc_score(opp_delta)
        my_acc = _calc_accuracy(my_delta)
        opp_acc = _calc_accuracy(opp_delta)
        my_combo = int(getattr(game, "combo", 0) or 0)
        opp_combo = int(getattr(game, "ai_combo", 0) or 0)

        my_id = getattr(self._net, "player_id", None) if self._net else 0
        my_label = f"P{my_id}  YOU" if my_id else "YOU"
        opp_label = "OPP" if self._net else "—"

        self._draw_side_card(
            renderer, left_x, top, card_w, height,
            my_label, my_score, my_acc, my_combo,
            accent=(0, 255, 220),
        )
        self._draw_side_card(
            renderer, right_x, top, card_w, height,
            opp_label, opp_score, opp_acc, opp_combo,
            accent=(255, 120, 120),
        )

    def _draw_side_card(self, renderer, x, y, w, h, label, score, acc, combo,
                         *, accent) -> None:
        def _s(v):
            return max(1, int(v * self._sy))

        pad = _s(12)

        renderer.draw_color = (6, 10, 22, 220)
        renderer.fill_rect((x, y, w, h))
        renderer.draw_color = (*accent, 200)
        renderer.fill_rect((x, y, w, _s(3)))
        renderer.fill_rect((x, y + h - _s(3), w, _s(3)))
        renderer.fill_rect((x, y, _s(3), h))
        renderer.fill_rect((x + w - _s(3), y, _s(3), h))

        def _blit(surf, dst_x, dst_y):
            tex = Texture.from_surface(renderer, surf)
            renderer.blit(tex, pygame.Rect(dst_x, dst_y, tex.width, tex.height))

        cy = y + pad

        # Label
        lbl_surf = self._font_sm.render(label, True, accent)
        _blit(lbl_surf, x + (w - lbl_surf.get_width()) // 2, cy)
        cy += lbl_surf.get_height() + _s(10)

        # Thin accent divider
        renderer.draw_color = (*accent, 120)
        renderer.fill_rect((x + pad, cy, w - pad * 2, _s(1)))
        cy += _s(12)

        # SCORE caption + big number
        cap1 = self._font_sm.render("SCORE", True, (140, 170, 200))
        _blit(cap1, x + (w - cap1.get_width()) // 2, cy)
        cy += cap1.get_height() + _s(4)

        score_surf = self._font_lg.render(f"{score:,}", True, (255, 255, 255))
        _blit(score_surf, x + (w - score_surf.get_width()) // 2, cy)
        cy += score_surf.get_height() + _s(16)

        # COMBO
        cap2 = self._font_sm.render("COMBO", True, (140, 170, 200))
        _blit(cap2, x + (w - cap2.get_width()) // 2, cy)
        cy += cap2.get_height() + _s(4)

        combo_surf = self._font_lg.render(f"{combo}", True, (220, 235, 255))
        _blit(combo_surf, x + (w - combo_surf.get_width()) // 2, cy)
        cy += combo_surf.get_height() + _s(16)

        # ACCURACY
        cap3 = self._font_sm.render("ACCURACY", True, (140, 170, 200))
        _blit(cap3, x + (w - cap3.get_width()) // 2, cy)
        cy += cap3.get_height() + _s(4)

        acc_surf = self._font_lg.render(f"{acc:.1f}%", True, (200, 220, 240))
        _blit(acc_surf, x + (w - acc_surf.get_width()) // 2, cy)

    def draw_overlay(self, renderer, window, game_state, phase) -> None:
        if phase == "playing":
            sim_time_ms = self._last_sim_time_ms
            self._draw_turn_hud(renderer, sim_time_ms)
            self._draw_player_cards(renderer)
            self._draw_turn_result_panel(renderer, sim_time_ms)
            return
        # phase == "result" / "paused" — no extra overlay; the menu-level
        # BattleResultScreen handles end-of-battle presentation.

    # -- Stats ---------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _judgment_delta(current: dict, baseline: dict) -> dict:
    return {k: max(0, current.get(k, 0) - baseline.get(k, 0)) for k in JUDGMENT_ORDER}


def _calc_score(j: dict) -> int:
    return j.get("PERFECT", 0) * 1000 + j.get("GREAT", 0) * 500 + j.get("GOOD", 0) * 200


def _calc_accuracy(j: dict) -> float:
    total = sum(j.values()) if j else 0
    if not total:
        return 0.0
    return (j.get("PERFECT", 0) * 1.0 + j.get("GREAT", 0) * 0.7 + j.get("GOOD", 0) * 0.3) / total * 100.0
