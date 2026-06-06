import argparse
import html
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


@dataclass(frozen=True)
class DownloadConfig:
    api_url: str
    api_index: str
    api_query: dict[str, str]
    search_params: dict[str, object]
    page_base_url: str
    title_field: str
    authors_field: str
    content_field: str
    uri_field: str
    fallback_uri_field: str
    id_field: str
    translated_by_field: str
    translation_title_field: str
    audio_selector: str
    audio_url_regex: str
    test_page_url: str | None
    hits_per_page: int
    fetch_delay_seconds: float
    browser_profile: str


@dataclass(frozen=True)
class Poem:
    poem_id: str
    title: str
    authors: tuple[str, ...]
    page_url: str
    word_count: int
    is_translated: bool


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def parse_json_env(name: str, default: str = "{}") -> dict:
    value = os.environ.get(name, default)
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must contain a JSON object")
    return parsed


def load_config() -> DownloadConfig:
    return DownloadConfig(
        api_url=require_env("POEM_API_URL"),
        api_index=require_env("POEM_API_INDEX"),
        api_query=parse_json_env("POEM_API_QUERY_JSON"),
        search_params=parse_json_env("POEM_SEARCH_PARAMS_JSON"),
        page_base_url=require_env("POEM_PAGE_BASE_URL"),
        title_field=os.environ.get("POEM_TITLE_FIELD", "title"),
        authors_field=os.environ.get("POEM_AUTHORS_FIELD", "authors"),
        content_field=os.environ.get("POEM_CONTENT_FIELD", "content"),
        uri_field=os.environ.get("POEM_URI_FIELD", "uri"),
        fallback_uri_field=os.environ.get("POEM_FALLBACK_URI_FIELD", "uri"),
        id_field=os.environ.get("POEM_ID_FIELD", "id"),
        translated_by_field=os.environ.get("POEM_TRANSLATED_BY_FIELD", "translated_by"),
        translation_title_field=os.environ.get("POEM_TRANSLATION_TITLE_FIELD", "translation_title"),
        audio_selector=os.environ.get("POEM_AUDIO_SELECTOR", "audio[src]"),
        audio_url_regex=os.environ.get(
            "POEM_AUDIO_URL_REGEX",
            r"https?://[^\s\"'<>]+\.mp3(?:\?[^\s\"'<>]+)?",
        ),
        test_page_url=os.environ.get("POEM_TEST_PAGE_URL"),
        hits_per_page=int(os.environ.get("POEM_HITS_PER_PAGE", "100")),
        fetch_delay_seconds=float(os.environ.get("POEM_FETCH_DELAY_SECONDS", "1.5")),
        browser_profile=os.environ.get("POEM_BROWSER_PROFILE", "chrome124"),
    )


def count_words(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def safe_filename(text: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._")
    return name[:120] or "poem"


def build_search_params(
    config: DownloadConfig,
    page: int,
) -> str:
    params = dict(config.search_params)
    params["hitsPerPage"] = config.hits_per_page
    params["page"] = page
    return urlencode(params)


def fetch_all_poems(
    config: DownloadConfig,
    min_words: int | None,
    max_words: int | None,
    translated: bool | None,
) -> list[Poem]:
    poems = []
    page = 0

    while True:
        payload = {
            "requests": [
                {
                    "indexName": config.api_index,
                    "params": build_search_params(config, page),
                }
            ]
        }
        response = requests.post(
            config.api_url,
            params=config.api_query,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()

        hits = response.json()["results"][0].get("hits", [])
        for hit in hits:
            uri = hit.get(config.uri_field) or hit.get(config.fallback_uri_field)
            if not uri:
                continue

            word_count = count_words(hit.get(config.content_field) or "")
            if min_words is not None and word_count < min_words:
                continue
            if max_words is not None and word_count > max_words:
                continue

            is_translated = bool(
                hit.get(config.translated_by_field)
                or hit.get(config.translation_title_field)
            )
            if translated is not None and is_translated != translated:
                continue

            page_url = urljoin(config.page_base_url.rstrip("/") + "/", str(uri).lstrip("/"))
            poem_id = str(hit.get(config.id_field) or page_url)
            poems.append(
                Poem(
                    poem_id=poem_id,
                    title=hit.get(config.title_field) or "Untitled",
                    authors=tuple(hit.get(config.authors_field) or ()),
                    page_url=page_url,
                    word_count=word_count,
                    is_translated=is_translated,
                )
            )

        print(f"Fetched page {page}: {len(hits)} hits, kept {len(poems)} total")
        if len(hits) < config.hits_per_page:
            break

        page += 1
        time.sleep(config.fetch_delay_seconds)

    random.shuffle(poems)
    return poems


def fetch_page(url: str, browser_profile: str) -> tuple[int, str]:
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=30,
        )
    else:
        response = cffi_requests.get(
            url,
            impersonate=browser_profile,
            timeout=30,
        )
    return response.status_code, response.text


def extract_audio_url(
    page_html: str,
    selector: str,
    url_regex: str,
) -> str | None:
    soup = BeautifulSoup(page_html, "html.parser")
    selected_audio = soup.select_one(selector)
    if selected_audio and selected_audio.get("src"):
        return selected_audio["src"]

    for audio in soup.select("audio[src]"):
        src = audio.get("src")
        if src:
            return src

    match = re.search(url_regex, page_html)
    return html.unescape(match.group(0)) if match else None


def resolve_audio_url(config: DownloadConfig, page_url: str) -> str | None:
    status_code, page_html = fetch_page(page_url, config.browser_profile)
    if status_code != 200:
        print(f"Page fetch failed with status {status_code}: {page_url}")
        return None
    return extract_audio_url(
        page_html,
        selector=config.audio_selector,
        url_regex=config.audio_url_regex,
    )


def test_audio_extraction(config: DownloadConfig) -> bool:
    if not config.test_page_url:
        return True
    print(f"Testing audio extraction: {config.test_page_url}")
    audio_url = resolve_audio_url(config, config.test_page_url)
    if audio_url:
        print(f"PASS: {audio_url}")
        return True
    print("FAIL: Could not extract audio URL")
    return False


def download_file(url: str, output_path: Path) -> None:
    temporary_path = output_path.with_suffix(output_path.suffix + ".part")
    with requests.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        with temporary_path.open("wb") as output_file:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    output_file.write(chunk)
    temporary_path.replace(output_path)


def load_manifest(manifest_path: Path) -> list[dict]:
    if not manifest_path.exists():
        return []
    with manifest_path.open("r", encoding="utf-8") as manifest_file:
        return json.load(manifest_file)


def write_manifest(manifest_path: Path, manifest: list[dict]) -> None:
    temporary_path = manifest_path.with_suffix(".json.tmp")
    with temporary_path.open("w", encoding="utf-8") as manifest_file:
        json.dump(manifest, manifest_file, ensure_ascii=False, indent=2)
    temporary_path.replace(manifest_path)


def run_download(
    config: DownloadConfig,
    output_dir: Path,
    min_words: int | None,
    max_words: int | None,
    translated: bool | None,
    limit: int | None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    manifest = load_manifest(manifest_path)
    existing_keys = {
        str(value)
        for item in manifest
        for value in (item.get("id"), item.get("url"))
        if value
    }

    poems = fetch_all_poems(
        config,
        min_words=min_words,
        max_words=max_words,
        translated=translated,
    )
    poems = [
        poem
        for poem in poems
        if poem.poem_id not in existing_keys and poem.page_url not in existing_keys
    ]
    if limit is not None:
        poems = poems[:limit]

    start_index = len(manifest) + 1
    for index, poem in enumerate(poems, start=start_index):
        authors = "_".join(poem.authors) if poem.authors else "Unknown"
        filename = (
            f"{index:04d}_{safe_filename(authors)}_"
            f"{safe_filename(poem.title)}.mp3"
        )
        output_path = output_dir / filename

        print(f"Resolving: {poem.title} - {', '.join(poem.authors)}")
        audio_url = resolve_audio_url(config, poem.page_url)
        time.sleep(config.fetch_delay_seconds)
        if not audio_url:
            print("Skipping: audio URL not found")
            continue

        print(f"Downloading: {filename}")
        download_file(audio_url, output_path)
        manifest.append(
            {
                "id": poem.poem_id,
                "title": poem.title,
                "authors": list(poem.authors),
                "url": poem.page_url,
                "word_count": poem.word_count,
                "is_translated": poem.is_translated,
                "audio_file": filename,
            }
        )
        write_manifest(manifest_path, manifest)

    print(f"Wrote {manifest_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download poems into a local manifest.")
    parser.add_argument("--env-file", default=".env", help="environment file path")
    parser.add_argument("--output-dir", default="poems", help="download directory")
    parser.add_argument("--test-only", action="store_true", help="only test extraction")
    parser.add_argument("--limit", type=int, help="maximum new poems to download")
    parser.add_argument("--min-words", type=int, help="minimum poem word count")
    parser.add_argument("--max-words", type=int, help="maximum poem word count")
    translation_group = parser.add_mutually_exclusive_group()
    translation_group.add_argument("--translated", action="store_true", help="only translated poems")
    translation_group.add_argument("--not-translated", action="store_true", help="only non-translated poems")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.limit is not None and args.limit < 0:
        print("--limit must be zero or greater")
        return 1

    load_env_file(Path(args.env_file))
    try:
        config = load_config()
    except (ValueError, json.JSONDecodeError) as error:
        print(f"Configuration error: {error}")
        return 1

    if not test_audio_extraction(config):
        return 1
    if args.test_only:
        return 0

    translated = None
    if args.translated:
        translated = True
    elif args.not_translated:
        translated = False

    run_download(
        config,
        output_dir=Path(args.output_dir),
        min_words=args.min_words,
        max_words=args.max_words,
        translated=translated,
        limit=args.limit,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
