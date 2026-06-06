# PiPhone

Rotary-phone audio player for a Raspberry Pi. Lifting the handset starts a
random local poem. Replacing it stops playback. Played poems are persisted so
each poem is selected once before a new random cycle begins.

## Pi Setup

Copy `poem_player.py` and downloaded `poems/` directory to the Pi.

```bash
sudo apt update
sudo apt install -y mpg123 python3-gpiozero sox libsox-fmt-mp3
```

Example wiring:

- Hook switch contact: BCM GPIO 17, physical pin 11
- Other hook switch contact: GND, physical pin 9
- Status LED anode through 220 ohm resistor: BCM GPIO 27, physical pin 13
- Status LED cathode: GND

The default configuration uses `poems/`, BCM GPIO 17, an inverted hook switch,
telephone audio processing, 20% volume, and a ready LED on BCM GPIO 27:

```bash
python3 poem_player.py
```

`--handset-inverted` is for a hook switch that closes while the handset is
down. Use `--no-handset-inverted` when the switch closes while lifted.

Useful options:

```text
--min-words 20
--max-words 100
--translated
--not-translated
--reset-played
--audio-device default
--player-verbose
--no-telephone-effect
--no-handset
--status-led-gpio 27
--no-status-led
```

`--telephone-effect` uses SoX to restrict playback to 300-3400 Hz and resample
to 8 kHz, approximating traditional telephone audio.

The status LED remains off during startup, turns on when the player is ready
for handset lifts, and turns off when the process stops.

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
SupplementaryGroups=audio gpio
WorkingDirectory=/home/admin
ExecStartPre=/bin/sh -c 'until /usr/bin/aplay -l 2>/dev/null | /usr/bin/grep -q sndrpihifiberry; do sleep 1; done'
ExecStart=/usr/bin/python3 /home/admin/poem_player.py --local-dir /home/admin/poems --audio-device plughw:CARD=sndrpihifiberry,DEV=0
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

Stop the service before running the player manually, otherwise both processes
will compete for the same GPIO:

```bash
sudo systemctl stop poem-player.service
python3 poem_player.py
sudo systemctl start poem-player.service
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
