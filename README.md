# Seedmix Battle

A multiplayer battle mod for [Only4BMS](https://github.com/minwook-shin/only4bms).

Inspired by arcade rhythm game battle modes — players compete head-to-head on the same chart with live score sync.

## Features

- **Private Match** — Host a local server and invite a friend by IP. No external server needed.
- **Official Match** — Connect to a dedicated [only4bms-server](https://github.com/user/only4bms-server) for matchmaking and ranked play. *(Coming soon)*

## Installation

1. Download or clone this repository.
2. Copy (or symlink) the `seedmix_battle/` folder into your Only4BMS `mods/` directory:

```
Only4BMS/
├── only4bms.exe
└── mods/
    └── seedmix_battle/   ← this folder
        ├── __init__.py
        └── ...
```

3. Launch Only4BMS — **Seedmix Battle** will appear in the main menu.

## Requirements

- Only4BMS 1.9.0+
- Python 3.10+ (if running from source)

## License

MIT
