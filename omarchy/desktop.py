"""Set up Flatfind's Omarchy menu entries and keep Flatpak desktop exports visible."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import select
import stat
import subprocess
import sys
from tempfile import NamedTemporaryFile
import time


REPO = Path(__file__).resolve().parents[1]
HOME = Path.home()
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share")
APPLICATIONS = DATA_HOME / "applications"
EXPORT_LINKS = DATA_HOME / "flatfind/desktop-exports"
UWSM_SOURCE = REPO / "omarchy/uwsm/60-flatfind-flatpak.sh"
UWSM_DEST = CONFIG_HOME / "uwsm/env.d" / UWSM_SOURCE.name
MENU_FILE = HOME / ".config/omarchy/extensions/omarchy-menu.jsonc"
PLUGIN_PICKER = "$HOME/.config/omarchy/plugins/wfo.flatfind/flatfind"
LEGACY_PICKER = "$HOME/.local/bin/flatfind"
# Keep offsets in the original file and leave punctuation inside strings alone.
JSON_STRING = r'"(?:\\.|[^"\\])*"'
JSONC_COMMENTS = re.compile(JSON_STRING + r"|^[ \t]*//[^\r\n]*", re.M)
JSONC_TRAILING_COMMAS = re.compile(JSON_STRING + r"|,(?=\s*[}\]])")


def log(message):
    print(message, file=sys.stderr)


def object_members(text, opening):
    """Locate member values in a JSON object that has already been validated."""
    decode = json.JSONDecoder().raw_decode
    offset = opening + 1
    while not text[offset:].lstrip().startswith("}"):
        key_start = len(text) - len(text[offset:].lstrip())
        key, end = decode(text, key_start)
        rest = text[end:].lstrip()
        start = len(text) - len(rest[1:].lstrip())
        _, end = decode(text, start)
        yield key, (start, end)
        rest = text[end:].lstrip()
        if rest.startswith("}"):
            break
        offset = len(text) - len(rest) + 1


def menu_with_entries(original, entries):
    """Preserve custom rows and migrate the previously shipped Flatfind rows."""
    def mask(match):
        token = match[0]
        return token if token.startswith('"') else " " * len(token)

    clean = JSONC_TRAILING_COMMAS.sub(mask, JSONC_COMMENTS.sub(mask, original))
    if not clean.strip():
        return original + ("\n" if original and not original.endswith("\n") else "") + json.dumps(
            entries, ensure_ascii=False, indent=2
        ) + "\n"

    menu = json.loads(clean)
    if not isinstance(menu, dict):
        raise ValueError("the Omarchy menu extension must be a JSONC object")
    source = menu.get("items")
    opening = len(clean) - len(clean.lstrip())
    if isinstance(source, dict):
        opening, _ = dict(object_members(clean, opening))["items"]
    else:
        source = menu

    locations = dict(object_members(clean, opening))
    replacements = []
    for key, entry in entries.items():
        legacy = {name: value for name, value in entry.items() if name != "when"}
        legacy["action"] = legacy["action"].replace(PLUGIN_PICKER, LEGACY_PICKER)
        if source.get(key) == legacy:
            start, end = locations[key]
            replacements.append((start, end, json.dumps(entry, ensure_ascii=False)))
    for start, end, value in sorted(replacements, reverse=True):
        original = original[:start] + value + original[end:]

    missing = {key: value for key, value in entries.items() if key not in source}
    if not missing:
        return original
    rows = [f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}" for key, value in missing.items()]
    insertion = ",\n".join(rows) + ("," if source else "")
    return original[:opening + 1] + "\n" + insertion + "\n" + original[opening + 1:]


def install_menu():
    entries = json.loads((REPO / "omarchy-menu.jsonc").read_text(encoding="utf-8"))
    menu_path = MENU_FILE.resolve()
    menu_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        original = menu_path.read_bytes()
        mode = stat.S_IMODE(menu_path.stat().st_mode)
    except FileNotFoundError:
        original, mode = b"", 0o644
    updated = menu_with_entries(original.decode(), entries).encode()
    if updated == original:
        return
    with NamedTemporaryFile(dir=menu_path.parent, delete=False) as staged:
        staged_path = Path(staged.name)
        try:
            staged.write(updated)
            staged.close()
            staged_path.chmod(mode)
            if (menu_path.read_bytes() if menu_path.exists() else b"") != original:
                raise ValueError("the Omarchy menu extension changed during setup")
            staged_path.replace(menu_path)
        finally:
            staged_path.unlink(missing_ok=True)
    # FileView may have started before the user's extension directory existed.
    subprocess.run(["omarchy", "menu", "refresh"], timeout=4, capture_output=True, check=False)


def link_if_absent(path, target):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.symlink_to(target)
    except FileExistsError:
        pass


def installation_exports():
    user = Path(os.environ.get("FLATPAK_USER_DIR") or DATA_HOME / "flatpak")
    system = os.environ.get("FLATPAK_SYSTEM_DIR") or "/var/lib/flatpak"
    try:
        result = subprocess.run(
            ["flatpak", "--installations"], capture_output=True, text=True, check=True, timeout=3
        )
        systems = result.stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        systems = [system]
    roots = dict.fromkeys([user, *(Path(path) for path in systems if path.startswith("/"))])
    return tuple(root / "exports/share" for root in roots)


def directory_stamp(path):
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    return (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns)


def reconcile_exports(exports, native):
    # A visible native export already provides the canonical desktop id.
    visible = set()
    fallback = {}
    for export in exports:
        for entry in (export / "applications").glob("*.desktop"):
            if entry.is_file():
                if export in native:
                    visible.add(entry.name)
                else:
                    fallback.setdefault(entry.name, entry)
    needed = fallback.keys() - visible
    for link in EXPORT_LINKS.glob("*.desktop"):
        if not link.is_symlink() or link.name in needed:
            continue
        local = APPLICATIONS / link.name
        if local.is_symlink() and local.readlink() == link:
            local.unlink(missing_ok=True)
        link.unlink(missing_ok=True)
    for name in needed:
        source = fallback[name]
        link = EXPORT_LINKS / name
        if link.is_symlink() and link.readlink() != source:
            link.unlink(missing_ok=True)
        link_if_absent(link, source)
        if link.is_symlink() and link.readlink() == source:
            link_if_absent(APPLICATIONS / name, link)

    # Quickshell watches this directory on Omarchy. A directory change also
    # rescans Flatpak export dirs that were missing when Quickshell started.
    APPLICATIONS.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=APPLICATIONS, prefix=".flatfind-refresh-"):
        pass


def watch_exports():
    dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    native = {DATA_HOME, *(Path(path) for path in dirs.split(":") if path.startswith("/"))}
    last = None
    next_discovery = 0
    while True:
        if time.monotonic() >= next_discovery:
            exports = installation_exports()
            next_discovery = time.monotonic() + 60
        current = tuple((root, directory_stamp(root / "applications")) for root in exports)
        if current != last:
            reconcile_exports(exports, native)
            last = current
        # Keep no orphan watcher when the Quickshell process exits or reloads.
        if select.select([sys.stdin], [], [], 0.5)[0] and not sys.stdin.buffer.read1(1):
            return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleanup", action="store_true", help="remove Flatfind's launcher links and UWSM drop-in")
    args = parser.parse_args()
    EXPORT_LINKS.mkdir(parents=True, exist_ok=True)
    with (EXPORT_LINKS / ".lock").open("a") as lock:
        try:
            # A QML reload may start the replacement a moment before the old
            # Process has stopped. Wait for it, but give cleanup an immediate error.
            mode = fcntl.LOCK_EX | (fcntl.LOCK_NB if args.cleanup else 0)
            fcntl.flock(lock, mode)
        except BlockingIOError:
            raise SystemExit("Disable the Flatfind plugin before cleaning up its launcher links.")
        if args.cleanup:
            reconcile_exports((), set())
            if UWSM_DEST.is_symlink() and UWSM_DEST.readlink() == UWSM_SOURCE:
                UWSM_DEST.unlink(missing_ok=True)
            return
        try:
            link_if_absent(UWSM_DEST, UWSM_SOURCE)
        except OSError as error:
            log(f"could not install the UWSM drop-in: {error}")
        try:
            install_menu()
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            log(f"could not add the Omarchy menu entries: {error}")
        watch_exports()


if __name__ == "__main__":
    try:
        main()
    except OSError as error:
        raise SystemExit(str(error)) from error
