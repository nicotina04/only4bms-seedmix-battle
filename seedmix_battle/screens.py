"""Seedmix Battle — end-of-battle result screen.

The per-turn transition phase now lives entirely inside the RhythmGame
session (handled by ``SeedmixBattleExtension`` drawing overlays on top
of the playfield), so this module only holds the final result screen.
"""

from __future__ import annotations

import pygame
from pygame._sdl2.video import Texture

from only4bms import i18n as _i18n
from only4bms.ui.components import (
    make_bg_cache, draw_glass_panel,
    draw_glow_text, draw_hint_bar,
    C_TEXT_PRIMARY, C_TEXT_SECONDARY, C_TEXT_DIM,
    C_GLOW_CYAN,
    BASE_H,
)

from .i18n import t as _t

C_ACCENT = C_GLOW_CYAN


class BattleResultScreen:
    """Final scoreboard: winner banner, final standings, 10-turn history."""

    def __init__(self, settings, renderer, window, result: dict,
                 my_player_id: int | None = None):
        self.settings = settings
        self.renderer = renderer
        self.window = window
        self.w, self.h = window.size
        self.sy = self.h / BASE_H

        self.result = result or {}
        self.my_id = my_player_id

        self.screen = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        self._bg = make_bg_cache(self.w, self.h)
        self.texture = None

        self.title_font = _i18n.font("menu_title", self.sy, bold=True)
        self.big_font = _i18n.font("hud_bold", self.sy, bold=True)
        self.body_font = _i18n.font("ui_body", self.sy)
        self.small_font = _i18n.font("menu_small", self.sy)

        self.clock = pygame.time.Clock()
        self.running = True

    def _s(self, v):
        return max(1, int(v * self.sy))

    def run(self):
        pygame.event.clear()
        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return
                if event.type == pygame.KEYDOWN and event.key in (
                    pygame.K_RETURN, pygame.K_ESCAPE, pygame.K_SPACE,
                ):
                    self.running = False
                    return

            self._draw()
            self._present()
            self.clock.tick(60)

    def _draw(self):
        self.screen.blit(self._bg, (0, 0))

        draw_glow_text(
            self.screen, _t("sm_battle_result"), self.title_font,
            C_ACCENT, C_ACCENT,
            (self.w // 2, self._s(30)), anchor="center", glow_radius=4,
        )

        winner_id = self.result.get("winner_id")
        standings = self.result.get("final_standings", {})
        history = self.result.get("turn_history", [])

        if winner_id is not None:
            is_me = (winner_id == self.my_id) if self.my_id is not None else None
            if is_me is True:
                banner_txt, banner_col = "YOU WIN", (0, 255, 200)
            elif is_me is False:
                banner_txt, banner_col = "YOU LOSE", (255, 80, 80)
            else:
                banner_txt, banner_col = f"WINNER  P{winner_id}", C_ACCENT
        else:
            banner_txt, banner_col = "DRAW", (255, 220, 0)

        banner = self.big_font.render(banner_txt, True, banner_col)
        self.screen.blit(banner, banner.get_rect(centerx=self.w // 2, top=self._s(80)))

        if standings:
            ids = sorted(standings.keys(), key=lambda k: standings[k], reverse=True)
            parts = [f"P{pid}  {standings[pid]}" for pid in ids]
            s_surf = self.body_font.render("     ".join(parts), True, C_TEXT_PRIMARY)
            self.screen.blit(s_surf, s_surf.get_rect(centerx=self.w // 2, top=self._s(130)))

        panel_w = self._s(520)
        panel_h = self._s(340)
        panel = pygame.Rect((self.w - panel_w) // 2, self._s(170), panel_w, panel_h)
        draw_glass_panel(self.screen, panel, border_color=(*C_ACCENT, 90), radius=14, fill_alpha=10)

        hdr = self.small_font.render("TURN HISTORY", True, C_TEXT_DIM)
        self.screen.blit(hdr, (panel.x + self._s(20), panel.y + self._s(12)))

        row_h = self._s(26)
        y = panel.y + self._s(40)
        for entry in history:
            turn = entry.get("turn", "?")
            scores = entry.get("scores", [])
            w_id = entry.get("winner_id")

            label = f"Turn {turn}"
            score_txt = "  vs  ".join(f"{s:,}" for s in scores) if scores else "—"
            win_txt = "DRAW" if w_id is None else f"P{w_id}"

            col = C_ACCENT if (self.my_id is not None and w_id == self.my_id) else C_TEXT_PRIMARY

            l_surf = self.small_font.render(label, True, C_TEXT_SECONDARY)
            s_surf = self.small_font.render(score_txt, True, col)
            w_surf = self.small_font.render(win_txt, True, col)

            self.screen.blit(l_surf, (panel.x + self._s(24), y))
            self.screen.blit(s_surf, (panel.x + self._s(140), y))
            self.screen.blit(w_surf, (panel.x + panel.w - w_surf.get_width() - self._s(24), y))
            y += row_h
            if y > panel.y + panel.h - row_h:
                break

        draw_hint_bar(
            self.screen, "ENTER / ESC  Continue",
            self.small_font, self.h, self.w,
        )

    def _present(self):
        if not self.texture:
            self.texture = Texture.from_surface(self.renderer, self.screen)
        else:
            self.texture.update(self.screen)
        self.renderer.clear()
        self.renderer.blit(self.texture, pygame.Rect(0, 0, self.w, self.h))
        self.renderer.present()
