"""
Seedmix Battle — Multiplayer battle mod for Only4BMS
=====================================================
Head-to-head rhythm game battles with live score sync.

Two sub-modes:
  - Private Match: built-in WebSocket server, invite by IP
  - Official Match: connect to a dedicated only4bms-server instance
"""

MOD_ID = "seedmix_battle"
MOD_NAME = "Seedmix Battle"
MOD_DESCRIPTION = "Head-to-head multiplayer battle with private or official servers."
MOD_VERSION = "0.1.0"

MOD_UPDATE_URL = ""  # Set to GitHub release URL when available


def get_display_name() -> str:
    from .i18n import t as _t
    return _t("menu_seedmix_battle")


def run(settings, renderer, window, **ctx):
    from .menu import SeedmixMenu

    menu = SeedmixMenu(settings, renderer, window, **ctx)
    menu.run()
