"""Seedmix Battle — top menu, private lobby and battle runner.

The flow is intentionally song-selection-free:

    Menu → Solo Test                            → run a single generated chart
    Menu → Private Match → Host/Join → Lobby    → host triggers `start_battle`
                                                → loop 10 turns of generated charts
                                                → final result screen
"""

from __future__ import annotations

import threading
import time

import pygame

from only4bms import i18n as _i18n
from only4bms.ui.components import (
    make_bg_cache, draw_glass_panel, draw_outer_glow,
    draw_glow_text, draw_hint_bar,
    C_TEXT_PRIMARY, C_TEXT_SECONDARY, C_TEXT_DIM,
    C_GLOW_CYAN, C_GLOW_PURPLE, C_BORDER_DIM,
    BASE_W, BASE_H,
)

from .i18n import t as _t

C_ACCENT = C_GLOW_CYAN
C_DISABLED = (60, 65, 85)


# ---------------------------------------------------------------------------
# Top menu: Solo / Private / Official
# ---------------------------------------------------------------------------

class SeedmixMenu:
    def __init__(self, settings, renderer, window, **ctx):
        self.settings = settings
        self.renderer = renderer
        self.window = window
        self.ctx = ctx

        self.w, self.h = window.size
        self.sx, self.sy = self.w / BASE_W, self.h / BASE_H

        self.screen = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        self._bg = make_bg_cache(self.w, self.h)
        self.texture = None

        self.title_font = _i18n.font("menu_title", self.sy, bold=True)
        self.font = _i18n.font("menu_option", self.sy)
        self.body_font = _i18n.font("ui_body", self.sy)
        self.small_font = _i18n.font("menu_small", self.sy)

        self.clock = pygame.time.Clock()
        self.running = True
        self.selected = 0  # 0: solo, 1: private, 2: official(disabled)

    def _s(self, v):
        return max(1, int(v * self.sy))

    def run(self):
        from pygame._sdl2.video import Texture

        pygame.key.set_repeat(300, 50)
        pygame.event.clear()

        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        self.running = False
                    elif event.key == pygame.K_UP:
                        self.selected = max(0, self.selected - 1)
                    elif event.key == pygame.K_DOWN:
                        self.selected = min(2, self.selected + 1)
                    elif event.key == pygame.K_RETURN:
                        if self.selected == 0:
                            self._run_solo_test()
                        elif self.selected == 1:
                            PrivateLobbyMenu(self.settings, self.renderer, self.window, **self.ctx).run()

            self._draw()

            if not self.texture:
                self.texture = Texture.from_surface(self.renderer, self.screen)
            else:
                self.texture.update(self.screen)

            self.renderer.clear()
            self.renderer.blit(self.texture, pygame.Rect(0, 0, self.w, self.h))
            self.renderer.present()
            self.clock.tick(self.settings.get("fps", 60))

        pygame.key.set_repeat(0)

    def _draw(self):
        self.screen.blit(self._bg, (0, 0))

        draw_glow_text(
            self.screen, _t("menu_seedmix_battle"), self.title_font,
            C_ACCENT, C_ACCENT,
            (self.w // 2, self._s(30)), anchor="center", glow_radius=4,
        )

        items = [
            (_t("sm_solo_test"), _t("sm_solo_desc"), True),
            (_t("sm_private_match"), _t("sm_private_desc"), True),
            (_t("sm_official_match"), _t("sm_official_soon"), False),
        ]

        card_w = self._s(440)
        card_h = self._s(80)
        start_y = self._s(140)
        gap = self._s(16)

        for i, (label, desc, enabled) in enumerate(items):
            is_sel = (i == self.selected)
            rect = pygame.Rect((self.w - card_w) // 2, start_y + i * (card_h + gap), card_w, card_h)

            if is_sel and enabled:
                draw_outer_glow(self.screen, rect, C_ACCENT, radius=10, passes=3, max_alpha=28)
                border = (*C_ACCENT, 180)
                fill_alpha = 18
            elif is_sel and not enabled:
                border = (*C_DISABLED, 120)
                fill_alpha = 8
            else:
                border = (*C_BORDER_DIM[:3], 50)
                fill_alpha = 6

            draw_glass_panel(self.screen, rect, border_color=border, radius=12, fill_alpha=fill_alpha)

            lbl_color = C_ACCENT if (is_sel and enabled) else (C_DISABLED if not enabled else C_TEXT_PRIMARY)
            desc_color = C_TEXT_SECONDARY if enabled else C_TEXT_DIM

            self.screen.blit(self.font.render(label, True, lbl_color),
                             (rect.x + self._s(24), rect.y + self._s(14)))
            self.screen.blit(self.small_font.render(desc, True, desc_color),
                             (rect.x + self._s(24), rect.y + self._s(48)))

        draw_hint_bar(self.screen, "UP/DOWN  Select    ENTER  Confirm    ESC  Back",
                      self.small_font, self.h, self.w)

    def _run_solo_test(self):
        """Solo test: run the full 10-turn battle flow locally, no network."""
        SoloBattleRunner(self.settings, self.renderer, self.window, self.ctx).run()


# ---------------------------------------------------------------------------
# Private lobby + battle runner
# ---------------------------------------------------------------------------

class PrivateLobbyMenu:
    def __init__(self, settings, renderer, window, **ctx):
        self.settings = settings
        self.renderer = renderer
        self.window = window
        self.ctx = ctx

        self.w, self.h = window.size
        self.sx, self.sy = self.w / BASE_W, self.h / BASE_H

        self.screen = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        self._bg = make_bg_cache(self.w, self.h)
        self.texture = None

        self.title_font = _i18n.font("ui_title", self.sy, bold=True)
        self.font = _i18n.font("menu_option", self.sy)
        self.body_font = _i18n.font("ui_body", self.sy)
        self.small_font = _i18n.font("menu_small", self.sy)

        self.clock = pygame.time.Clock()
        self.running = True

        # MODE_SELECT → HOST_LOBBY / JOIN_INPUT → CONNECTING → LOBBY
        self.state = "MODE_SELECT"
        self.selected = 0

        self.address_input = self.settings.get("last_private_ip", "127.0.0.1:7215")
        self.server_addr = None
        self.client = None  # BattleClient
        self.status_msg = ""

    def _s(self, v):
        return max(1, int(v * self.sy))

    def run(self):
        from pygame._sdl2.video import Texture

        pygame.key.set_repeat(300, 50)
        pygame.event.clear()

        while self.running:
            self._handle_events()
            self._draw()

            if not self.texture:
                self.texture = Texture.from_surface(self.renderer, self.screen)
            else:
                self.texture.update(self.screen)

            self.renderer.clear()
            self.renderer.blit(self.texture, pygame.Rect(0, 0, self.w, self.h))
            self.renderer.present()
            self.clock.tick(self.settings.get("fps", 60))

        pygame.key.set_repeat(0)
        self._cleanup()

    def _cleanup(self):
        from . import private_server
        if private_server.is_running():
            private_server.stop_server()
        if self.client:
            self.client.disconnect()
            self.client = None

    def _handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
                return
            if event.type != pygame.KEYDOWN:
                continue

            if event.key == pygame.K_ESCAPE:
                if self.state == "MODE_SELECT":
                    self.running = False
                else:
                    self._cleanup()
                    self.state = "MODE_SELECT"
                return

            if self.state == "MODE_SELECT":
                if event.key in (pygame.K_UP, pygame.K_DOWN):
                    self.selected = 1 - self.selected
                elif event.key == pygame.K_RETURN:
                    if self.selected == 0:
                        self._start_host()
                    else:
                        self.state = "JOIN_INPUT"

            elif self.state == "JOIN_INPUT":
                if event.key == pygame.K_RETURN:
                    self._join_room()
                elif event.key == pygame.K_BACKSPACE:
                    self.address_input = self.address_input[:-1]
                elif event.unicode.isprintable() and len(self.address_input) < 40:
                    self.address_input += event.unicode

            elif self.state in ("HOST_LOBBY", "LOBBY"):
                if event.key == pygame.K_RETURN:
                    if self.client and self.client.player_id == self.client.host_id:
                        self._start_battle()

    def _start_host(self):
        from . import private_server
        from .battle_client import BattleClient

        host_ip, port = private_server.start_server()
        self.server_addr = f"{host_ip}:{port}"
        self.status_msg = _t("sm_server_running")

        self.client = BattleClient()
        self.client.connect(f"127.0.0.1:{port}")
        self.client.join_lobby()

        self.state = "HOST_LOBBY"

    def _join_room(self):
        from .battle_client import BattleClient

        self.settings["last_private_ip"] = self.address_input
        self.client = BattleClient()
        self.status_msg = _t("sm_connecting", dots="...")
        self.state = "CONNECTING"

        def _do_connect():
            if self.client.connect(self.address_input):
                self.client.join_lobby()
                time.sleep(0.5)
                if self.client.is_connected and self.client.player_id is not None:
                    self.state = "LOBBY"
                    self.status_msg = _t("sm_connected")
                else:
                    self.status_msg = _t("sm_connection_failed")
                    self.state = "JOIN_INPUT"
            else:
                self.status_msg = _t("sm_connection_failed")
                self.state = "JOIN_INPUT"

        threading.Thread(target=_do_connect, daemon=True).start()

    # -- Battle loop ---------------------------------------------------------

    def _start_battle(self):
        if not self.client or self.client.player_id != self.client.host_id:
            return
        self.client.start_battle()
        # Battle loop runs in current thread (blocks the menu loop, which is fine).
        BattleRunner(self.settings, self.renderer, self.window, self.ctx, self.client).run()

    # -- Drawing -------------------------------------------------------------

    def _draw(self):
        self.screen.blit(self._bg, (0, 0))

        draw_glow_text(
            self.screen, _t("sm_private_match"), self.title_font,
            C_ACCENT, C_ACCENT,
            (self.w // 2, self._s(30)), anchor="center", glow_radius=3,
        )

        if self.state == "MODE_SELECT":
            self._draw_mode_select()
        elif self.state in ("HOST_LOBBY", "LOBBY"):
            self._draw_lobby()
        elif self.state in ("JOIN_INPUT", "CONNECTING"):
            self._draw_join_input()

    def _draw_mode_select(self):
        items = [
            (_t("sm_host_room"), C_ACCENT),
            (_t("sm_join_room"), C_GLOW_PURPLE),
        ]

        card_w = self._s(340)
        card_h = self._s(60)
        start_y = self._s(140)
        gap = self._s(14)

        for i, (label, accent) in enumerate(items):
            is_sel = (i == self.selected)
            rect = pygame.Rect((self.w - card_w) // 2, start_y + i * (card_h + gap), card_w, card_h)

            if is_sel:
                draw_outer_glow(self.screen, rect, accent, radius=10, passes=3, max_alpha=28)
                border = (*accent, 180)
            else:
                border = (*C_BORDER_DIM[:3], 50)

            draw_glass_panel(self.screen, rect, border_color=border, radius=12,
                             fill_alpha=16 if is_sel else 6)

            color = accent if is_sel else C_TEXT_PRIMARY
            s = self.font.render(label, True, color)
            self.screen.blit(s, s.get_rect(center=rect.center))

        draw_hint_bar(self.screen, "UP/DOWN  Select    ENTER  Confirm    ESC  Back",
                      self.small_font, self.h, self.w)

    def _draw_lobby(self):
        panel_w, panel_h = self._s(480), self._s(320)
        panel = pygame.Rect((self.w - panel_w) // 2, self._s(100), panel_w, panel_h)
        draw_glass_panel(self.screen, panel, border_color=(*C_ACCENT, 80), radius=14, fill_alpha=10)

        y = panel.y + self._s(18)

        if self.server_addr:
            addr_s = self.small_font.render(
                _t("sm_your_address", addr=self.server_addr), True, C_TEXT_SECONDARY,
            )
            self.screen.blit(addr_s, (panel.x + self._s(20), y))
            y += self._s(30)

        players = []
        if self.client:
            players = self.client.lobby_state.get("players", [])

        lbl = self.small_font.render("LOBBY", True, C_TEXT_DIM)
        self.screen.blit(lbl, (panel.x + self._s(20), y))
        y += lbl.get_height() + self._s(10)

        for p in players:
            is_host = (p["id"] == self.client.host_id)
            is_me = (p["id"] == self.client.player_id)
            name = f"Player {p['id']}"
            if is_me:
                name += f"  ({_t('sm_host_room') if is_host else 'You'})"

            row_rect = pygame.Rect(panel.x + self._s(10), y, panel.width - self._s(20), self._s(40))
            if is_me:
                bg = pygame.Surface(row_rect.size, pygame.SRCALPHA)
                pygame.draw.rect(bg, (*C_ACCENT, 14), (0, 0, *row_rect.size), border_radius=8)
                self.screen.blit(bg, row_rect.topleft)

            color = C_ACCENT if is_me else C_TEXT_PRIMARY
            s = self.body_font.render(name, True, color)
            self.screen.blit(s, (row_rect.x + self._s(14), row_rect.centery - s.get_height() // 2))
            y += self._s(46)

        if len(players) < 2:
            wait = self.body_font.render(_t("sm_waiting_opponent"), True, C_TEXT_SECONDARY)
            self.screen.blit(wait, wait.get_rect(centerx=panel.centerx, top=y + self._s(10)))
        elif self.client and self.client.player_id == self.client.host_id:
            hint = self.body_font.render(_t("sm_start_battle"), True, C_ACCENT)
            self.screen.blit(hint, hint.get_rect(centerx=panel.centerx, top=y + self._s(10)))

        draw_hint_bar(self.screen, "ENTER  Start Battle (Host)    ESC  Leave",
                      self.small_font, self.h, self.w)

    def _draw_join_input(self):
        panel_w, panel_h = self._s(420), self._s(180)
        panel = pygame.Rect((self.w - panel_w) // 2, self._s(140), panel_w, panel_h)
        draw_glass_panel(self.screen, panel, border_color=(*C_GLOW_PURPLE, 80), radius=14, fill_alpha=10)

        y = panel.y + self._s(20)
        lbl = self.body_font.render(_t("sm_enter_address"), True, C_TEXT_SECONDARY)
        self.screen.blit(lbl, lbl.get_rect(centerx=panel.centerx, top=y))
        y += lbl.get_height() + self._s(16)

        bw = panel.width - self._s(40)
        bh = self._s(46)
        bx = panel.x + self._s(20)

        box = pygame.Rect(bx, y, bw, bh)
        bg = pygame.Surface((bw, bh), pygame.SRCALPHA)
        pygame.draw.rect(bg, (*C_GLOW_PURPLE, 8), (0, 0, bw, bh), border_radius=8)
        self.screen.blit(bg, (bx, y))
        pygame.draw.rect(self.screen, (*C_GLOW_PURPLE, 120), box, 1, border_radius=8)

        cursor = "_" if self.state == "JOIN_INPUT" else ""
        val = self.body_font.render(self.address_input + cursor, True, C_TEXT_PRIMARY)
        self.screen.blit(val, (bx + self._s(12), y + (bh - val.get_height()) // 2))

        y += bh + self._s(14)
        if self.status_msg:
            status = self.small_font.render(self.status_msg, True, C_TEXT_DIM)
            self.screen.blit(status, status.get_rect(centerx=panel.centerx, top=y))

        draw_hint_bar(self.screen, "ENTER  Connect    ESC  Back",
                      self.small_font, self.h, self.w)


# ---------------------------------------------------------------------------
# Battle runner — drives the 10-turn flow
# ---------------------------------------------------------------------------

TOTAL_TURNS = 10


class BattleRunner:
    """Online battle runner — waits for seed distribution, builds a single
    mega-chart, plays the entire 10-turn battle inside one RhythmGame
    session, then shows the final result screen.

    Per-turn network events (`turn_complete`, `opponent_score`) are fired
    by the extension from inside the RhythmGame loop, not here.
    """

    def __init__(self, settings, renderer, window, ctx, client):
        self.settings = settings
        self.renderer = renderer
        self.window = window
        self.ctx = ctx
        self.client = client

    def run(self):
        if not self.client.wait_for(lambda: bool(self.client.turns), timeout=5.0):
            return

        turns = list(self.client.turns)

        # We still send a single turn_ready for turn 1 so the server's
        # state machine leaves BATTLE_READY. Subsequent turns are driven
        # entirely by the baked mega-chart timeline.
        self.client.send_turn_ready(1)

        from .pattern.battle_chart import build_battle_chart
        mega = build_battle_chart(turns)

        _run_mega_chart(
            self.settings, self.renderer, self.window, self.ctx, mega,
            battle_client=self.client, total_turns=len(turns),
        )

        # Wait a short moment for the last turn's network round-trip.
        self.client.wait_for(
            lambda: self.client.battle_result is not None, timeout=3.0,
        )
        result = self.client.battle_result or _fallback_result(len(turns))
        self.client.battle_result = None

        from .screens import BattleResultScreen
        BattleResultScreen(
            self.settings, self.renderer, self.window, result,
            my_player_id=getattr(self.client, "player_id", None),
        ).run()


class SoloBattleRunner:
    """Local 10-turn battle without network. Uses the same continuous-play
    mega-chart flow as the online runner — a single RhythmGame session."""

    def __init__(self, settings, renderer, window, ctx):
        self.settings = settings
        self.renderer = renderer
        self.window = window
        self.ctx = ctx

    def run(self):
        import random as _r
        from .pattern import THEMES
        from .pattern.battle_chart import build_battle_chart

        rng = _r.Random()
        seed_root = rng.randrange(1, 2**31)
        turn_rng = _r.Random(seed_root)

        turns = [
            {
                "turn": i + 1,
                "seed": turn_rng.randrange(1, 2**31),
                "bpm": turn_rng.randint(130, 180),
                "theme": turn_rng.choice(THEMES),
                "owner_id": 0,
            }
            for i in range(TOTAL_TURNS)
        ]

        mega = build_battle_chart(turns)
        _run_mega_chart(
            self.settings, self.renderer, self.window, self.ctx, mega,
            battle_client=None, total_turns=len(turns),
        )

        # Fabricate a solo result summary from the (non-existent) standings.
        from .screens import BattleResultScreen
        BattleResultScreen(
            self.settings, self.renderer, self.window,
            {
                "winner_id": 0,
                "final_standings": {0: TOTAL_TURNS},
                "turn_history": [
                    {"turn": t["turn"], "winner_id": 0, "scores": [0]}
                    for t in turns
                ],
            },
            my_player_id=0,
        ).run()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_mega_chart(settings, renderer, window, ctx, mega, *,
                    battle_client, total_turns: int):
    """Spin up a single RhythmGame session playing the stitched battle."""
    from only4bms.game.rhythm_game import RhythmGame
    from .extension import SeedmixBattleExtension

    init_mixer_fn = ctx.get("init_mixer_fn")
    challenge_manager = ctx.get("challenge_manager")
    if init_mixer_fn:
        init_mixer_fn(settings)

    game = RhythmGame(
        mega.notes, mega.bgms, mega.bgas, mega.wav_map, mega.bmp_map,
        mega.title, settings,
        visual_timing_map=mega.visual_timing_map,
        measures=mega.measures,
        mode="seedmix_battle",
        metadata=mega.metadata,
        renderer=renderer,
        window=window,
        note_mod="None",
        challenge_manager=challenge_manager,
        extension=SeedmixBattleExtension(
            battle_client,
            turn_boundaries=mega.turn_boundaries,
            total_turns=total_turns,
        ),
    )
    game.run()


def _fallback_result(total_turns: int) -> dict:
    return {
        "winner_id": None,
        "final_standings": {},
        "turn_history": [
            {"turn": i + 1, "winner_id": None, "scores": []}
            for i in range(total_turns)
        ],
    }
