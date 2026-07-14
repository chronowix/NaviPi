#!/usr/bin/env python3

import sys
import time
from pathlib import Path
from pyfiglet import Figlet

import requests
from mutagen import File as MutagenFile

import glob
import os

try:
    import readline
except ImportError:
    readline = None


LRCLIB_BASE = "https://lrclib.net/api"
AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".wma"}
USER_AGENT = "lrclib-fetcher-script/1.0 (perso)"
API_PAUSE = 0.3
DEFAULT_PATH = "/home/chrono/Music/"

class PathCompleter:
    def __init__(self, base_path=None, directories_only=False):
        self.base_path = base_path
        self.directories_only = directories_only

    def __call__(self, text, state):
        if state == 0:
            self.matches = self.get_matches(text)
        try:
            return self.matches[state]
        except IndexError:
            return None

    def get_matches(self, text):
        expanded = os.path.expanduser(text)
        is_absolute = os.path.isabs(expanded) or text.startswith('~')
        
        # relative path
        if not is_absolute and self.base_path:
            base = os.path.expanduser(self.base_path)
            search_prefix = os.path.join(base, expanded)
            matches = glob.glob(search_prefix + '*')
            results = []
            for m in matches:
                is_dir = os.path.isdir(m)
                if self.directories_only and not is_dir:
                    continue
                rel = os.path.relpath(m, base)
                if is_dir:
                    rel += '/'
                results.append(rel)
            return results
        
        # absolute path
        else:
            matches = glob.glob(expanded + '*')
            results = []
            for m in matches:
                is_dir = os.path.isdir(m)
                if self.directories_only and not is_dir:
                    continue
                display_path = m
                if text.startswith('~'):
                    home = os.path.expanduser('~')
                    if display_path.startswith(home):
                        display_path = '~' + display_path[len(home):]
                if is_dir:
                    display_path += '/'
                results.append(display_path)
            return results



def extract_metadata(audio_path: Path):
    # Lit métadonnées du titre via mutagen
    try:
        audio = MutagenFile(audio_path, easy=True)
    except Exception as e:
        print(f"  ⚠️  Can't read tags : {e}")
        return None

    if audio is None:
        return None

    def get_tag(cle):
        values = audio.get(cle)
        return values[0] if values else None

    title = get_tag("title")
    artist = get_tag("artist")
    album = get_tag("album")
    duration = None
    if audio.info is not None and hasattr(audio.info, "duration"):
        duration = int(round(audio.info.duration))

    if not title or not artist:
        return None

    return {
        "title": title,
        "artist": artist,
        "album": album,
        "duration": duration,
    }


def find_lyrics_get(meta: dict):
    # Try search via /api/get
    if not meta["duration"]:
        return None

    params = {
        "artist_name": meta["artist"],
        "track_name": meta["title"],
        "duration": meta["duration"],
    }
    if meta["album"]:
        params["album_name"] = meta["album"]

    try:
        r = requests.get(
            f"{LRCLIB_BASE}/get",
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
    except requests.RequestException as e:
        print(f"  ⚠️  Erreur réseau (get) : {e}")
    return None


def find_lyrics_search(meta: dict):
    # Fallback : search via /api/search if /get fails
    params = {
        "track_name": meta["title"],
        "artist_name": meta["artist"],
    }
    try:
        r = requests.get(
            f"{LRCLIB_BASE}/search",
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        if r.status_code == 200:
            results = r.json()
            if results:
                # Take first result
                for res in results:
                    if res.get("syncedLyrics") or res.get("plainLyrics"):
                        return res
    except requests.RequestException as e:
        print(f"  ⚠️  Network error on (search) : {e}")
    return None


def save_lrc(audio_path: Path, content: str):
    lrc_path = audio_path.with_suffix(".lrc")
    lrc_path.write_text(content, encoding="utf-8")
    return lrc_path


def process_file(audio_path: Path, overwrite: bool, pause: float):
    lrc_path = audio_path.with_suffix(".lrc")
    print(f"\n🎵 {audio_path.name}")

    if lrc_path.exists() and not overwrite:
        print("  ↪️  .lrc already present, ignored (use the overwrite command).")
        return "skip"

    meta = extract_metadata(audio_path)
    if meta is None:
        print("  ⚠️  Insufficient metadata, ignored.")
        return "no_meta"

    print(f"  → {meta['artist']} - {meta['title']}"
          + (f" ({meta['album']})" if meta["album"] else ""))

    result = find_lyrics_get(meta)
    if result is None:
        result = find_lyrics_search(meta)

    if result is None:
        print("  ❌ No lyrics found on lrclib.")
        return "not_found"

    if result.get("instrumental"):
        print("  🎼 Instrumental track.")
        return "instrumental"

    lyrics = result.get("syncedLyrics") or result.get("plainLyrics")
    if not lyrics:
        print("  ❌ Result found but no lyrics found in DB.")
        return "not_found"

    type_lyrics = "synchronisées" if result.get("syncedLyrics") else "texte brut"

    final_path = save_lrc(audio_path, lyrics)
    print(f"  ✅ Paroles {type_lyrics} sauvegardées → {final_path.name}")

    time.sleep(pause)  # petite pause pour rester correct envers l'API
    return "found"


def scan_dir(dir: Path, overwrite: bool, pause: float):
    files = sorted(
        p for p in dir.rglob("*")
        if p.suffix.lower() in AUDIO_EXTENSIONS and p.is_file()
    )

    if not files:
        print("No audio file found in this directory.")
        return

    print(f"📂 {len(files)} audio file(s) found in {dir}\n")

    stats = {"found": 0, "not_found": 0, "skip": 0, "no_meta": 0, "instrumental": 0}

    for path in files:
        result = process_file(path, overwrite, pause)
        stats[result] = stats.get(result, 0) + 1

    print("\n" + "=" * 40)
    print("Summary :")
    print(f"  ✅ Paroles trouvées      : {stats['found']}")
    print(f"  ❌ Non trouvées          : {stats['not_found']}")
    print(f"  🎼 Instrumentales        : {stats['instrumental']}")
    print(f"  ↪️  Déjà présentes (skip) : {stats['skip']}")
    print(f"  ⚠️  Métadonnées absentes : {stats['no_meta']}\n")


def main():
    if readline:
        # On retire le caractère '/' des délimiteurs pour que readline passe le chemin entier au compléteur
        readline.set_completer_delims(' \t\n;')
        readline.parse_and_bind("tab: complete")

    f = Figlet(font='slant')
    print(f.renderText('Lrc Digger'))

    
    # directory for full album lyrics scan, fetch for specific audio file lyrics search
    while True:
        print("1. Directory scan")
        print("2. Overwrite directory scan")
        print("3. Fetch specific lyrics file")
        print("4. Overwrite existing specific lyrics file")
        print("Q. Quit")
        
        if readline:
            readline.set_completer(None)

        
        choice = input("\n>>> ").strip()
        
        match choice:
            case "1":
                if readline:
                    readline.set_completer(PathCompleter(DEFAULT_PATH, directories_only=True))
                path = input("Directory path : ").strip()
                if not path:
                    continue
                dir = (Path(DEFAULT_PATH) / Path(path).expanduser()).resolve()
                while not dir.is_dir():
                    print(f"Error : '{dir}' isn't a valid directory.")
                    if readline:
                        readline.set_completer(PathCompleter(DEFAULT_PATH, directories_only=True))
                    path = input("Directory path (or press Enter to cancel): ").strip()
                    if not path:
                        break
                    dir = (Path(DEFAULT_PATH) / Path(path).expanduser()).resolve()
                    
                if path:
                    scan_dir(dir, overwrite=False, pause=API_PAUSE)
                
            case "2":
                if readline:
                    readline.set_completer(PathCompleter(DEFAULT_PATH, directories_only=True))
                path = input("Directory path : ").strip()
                if not path:
                    continue
                dir = (Path(DEFAULT_PATH) / Path(path).expanduser()).resolve()
                while not dir.is_dir():
                    print(f"Error : '{dir}' isn't a valid directory.")
                    if readline:
                        readline.set_completer(PathCompleter(DEFAULT_PATH, directories_only=True))
                    path = input("Directory path (or press Enter to cancel): ").strip()
                    if not path:
                        break
                    dir = (Path(DEFAULT_PATH) / Path(path).expanduser()).resolve()
                    
                if path:
                    scan_dir(dir, overwrite=True, pause=API_PAUSE)
                    
            case "3":
                if readline:
                    readline.set_completer(PathCompleter(DEFAULT_PATH, directories_only=True))
                path = input("Audio file path (or press Enter to cancel): ").strip()
                if not path:
                    continue
                file = (Path(DEFAULT_PATH) / Path(path).expanduser()).resolve()
                while not file.is_file() or file.suffix.lower() not in AUDIO_EXTENSIONS:
                    if not file.is_file():
                        print(f"Error : '{file}' isn't a valid file.")
                    else:
                        print(f"Error : '{file}' isn't a supported audio file.")
                        print(f"Supported extensions  : {', '.join(sorted(AUDIO_EXTENSIONS))}")
                    if readline:
                        readline.set_completer(PathCompleter(DEFAULT_PATH, directories_only=True))
                    
                    path = input("Audio file path (or press Enter to cancel): ").strip()
                    if not path:
                        break
                    file = (Path(DEFAULT_PATH) / Path(path).expanduser()).resolve()
                
                if path:
                    process_file(file, overwrite=False, pause=API_PAUSE)

                
            case "4":
                if readline:
                    readline.set_completer(PathCompleter(DEFAULT_PATH, directories_only=True))
                path = input("Audio file path (or press Enter to cancel): ").strip()
                if not path:
                    continue
                file = (Path(DEFAULT_PATH) / Path(path).expanduser()).resolve()
                while not file.is_file() or file.suffix.lower() not in AUDIO_EXTENSIONS:
                    if not file.is_file():
                        print(f"Error : '{file}' isn't a valid file.")
                    else:
                        print(f"Error : '{file}' isn't a supported audio file.")
                        print(f"Supported extensions : {', '.join(sorted(AUDIO_EXTENSIONS))}")
                    if readline:
                        readline.set_completer(PathCompleter(DEFAULT_PATH, directories_only=True))
                    path = input("Audio file path (or press Enter to cancel): ").strip()
                    if not path:
                        break
                    file = (Path(DEFAULT_PATH) / Path(path).expanduser()).resolve()
                
                if path:
                    process_file(file, overwrite=True, pause=API_PAUSE)

                
            case "q":
                sys.exit(0)
                
            case _:
                print("Invalid input, please try again")
        


if __name__ == "__main__":
    main()
