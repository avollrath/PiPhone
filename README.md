# PiPhone

Rotary-phone audio player for a Raspberry Pi. Lifting the handset starts a
random local poem. Replacing it stops playback. Played poems are persisted so
each poem is selected once before a new random cycle begins.

## Pi Setup

Copy `poem_player.py` and downloaded `poems/` directory to the Pi.

```bash
sudo apt update
sudo apt install -y mpg123 python3-gpiozero
```

Example wiring:

- Hook switch contact: BCM GPIO 17, physical pin 11
- Other hook switch contact: GND, physical pin 9

Run:

```bash
python3 poem_player.py \
  --local-dir poems \
  --handset-gpio 17 \
  --handset-inverted \
  --volume 70
```

`--handset-inverted` is for a hook switch that closes while the handset is
down. Omit it when the switch closes while lifted.

Useful options:

```text
--min-words 20
--max-words 100
--translated
--not-translated
--reset-played
--audio-device default
--player-verbose
```

Playback progress is stored in `poems/.playback-state.json` by default.
Deleting that file or using `--reset-played` starts a new cycle.

## Automatic Startup

Create `/etc/systemd/system/poem-player.service`:

```ini
[Unit]
Description=PiPhone poem player
After=local-fs.target sound.target

[Service]
Type=simple
User=admin
WorkingDirectory=/home/admin
ExecStart=/usr/bin/python3 /home/admin/poem_player.py --local-dir /home/admin/poems --handset-gpio 17 --handset-inverted --volume 70
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now poem-player.service
sudo journalctl -u poem-player.service -f
```

## Downloading Poems

Downloading is separate from Pi playback. It can run on another computer, then
the resulting directory can be copied to the Pi.

```bash
python -m pip install -r requirements-downloader.txt
cp .env.example .env
```

Configure `.env` for a compatible search API and poem site. The downloader
expects an Algolia-style multi-query response containing
`results[0].hits`. Endpoint, credentials, index, query parameters, response
field names, page base URL, audio selector, and fallback audio URL regex are
all configured through `POEM_*` environment variables.

Download or extend a collection:

```bash
python poem_downloader.py --output-dir poems --max-words 100
```

Existing manifest entries are skipped by ID or page URL. Each successful
download is added to `manifest.json` immediately, so interrupted runs resume
without downloading completed poems again.

Test configuration without downloading:

```bash
python poem_downloader.py --test-only
```

The `poems/` directory and `.env` are intentionally ignored by Git.
