# Seedmix Battle

A turn-based 1v1 battle mod for [Only4BMS](https://github.com/minwook-shin/only4bms),
inspired by SOUND VOLTEX's MEGAMIX BATTLE.

Instead of playing existing BMS charts, both players hit **procedurally
generated patterns** driven by a shared seed distributed by the server.
The whole 10-turn battle runs inside a single continuous session — no
song selection, no loading between turns, just a DJ-style BPM bridge
between one theme and the next.

## How it plays

- **10 turns, 1v1** — Each turn uses a different seed, BPM, and *theme*
  (pattern personality). The server hands out all 10 seeds up front so
  both clients generate identical charts locally.
- **4 themes** — `trill` (16th-note trills) / `longnote` (hold-heavy) /
  `chord` (2–3 key stacks) / `speed_change` (mid-turn BPM shifts).
- **Continuous play** — Between turns the lane playfield never clears.
  A 5-second bridge plays a DJ-mix kick pattern that interpolates from
  the previous BPM to the next — **speeding up** (ease-in, densifying
  drums, overshoot) if the next turn is faster, **slowing down**
  (ease-out, layers thinning) if it's slower.
- **Per-turn result overlay** — When a turn ends, a translucent result
  card fades in over the playfield during the bridge, showing both
  players' scores / accuracy / turn winner. Then the next turn's notes
  start dropping naturally into the receptors.
- **Side profile cards** — Player stats (live score, combo, accuracy)
  sit on side panels flanking the centered lane, SDVX-style. You only
  see your own lane — opponent state comes via the VS card.
- **Doublescore** — Win a turn by 50,000+ score and you take 2 points
  instead of 1.

## Modes

- **Solo Test** — Run the full 10-turn flow offline against a pseudo
  opponent. Same generator, same bridges, same HUD — useful for
  dogfooding the pattern generator and pacing.
- **Private Match** — Host an embedded socket.io server (default port
  `7215`) and invite a friend by IP. The host starts the battle; the
  server distributes seeds and relays per-turn scores.
- **Official Match** — Connect to a dedicated
  [only4bms-server](https://github.com/nicotina04/only4bms-server)
  instance. *(Not wired up yet — placeholder in the menu.)*

## What the server sends vs what the client generates

Following the
[only4bms-server battle protocol](https://github.com/nicotina04/only4bms-server/blob/main/documents/BACKEND_DESIGN.md)
(§9), the server is deliberately thin:

- **Server** hands out seeds, relays scores, arbitrates turn winners.
- **Client** generates charts, synthesizes samples, stitches the
  mega-chart, runs the playfield, and draws all UI.

No audio files ship with this mod. The first time you launch it, a
small drum + pentatonic synth palette (kick / snare / hat / sub-bass /
lead / arp / chord / FX) is synthesized into
`~/.cache/only4bms/seedmix_battle/samples/` with `numpy + wave` and
reused from then on.

## Installation

1. Clone (or download) this repository.
2. Copy or symlink the `seedmix_battle/` folder into your Only4BMS
   `mods/` directory:

   ```
   Only4BMS/
   ├── only4bms.exe
   └── mods/
       └── seedmix_battle/
           ├── __init__.py
           ├── menu.py
           ├── extension.py
           ├── pattern/
           └── ...
   ```

3. Launch Only4BMS — **Seedmix Battle** will appear in the main menu.

## Requirements

- Only4BMS 1.9.0+
- Python 3.10+
- `numpy`, `python-socketio` (both pulled in by Only4BMS itself)

## Layout

```
seedmix_battle/
├── __init__.py           mod entry point
├── menu.py               top menu + private lobby + battle runners
├── extension.py          RhythmGame extension: turn phase tracker, HUD, cards
├── screens.py            final BattleResultScreen
├── battle_client.py      socket.io client speaking the §9 battle protocol
├── private_server.py     embedded socket.io server (threading WSGI)
├── i18n.py               en / ko / ja strings
└── pattern/
    ├── generator.py      deterministic per-turn chart generator (4 themes)
    ├── battle_chart.py   mega-chart stitcher (intro + turns + bridges)
    ├── synth.py          numpy-based sample synthesizer + palette
    └── samples.py        cache facade
```

## License

MIT
