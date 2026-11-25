"""Set up Flatpak Companion's Omarchy menu entries and keep Flatpak desktop exports visible."""

import argparse
from collections import Counter
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
CLI = HOME / ".local/bin/flatpak-companion"
INSTALLED = HOME / ".config/omarchy/plugins/wfoyt.flatpak-companion"
# Keep consent with this installation so removing the plugin also revokes it.
CONSENT = INSTALLED / ".desktop-integration-enabled"
APPLICATIONS = DATA_HOME / "applications"
STATE_DIR = DATA_HOME / "flatpak-companion"
EXPORT_LINKS = STATE_DIR / "desktop-exports"
UWSM_SOURCE = INSTALLED / "omarchy/uwsm/60-flatpak-companion.sh"
UWSM_DEST = CONFIG_HOME / "uwsm/env.d" / UWSM_SOURCE.name
UNIT = "flatpak-companion-cleanup"
UNIT_HEADER = "# Managed by Flatpak Companion\n"
UNIT_DIR = CONFIG_HOME / "systemd/user"
CLEANUP_DIR = STATE_DIR / "removal"
MENU_FILE = HOME / ".config/omarchy/extensions/omarchy-menu.jsonc"
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
        yield key, (key_start, start, end)
        rest = text[end:].lstrip()
        if rest.startswith("}"):
            break
        offset = len(text) - len(rest) + 1


def menu_with_entries(original, entries, *, cleanup=False):
    """Add or remove plugin rows while preserving custom rows and comments."""
    def mask(match):
        token = match[0]
        return token if token.startswith('"') else " " * len(token)

    uncommented = JSONC_COMMENTS.sub(mask, original)
    clean = JSONC_TRAILING_COMMAS.sub(mask, uncommented)
    if not clean.strip():
        if cleanup:
            return original
        return original + ("\n" if original and not original.endswith("\n") else "") + json.dumps(
            entries, ensure_ascii=False, indent=2
        ) + "\n"

    menu = json.loads(clean)
    if not isinstance(menu, dict):
        raise ValueError("the Omarchy menu extension must be a JSONC object")
    source = menu.get("items")
    opening = len(clean) - len(clean.lstrip())
    if isinstance(source, dict):
        _, opening, _ = dict(object_members(clean, opening))["items"]
    else:
        source = menu

    if cleanup:
        members = list(object_members(clean, opening))
        # Leave ambiguous duplicate keys alone, including a user's earlier row.
        key_counts = Counter(key for key, _ in members)
        removals = []
        removed_tail = False
        for index, (key, (key_start, _, end)) in enumerate(reversed(members)):
            shipped = key in entries and key_counts[key] == 1 and source[key] == entries[key]
            if shipped:
                removals.append((key_start, end))
            # Remove the trailing separator as well when deleting the last rows.
            if shipped or removed_tail:
                rest = uncommented[end:].lstrip()
                if rest.startswith(","):
                    comma = len(uncommented) - len(rest)
                    removals.append((comma, comma + 1))
            removed_tail = shipped and (removed_tail or index == 0)
        for start, end in sorted(removals, reverse=True):
            original = original[:start] + original[end:]
        return original

    missing = {key: value for key, value in entries.items() if key not in source}
    if not missing:
        return original
    rows = [f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}" for key, value in missing.items()]
    insertion = ",\n".join(rows) + ("," if source else "")
    return original[:opening + 1] + "\n" + insertion + "\n" + original[opening + 1:]


def update_menu(*, cleanup=False, entries_file=REPO / "omarchy-menu.jsonc"):
    entries = json.loads(entries_file.read_text(encoding="utf-8"))
    menu_path = MENU_FILE.resolve()
    try:
        original = menu_path.read_bytes()
        mode = stat.S_IMODE(menu_path.stat().st_mode)
    except FileNotFoundError:
        original, mode = b"", 0o644
    updated = menu_with_entries(original.decode(), entries, cleanup=cleanup).encode()
    if updated == original:
        return
    menu_path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=menu_path.parent, delete=False) as staged:
        staged_path = Path(staged.name)
        try:
            staged.write(updated)
            staged.close()
            staged_path.chmod(mode)
            if (menu_path.read_bytes() if menu_path.exists() else b"") != original:
                raise ValueError("the Omarchy menu extension changed during the update")
            staged_path.replace(menu_path)
        finally:
            staged_path.unlink(missing_ok=True)
    # FileView may have started before the user's extension directory existed.
    try:
        subprocess.run(["omarchy", "menu", "refresh"], timeout=4, capture_output=True, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        log(f"could not refresh the Omarchy menu: {error}")


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


def links_to(path, target):
    try:
        return path.readlink() == target
    except OSError:
        return False


def desktop_files(directory, ancestors=frozenset()):
    try:
        info = directory.stat()
        identity = (info.st_dev, info.st_ino)
        if identity in ancestors:
            return
        with os.scandir(directory) as entries:
            for entry in entries:
                try:
                    is_directory = entry.is_dir()
                except OSError:
                    continue
                if is_directory:
                    yield from desktop_files(Path(entry.path), ancestors | {identity})
                elif entry.name.endswith(".desktop"):
                    yield Path(entry.path)
    except OSError:
        return


def native_desktop_ids(roots):
    ids = set()
    for root in roots:
        applications = root / "applications"
        for entry in desktop_files(applications):
            if entry.parent == APPLICATIONS and links_to(entry, EXPORT_LINKS / entry.name):
                continue
            # XDG derives desktop IDs from the relative path, including subdirectories.
            ids.add("-".join(entry.relative_to(applications).parts))
    return ids


def reconcile_exports(exports, visible):
    fallback = {}
    for export in exports:
        try:
            with os.scandir(export / "applications") as entries:
                for entry in entries:
                    if entry.name.endswith(".desktop") and entry.is_file():
                        fallback.setdefault(entry.name, Path(entry.path))
        except FileNotFoundError:
            continue
    needed = fallback.keys() - visible
    for link in EXPORT_LINKS.glob("*.desktop"):
        if not link.is_symlink() or link.name in needed:
            continue
        local = APPLICATIONS / link.name
        if links_to(local, link):
            local.unlink(missing_ok=True)
        elif link.name in fallback and not os.path.lexists(local):
            # An Apps menu removal deleted the local launcher. Remember it
            # while the app remains installed by retaining the private link.
            continue
        link.unlink(missing_ok=True)
    for name in needed:
        source = fallback[name]
        link = EXPORT_LINKS / name
        local = APPLICATIONS / name
        if link.is_symlink() and not os.path.lexists(local):
            continue
        if link.exists() and not link.is_symlink():
            continue
        link_if_absent(local, link)
        if links_to(local, link):
            if link.is_symlink() and not links_to(link, source):
                link.unlink(missing_ok=True)
            link_if_absent(link, source)

    # Quickshell watches this directory on Omarchy. A directory change also
    # rescans Flatpak export dirs that were missing when Quickshell started.
    APPLICATIONS.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=APPLICATIONS, prefix=".flatpak-companion-refresh-"):
        pass


def watch_exports():
    dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    native = {DATA_HOME, *(Path(path) for path in dirs.split(":") if path.startswith("/"))}
    last = None
    last_exports = None
    next_discovery = 0
    next_native_scan = 0
    retry_at = 0
    while True:
        now = time.monotonic()
        if now >= next_discovery:
            exports = installation_exports()
            next_discovery = now + 60
        if now >= retry_at:
            try:
                export_stamps = tuple((root, directory_stamp(root / "applications")) for root in exports)
                if export_stamps != last_exports or now >= next_native_scan:
                    visible = native_desktop_ids(native)
                    current = (export_stamps, visible)
                    if current != last:
                        reconcile_exports(exports, visible)
                        last = current
                    next_native_scan = now + 2
                    last_exports = export_stamps
            except OSError as error:
                log(f"could not sync Flatpak launchers, retrying: {error}")
                retry_at = now + 2
        # Keep no orphan watcher when the Quickshell process exits or reloads.
        if select.select([sys.stdin], [], [], 0.5)[0] and not sys.stdin.buffer.read1(1):
            return


def register_cleanup():
    # Omarchy removes the plugin checkout without invoking a plugin hook.
    # Keep a copy of just the cleanup inputs beside the plugin's launcher state.
    def unit_path(path):
        return str(path).replace("%", "%%")

    def unit_quote(path):
        return json.dumps(unit_path(path), ensure_ascii=False)

    path_unit = UNIT_DIR / (UNIT + ".path")
    service_unit = UNIT_DIR / (UNIT + ".service")
    path_config = UNIT_HEADER + (
        "[Unit]\nDescription=Watch for Flatpak Companion removal\n"
        f"[Path]\nPathChanged={unit_path(INSTALLED.parent)}\n"
        "[Install]\nWantedBy=default.target\n"
    )
    service_config = UNIT_HEADER + (
        "[Unit]\nDescription=Clean up after Flatpak Companion removal\n"
        f"ConditionPathExists=!{unit_path(INSTALLED / 'manifest.json')}\n"
        "[Service]\nType=oneshot\n"
        f"Environment={unit_quote(f'XDG_CONFIG_HOME={CONFIG_HOME}')} "
        f"{unit_quote(f'XDG_DATA_HOME={DATA_HOME}')}\n"
        "ExecStart=:/usr/bin/python3 "
        f"{unit_quote(CLEANUP_DIR / 'desktop.py')} --cleanup-removed {unit_quote(INSTALLED)}\n"
        "[Install]\nWantedBy=default.target\n"
    )
    for unit in (path_unit, service_unit):
        if os.path.lexists(unit) and not owns_unit(unit):
            raise ValueError(f"An existing file at {unit} prevents cleanup registration.")
    files = {
        CLEANUP_DIR / "desktop.py": Path(__file__).read_bytes(),
        CLEANUP_DIR / "omarchy-menu.jsonc": (REPO / "omarchy-menu.jsonc").read_bytes(),
        path_unit: path_config.encode(),
        service_unit: service_config.encode(),
    }
    for target, contents in files.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=target.parent, delete=False) as staged:
            staged_path = Path(staged.name)
            try:
                staged.write(contents)
                staged.close()
                staged_path.replace(target)
            finally:
                staged_path.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", path_unit.name, service_unit.name],
                   check=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


def owns_unit(unit):
    if unit.is_symlink() or not unit.is_file():
        return False
    with unit.open("rb") as stream:
        return stream.read(len(UNIT_HEADER)) == UNIT_HEADER.encode()


def unregister_cleanup():
    path_unit = UNIT_DIR / (UNIT + ".path")
    service_unit = UNIT_DIR / (UNIT + ".service")
    units = [unit for unit in (path_unit, service_unit) if owns_unit(unit)]
    if path_unit in units:
        subprocess.run(["systemctl", "--user", "stop", path_unit.name], check=True, timeout=10)
    if units:
        subprocess.run(["systemctl", "--user", "disable", *(unit.name for unit in units)],
                       check=True, timeout=10)
        for unit in units:
            unit.unlink(missing_ok=True)
    for name in ("desktop.py", "omarchy-menu.jsonc"):
        (CLEANUP_DIR / name).unlink(missing_ok=True)
    if units:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, timeout=10)


def cleanup(source, entries_file):
    (source / CONSENT.name).unlink(missing_ok=True)
    for path, target in ((CLI, source / "flatpak-companion"),
                         (UWSM_DEST, source / "omarchy/uwsm" / UWSM_DEST.name)):
        if links_to(path, target):
            path.unlink(missing_ok=True)
    if any(EXPORT_LINKS.glob("*.desktop")):
        reconcile_exports((), set())
    update_menu(cleanup=True, entries_file=entries_file)
    unregister_cleanup()


def install_cli():
    if INSTALLED.resolve() != REPO:
        raise ValueError("Install the plugin under its manifest ID, wfoyt.flatpak-companion.")
    target = INSTALLED / "flatpak-companion"
    if os.path.lexists(CLI) and not links_to(CLI, target):
        raise ValueError(f"A different command exists at {CLI}. Use {target} directly.")
    register_cleanup()
    link_if_absent(CLI, target)


def setup():
    if INSTALLED.resolve() != REPO:
        raise ValueError("Run flatpak-companion setup from the wfoyt.flatpak-companion installation.")
    print(f"Add Flatpak rows to {MENU_FILE}, link {UWSM_DEST} for future logins, "
          f"and enable wfoyt.flatpak-companion to maintain launcher links in {APPLICATIONS}.")
    try:
        answer = input("Set up Omarchy integration? [y/N] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        print("Setup cancelled.")
        return
    if os.path.lexists(UWSM_DEST) and not links_to(UWSM_DEST, UWSM_SOURCE):
        raise ValueError(f"An existing file at {UWSM_DEST} prevents setup.")
    subprocess.run(["omarchy-shell", "shell", "rescanPlugins"], check=True, timeout=5)
    for _ in range(40):
        result = subprocess.run(["omarchy", "plugin", "list", "--json"], check=True,
                                capture_output=True, text=True, timeout=5)
        plugin = next((p for p in json.loads(result.stdout) if p.get("id") == "wfoyt.flatpak-companion"), None)
        if plugin is not None:
            break
        time.sleep(0.05)
    else:
        raise ValueError("Omarchy has not discovered Flatpak Companion. Retry setup in your Omarchy session.")
    register_cleanup()
    update_menu()
    link_if_absent(UWSM_DEST, UWSM_SOURCE)
    if not links_to(UWSM_DEST, UWSM_SOURCE):
        raise ValueError(f"Could not install the login link at {UWSM_DEST}.")
    # On first setup an already enabled service has finished its CLI-only startup.
    if not CONSENT.is_file():
        if plugin.get("enabled"):
            subprocess.run(["omarchy", "plugin", "disable", "wfoyt.flatpak-companion"], check=True, timeout=10)
        with CONSENT.open("x"):
            pass
    subprocess.run(["omarchy", "plugin", "enable", "wfoyt.flatpak-companion"], check=True, timeout=10)
    print("Flatpak Companion desktop integration is enabled.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--install-cli", action="store_true")
    actions.add_argument("--setup", action="store_true")
    actions.add_argument("--cleanup", action="store_true", help="remove Flatpak Companion's menu entries, launcher links, and UWSM drop-in")
    actions.add_argument("--cleanup-removed", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.setup:
        setup()
        return
    if args.install_cli:
        install_cli()
        print(f"Installed {CLI}. Run flatpak-companion setup for Omarchy integration.")
        return
    if not (args.cleanup or args.cleanup_removed):
        try:
            install_cli()
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            log(getattr(error, "stderr", None) or error)
        if not CONSENT.is_file():
            return
    EXPORT_LINKS.mkdir(parents=True, exist_ok=True)
    with (EXPORT_LINKS / ".lock").open("a") as lock:
        try:
            # A QML reload may start the replacement a moment before the old
            # Process has stopped. Wait for it, but give cleanup an immediate error.
            mode = fcntl.LOCK_EX | (fcntl.LOCK_NB if args.cleanup else 0)
            fcntl.flock(lock, mode)
        except BlockingIOError:
            raise SystemExit("Disable the Flatpak Companion plugin before cleaning up its launcher links.")
        if args.cleanup_removed:
            if not (INSTALLED / "manifest.json").exists():
                cleanup(args.cleanup_removed, Path(__file__).with_name("omarchy-menu.jsonc"))
            return
        if args.cleanup:
            if INSTALLED.resolve() != REPO:
                raise ValueError("Run cleanup from the installed plugin after disabling it.")
            cleanup(INSTALLED, REPO / "omarchy-menu.jsonc")
            return
        watch_exports()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(getattr(error, "stderr", None) or str(error)) from error
