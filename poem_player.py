import argparse
import json
import random
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


@dataclass(frozen=True)
class Poem:
    poem_id: str
    title: str
    authors: tuple[str, ...]
    audio_path: Path
    word_count: int
    is_translated: bool


@dataclass(frozen=True)
class HandsetSwitch:
    button: object
    inverted: bool

    @property
    def is_lifted(self) -> bool:
        return not self.button.is_active if self.inverted else self.button.is_active

    def wait_for_lift(self) -> None:
        if self.inverted:
            self.button.wait_for_release()
        else:
            self.button.wait_for_press()

    def wait_for_down(self) -> None:
        if self.inverted:
            self.button.wait_for_press()
        else:
            self.button.wait_for_release()


class PlaybackState:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.played_ids = self._load()

    def _load(self) -> set[str]:
        if not self.path.exists():
            return set()
        with self.path.open("r", encoding="utf-8") as state_file:
            data = json.load(state_file)
        return set(data.get("played_ids") or ())

    def mark_played(self, poem_id: str) -> None:
        self.played_ids.add(poem_id)
        self._save()

    def reset(self) -> None:
        self.played_ids.clear()
        self._save()

    def reset_poems(self, poem_ids: set[str]) -> None:
        self.played_ids.difference_update(poem_ids)
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary_path.open("w", encoding="utf-8") as state_file:
            json.dump(
                {"played_ids": sorted(self.played_ids)},
                state_file,
                indent=2,
            )
        temporary_path.replace(self.path)


def load_manifest(
    local_dir: Path,
    min_words: int | None = None,
    max_words: int | None = None,
    translated: bool | None = None,
) -> list[Poem]:
    manifest_path = local_dir / "manifest.json"
    with manifest_path.open("r", encoding="utf-8") as manifest_file:
        items = json.load(manifest_file)

    poems = []
    for item in items:
        word_count = int(item.get("word_count") or 0)
        is_translated = bool(item.get("is_translated"))
        if min_words is not None and word_count < min_words:
            continue
        if max_words is not None and word_count > max_words:
            continue
        if translated is not None and is_translated != translated:
            continue

        audio_file = item["audio_file"]
        audio_path = (local_dir / audio_file).resolve()
        if not audio_path.is_file():
            print(f"Skipping missing audio file: {audio_path}")
            continue

        poem_id = str(item.get("id") or item.get("url") or audio_file)
        poems.append(
            Poem(
                poem_id=poem_id,
                title=item.get("title") or "Untitled",
                authors=tuple(item.get("authors") or ()),
                audio_path=audio_path,
                word_count=word_count,
                is_translated=is_translated,
            )
        )

    return poems


def choose_unplayed_poem(poems: list[Poem], state: PlaybackState) -> Poem:
    unplayed = [poem for poem in poems if poem.poem_id not in state.played_ids]
    if not unplayed:
        print("All poems played once. Starting a new random cycle.", flush=True)
        state.reset_poems({poem.poem_id for poem in poems})
        unplayed = poems
    return random.choice(unplayed)


def build_player_command(
    audio_path: Path,
    quiet: bool = True,
    audio_device: str | None = None,
    volume_percent: int | None = None,
    telephone_effect: bool = False,
) -> list[str]:
    if telephone_effect:
        if not shutil.which("sox"):
            raise RuntimeError(
                "sox is not installed. Run: "
                "sudo apt install sox libsox-fmt-mp3"
            )

        command = ["sox"]
        if quiet:
            command.append("-q")
        if volume_percent is not None:
            command.extend(["-v", str(volume_percent / 100)])
        command.append(str(audio_path))
        command.extend(["-t", "alsa", audio_device or "default"])
        command.extend(["highpass", "300", "lowpass", "3400", "rate", "8000"])
        return command

    if not shutil.which("mpg123"):
        raise RuntimeError("mpg123 is not installed. Run: sudo apt install mpg123")

    command = ["mpg123"]
    if quiet:
        command.append("-q")
    if volume_percent is not None:
        command.extend(["-f", str(round(32768 * volume_percent / 100))])
    if audio_device:
        command.extend(["-o", "alsa", "-a", audio_device])
    command.append(str(audio_path))
    return command


def play_until_handset_down(
    poem: Poem,
    handset_switch: HandsetSwitch,
    quiet: bool,
    audio_device: str | None,
    volume_percent: int | None,
    telephone_effect: bool,
) -> None:
    process = subprocess.Popen(
        build_player_command(
            poem.audio_path,
            quiet=quiet,
            audio_device=audio_device,
            volume_percent=volume_percent,
            telephone_effect=telephone_effect,
        )
    )
    while process.poll() is None:
        if not handset_switch.is_lifted:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            return
        time.sleep(0.1)

    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, process.args)


def print_poem(poem: Poem) -> None:
    authors = ", ".join(poem.authors) if poem.authors else "Unknown author"
    print(f"\n{poem.title} - {authors}", flush=True)


def create_handset_switch(
    gpio_pin: int,
    active_low: bool,
    inverted: bool,
) -> HandsetSwitch:
    from gpiozero import Button

    return HandsetSwitch(
        button=Button(gpio_pin, pull_up=active_low, bounce_time=0.05),
        inverted=inverted,
    )


def play_handset_mode(
    poems: list[Poem],
    state: PlaybackState,
    handset_switch: HandsetSwitch,
    quiet: bool,
    audio_device: str | None,
    volume_percent: int | None,
    telephone_effect: bool,
) -> None:
    while True:
        poem = choose_unplayed_poem(poems, state)
        print("\nWaiting for handset lift...", flush=True)
        handset_switch.wait_for_lift()

        print_poem(poem)
        state.mark_played(poem.poem_id)
        try:
            play_until_handset_down(
                poem,
                handset_switch,
                quiet=quiet,
                audio_device=audio_device,
                volume_percent=volume_percent,
                telephone_effect=telephone_effect,
            )
        except subprocess.CalledProcessError as error:
            print(f"mpg123 failed with exit code {error.returncode}")

        if handset_switch.is_lifted:
            handset_switch.wait_for_down()


def play_standard_mode(
    poems: list[Poem],
    state: PlaybackState,
    repeat: bool,
    limit: int | None,
    dry_run: bool,
    quiet: bool,
    audio_device: str | None,
    volume_percent: int | None,
    telephone_effect: bool,
) -> None:
    played_this_run = 0
    while limit is None or played_this_run < limit:
        poem = choose_unplayed_poem(poems, state)
        print_poem(poem)
        state.mark_played(poem.poem_id)

        if dry_run:
            print(f"Dry run: {poem.audio_path}")
        else:
            try:
                subprocess.run(
                    build_player_command(
                        poem.audio_path,
                        quiet=quiet,
                        audio_device=audio_device,
                        volume_percent=volume_percent,
                        telephone_effect=telephone_effect,
                    ),
                    check=True,
                )
            except subprocess.CalledProcessError as error:
                print(f"mpg123 failed with exit code {error.returncode}")

        played_this_run += 1
        if not repeat and all(poem.poem_id in state.played_ids for poem in poems):
            return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Play poems from a local manifest.")
    parser.add_argument("--local-dir", default="poems", help="directory containing manifest.json and MP3 files")
    parser.add_argument("--state-file", help="played-state file; defaults inside local directory")
    parser.add_argument("--reset-played", action="store_true", help="start a new playback cycle")
    parser.add_argument("--repeat", action="store_true", help="continue after current cycle finishes")
    parser.add_argument("--limit", type=int, help="maximum poems to play during this run")
    parser.add_argument("--dry-run", action="store_true", help="print local audio paths without playing")
    parser.add_argument("--player-verbose", action="store_true", help="show mpg123 output")
    parser.add_argument("--audio-device", help="ALSA device, for example default or plughw:0,0")
    parser.add_argument("--volume", type=int, help="playback volume percent")
    parser.add_argument(
        "--telephone-effect",
        action="store_true",
        help="band-limit audio to 300-3400 Hz and resample to 8 kHz using SoX",
    )
    parser.add_argument("--handset-gpio", type=int, help="BCM GPIO pin connected to handset hook switch")
    parser.add_argument("--handset-active-high", action="store_true", help="configure GPIO with pull-down")
    parser.add_argument("--handset-inverted", action="store_true", help="switch is closed while handset is down")
    parser.add_argument("--min-words", type=int, help="skip poems shorter than this word count")
    parser.add_argument("--max-words", type=int, help="skip poems longer than this word count")
    translation_group = parser.add_mutually_exclusive_group()
    translation_group.add_argument("--translated", action="store_true", help="only translated poems")
    translation_group.add_argument("--not-translated", action="store_true", help="only non-translated poems")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.volume is not None and not 0 <= args.volume <= 100:
        print("--volume must be between 0 and 100")
        return 1
    if args.limit is not None and args.limit < 0:
        print("--limit must be zero or greater")
        return 1

    translated = None
    if args.translated:
        translated = True
    elif args.not_translated:
        translated = False

    local_dir = Path(args.local_dir)
    poems = load_manifest(
        local_dir,
        min_words=args.min_words,
        max_words=args.max_words,
        translated=translated,
    )
    if not poems:
        print("No playable poems found in manifest")
        return 1

    state_path = Path(args.state_file) if args.state_file else local_dir / ".playback-state.json"
    state = PlaybackState(state_path)
    if args.reset_played:
        state.reset()

    if args.handset_gpio is not None:
        try:
            handset_switch = create_handset_switch(
                args.handset_gpio,
                active_low=not args.handset_active_high,
                inverted=args.handset_inverted,
            )
        except ImportError:
            print("gpiozero is not installed. Run: sudo apt install python3-gpiozero")
            return 1

        play_handset_mode(
            poems,
            state,
            handset_switch,
            quiet=not args.player_verbose,
            audio_device=args.audio_device,
            volume_percent=args.volume,
            telephone_effect=args.telephone_effect,
        )
        return 0

    play_standard_mode(
        poems,
        state,
        repeat=args.repeat,
        limit=args.limit,
        dry_run=args.dry_run,
        quiet=not args.player_verbose,
        audio_device=args.audio_device,
        volume_percent=args.volume,
        telephone_effect=args.telephone_effect,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
