"""
Seedmix Battle — Main menu and private match lobby UI.
"""

import pygame
import threading
import time
import os

from only4bms import i18n as _i18n
from only4bms import paths
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


class SeedmixMenu:
    """Top-level menu: Private Match / Official Match selector."""

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
        self.selected = 0  # 0: private, 1: official

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
                    elif event.key in (pygame.K_UP, pygame.K_DOWN):
                        self.selected = 1 - self.selected
                    elif event.key == pygame.K_RETURN:
                        if self.selected == 0:
                            self._run_private_match()
                        # official match: disabled for now

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

        # Title
        draw_glow_text(
            self.screen, _t("menu_seedmix_battle"), self.title_font,
            C_ACCENT, C_ACCENT,
            (self.w // 2, self._s(30)), anchor="center", glow_radius=4,
        )

        # Accent line
        lw = self._s(220)
        lx = (self.w - lw) // 2
        ly = self._s(86)
        for x in range(lw):
            t = 1.0 - abs(x - lw / 2) / (lw / 2 + 1)
            self.screen.set_at((lx + x, ly), (*C_ACCENT, int(55 * t)))

        # Menu items
        items = [
            (_t("sm_private_match"), _t("sm_private_desc"), True),
            (_t("sm_official_match"), _t("sm_official_soon"), False),
        ]

        card_w = self._s(440)
        card_h = self._s(80)
        start_y = self._s(140)
        gap = self._s(16)

        for i, (label, desc, enabled) in enumerate(items):
            is_sel = (i == self.selected)
            rect = pygame.Rect(
                (self.w - card_w) // 2,
                start_y + i * (card_h + gap),
                card_w, card_h,
            )

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

            lbl_s = self.font.render(label, True, lbl_color)
            desc_s = self.small_font.render(desc, True, desc_color)

            self.screen.blit(lbl_s, (rect.x + self._s(24), rect.y + self._s(14)))
            self.screen.blit(desc_s, (rect.x + self._s(24), rect.y + self._s(48)))

        draw_hint_bar(
            self.screen, "UP/DOWN  Select    ENTER  Confirm    ESC  Back",
            self.small_font, self.h, self.w,
        )

    # -- Private match flow -------------------------------------------------

    def _run_private_match(self):
        lobby = PrivateLobbyMenu(
            self.settings, self.renderer, self.window, **self.ctx,
        )
        lobby.run()


class PrivateLobbyMenu:
    """Private match: host/join selector and lobby."""

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

        # States: MODE_SELECT -> HOST_LOBBY / JOIN_INPUT -> LOBBY -> SONG_SELECT -> PLAYING
        self.state = "MODE_SELECT"
        self.selected = 0  # 0: host, 1: join

        self.address_input = self.settings.get("last_private_ip", "127.0.0.1:7215")
        self.server_addr = None
        self.net = None  # NetworkManager instance
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
        if self.net:
            self.net.disconnect()

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
                elif self.state in ("HOST_LOBBY", "JOIN_INPUT", "LOBBY"):
                    self._cleanup()
                    self.state = "MODE_SELECT"
                    self.net = None
                elif self.state == "SONG_SELECT":
                    self.state = "LOBBY"
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

            elif self.state == "LOBBY":
                if event.key == pygame.K_RETURN:
                    if self.net and self.net.player_id == self.net.host_id:
                        self._open_song_select()

            elif self.state == "SONG_SELECT":
                self._handle_song_select(event)

    def _start_host(self):
        from . import private_server
        from only4bms.core.network_manager import NetworkManager

        host_ip, port = private_server.start_server()
        self.server_addr = f"{host_ip}:{port}"
        self.status_msg = _t("sm_server_running")

        # Connect to own server
        self.net = NetworkManager()
        self.net.connect(f"127.0.0.1:{port}")
        self.net.join_lobby()

        self.state = "HOST_LOBBY"

    def _join_room(self):
        from only4bms.core.network_manager import NetworkManager

        self.settings["last_private_ip"] = self.address_input
        self.net = NetworkManager()
        self.status_msg = _t("sm_connecting", dots="...")

        def connect():
            if self.net.connect(self.address_input):
                self.net.join_lobby()
                time.sleep(0.5)
                if self.net.is_connected and self.net.player_id:
                    self.state = "LOBBY"
                    self.status_msg = _t("sm_connected")
                else:
                    self.status_msg = _t("sm_connection_failed")
                    self.state = "JOIN_INPUT"
            else:
                self.status_msg = _t("sm_connection_failed")
                self.state = "JOIN_INPUT"

        threading.Thread(target=connect, daemon=True).start()
        self.state = "CONNECTING"

    def _open_song_select(self):
        self.song_list = []
        self.song_idx = 0

        # Scan local BMS directory
        song_dir = paths.SONG_DIR
        if os.path.isdir(song_dir):
            for entry in sorted(os.listdir(song_dir)):
                entry_path = os.path.join(song_dir, entry)
                if os.path.isdir(entry_path):
                    for f in os.listdir(entry_path):
                        if f.lower().endswith((".bms", ".bme", ".bml")):
                            self.song_list.append({
                                "dir": entry,
                                "file": f,
                                "path": os.path.join(entry_path, f),
                            })

        self.state = "SONG_SELECT"

    def _handle_song_select(self, event):
        if event.key == pygame.K_UP:
            self.song_idx = max(0, self.song_idx - 1)
        elif event.key == pygame.K_DOWN:
            if self.song_list:
                self.song_idx = min(len(self.song_list) - 1, self.song_idx + 1)
        elif event.key == pygame.K_RETURN and self.song_list:
            self._start_game(self.song_list[self.song_idx])

    def _start_game(self, song_info):
        from only4bms.core.bms_parser import BMSParser
        from only4bms.game.rhythm_game import RhythmGame
        from .extension import SeedmixBattleExtension

        init_mixer_fn = self.ctx.get("init_mixer_fn")
        challenge_manager = self.ctx.get("challenge_manager")

        if init_mixer_fn:
            init_mixer_fn(self.settings)

        parser = BMSParser(song_info["path"])
        notes, bgms, bgas, bmp_map, visual_timing_map, measures = parser.parse()

        if not notes and not bgms:
            self.state = "LOBBY"
            return

        metadata = {
            "artist": parser.artist,
            "bpm": parser.bpm,
            "level": parser.playlevel,
            "genre": parser.genre,
            "notes": parser.total_notes,
            "stagefile": parser.stagefile,
            "banner": parser.banner,
            "total": parser.total,
            "lanes_compressed": parser.lanes_compressed,
        }

        game = RhythmGame(
            notes, bgms, bgas, parser.wav_map, bmp_map,
            parser.title, self.settings,
            visual_timing_map=visual_timing_map,
            measures=measures,
            mode="seedmix_battle",
            metadata=metadata,
            renderer=self.renderer,
            window=self.window,
            note_mod="None",
            challenge_manager=challenge_manager,
            extension=SeedmixBattleExtension(self.net),
        )
        game.run()
        self.state = "LOBBY"

    # -- Drawing ------------------------------------------------------------

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
        elif self.state == "SONG_SELECT":
            self._draw_song_select()

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
            rect = pygame.Rect(
                (self.w - card_w) // 2,
                start_y + i * (card_h + gap),
                card_w, card_h,
            )

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

        draw_hint_bar(
            self.screen, "UP/DOWN  Select    ENTER  Confirm    ESC  Back",
            self.small_font, self.h, self.w,
        )

    def _draw_lobby(self):
        panel_w, panel_h = self._s(480), self._s(320)
        panel = pygame.Rect((self.w - panel_w) // 2, self._s(100), panel_w, panel_h)
        draw_glass_panel(self.screen, panel, border_color=(*C_ACCENT, 80), radius=14, fill_alpha=10)

        y = panel.y + self._s(18)

        # Server address (host only)
        if self.server_addr:
            addr_s = self.small_font.render(
                _t("sm_your_address", addr=self.server_addr), True, C_TEXT_SECONDARY,
            )
            self.screen.blit(addr_s, (panel.x + self._s(20), y))
            y += self._s(30)

        # Player list
        players = []
        if self.net:
            players = self.net.lobby_state.get("players", [])

        lbl = self.small_font.render("LOBBY", True, C_TEXT_DIM)
        self.screen.blit(lbl, (panel.x + self._s(20), y))
        y += lbl.get_height() + self._s(10)

        for p in players:
            is_host = (p["id"] == self.net.host_id)
            is_me = (p["id"] == self.net.player_id)
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
        elif self.net and self.net.player_id == self.net.host_id:
            hint = self.body_font.render(_t("sm_select_song"), True, C_ACCENT)
            self.screen.blit(hint, hint.get_rect(centerx=panel.centerx, top=y + self._s(10)))

        hint_text = "ENTER  Select Song (Host)    ESC  Leave"
        draw_hint_bar(self.screen, hint_text, self.small_font, self.h, self.w)

    def _draw_join_input(self):
        panel_w, panel_h = self._s(420), self._s(180)
        panel = pygame.Rect((self.w - panel_w) // 2, self._s(140), panel_w, panel_h)
        draw_glass_panel(self.screen, panel, border_color=(*C_GLOW_PURPLE, 80), radius=14, fill_alpha=10)

        y = panel.y + self._s(20)
        lbl = self.body_font.render(_t("sm_enter_address"), True, C_TEXT_SECONDARY)
        self.screen.blit(lbl, lbl.get_rect(centerx=panel.centerx, top=y))
        y += lbl.get_height() + self._s(16)

        # Input box
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

        draw_hint_bar(
            self.screen, "ENTER  Connect    ESC  Back",
            self.small_font, self.h, self.w,
        )

    def _draw_song_select(self):
        panel_w, panel_h = self._s(520), self._s(380)
        panel = pygame.Rect((self.w - panel_w) // 2, self._s(90), panel_w, panel_h)
        draw_glass_panel(self.screen, panel, border_color=(*C_ACCENT, 80), radius=14, fill_alpha=10)

        hdr = self.small_font.render(_t("sm_select_song").upper(), True, C_TEXT_DIM)
        self.screen.blit(hdr, (panel.x + self._s(20), panel.y + self._s(14)))

        if not self.song_list:
            msg = self.body_font.render("No songs found in bms/ folder", True, C_TEXT_SECONDARY)
            self.screen.blit(msg, msg.get_rect(center=panel.center))
            draw_hint_bar(self.screen, "ESC  Back", self.small_font, self.h, self.w)
            return

        row_h = self._s(40)
        visible = max(1, (panel.height - self._s(50)) // row_h)
        start_y = panel.y + self._s(44)

        scroll = max(0, self.song_idx - visible + 1)
        for i, song in enumerate(self.song_list):
            if i < scroll or i >= scroll + visible:
                continue
            row = i - scroll
            is_sel = (i == self.song_idx)
            rect = pygame.Rect(
                panel.x + self._s(10),
                start_y + row * row_h,
                panel.width - self._s(20),
                row_h - self._s(4),
            )

            if is_sel:
                bg = pygame.Surface(rect.size, pygame.SRCALPHA)
                pygame.draw.rect(bg, (*C_ACCENT, 18), (0, 0, *rect.size), border_radius=8)
                self.screen.blit(bg, rect.topleft)
                pygame.draw.rect(self.screen, (*C_ACCENT, 180), rect, 1, border_radius=8)

            txt = f"{song['dir']} / {song['file']}"
            color = C_ACCENT if is_sel else C_TEXT_PRIMARY
            s = self.small_font.render(txt, True, color)
            self.screen.blit(s, (rect.x + self._s(10), rect.centery - s.get_height() // 2))

        draw_hint_bar(
            self.screen, "UP/DOWN  Navigate    ENTER  Start    ESC  Back",
            self.small_font, self.h, self.w,
        )
