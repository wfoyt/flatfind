from contextlib import redirect_stderr
import gzip
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PICKER = ROOT / "flatpak-companion"
picker = runpy.run_path(str(PICKER))
desktop = runpy.run_path(str(ROOT / "omarchy/desktop.py"))
ENTRIES = json.loads((ROOT / "omarchy-menu.jsonc").read_text(encoding="utf-8"))
REF = "app/org.example.FlatpakCompanion/x86_64/stable"
CATALOG = f"""<components><component>
    <name xml:lang="da">Dansk navn</name>
    <name>Flatpak Companion Test</name>
    <summary>Test summary</summary>
    <bundle type="flatpak">{REF}</bundle>
    </component></components>"""


class CatalogTests(unittest.TestCase):
    def test_catalog_uses_unlocalized_fields_and_bounds_transfer(self):
        download = picker["download_flathub_metadata"]
        response = subprocess.CompletedProcess([], 0, gzip.compress(CATALOG.encode()))
        with patch("subprocess.run", return_value=response) as run:
            self.assertEqual(download("x86_64"), {REF: ("Flatpak Companion Test", "Test summary")})
        args = run.call_args.args[0]
        self.assertEqual(args[:2], ["curl", "-q"])
        self.assertEqual(args[-1], "https://dl.flathub.org/repo/appstream/x86_64/appstream.xml.gz")
        self.assertIn("--max-filesize", args)
        self.assertIn("--max-time", args)
        self.assertTrue(run.call_args.kwargs["check"])

    def test_decoded_limit_and_deadline(self):
        download = picker["download_flathub_metadata"]
        response = subprocess.CompletedProcess([], 0, gzip.compress(CATALOG.encode()))
        with patch("subprocess.run", return_value=response):
            with patch.dict(download.__globals__, APPSTREAM_XML_LIMIT=len(CATALOG) - 1):
                with self.assertRaisesRegex(ValueError, "decoded size limit"):
                    download("x86_64")
            with patch("time.monotonic", side_effect=[0, picker["APPSTREAM_TIMEOUT"] + 1]):
                with self.assertRaisesRegex(ValueError, "processing timed out"):
                    download("x86_64")

    def test_catalog_rejects_document_types(self):
        xml = "<!DOCTYPE components>" + CATALOG
        response = subprocess.CompletedProcess([], 0, gzip.compress(xml.encode()))
        with patch("subprocess.run", return_value=response):
            with self.assertRaisesRegex(ValueError, "document type"):
                picker["download_flathub_metadata"]("x86_64")

    def test_catalog_uses_stale_metadata_after_download_failure(self):
        metadata = picker["flathub_metadata"]
        with TemporaryDirectory() as directory, patch.dict("os.environ", XDG_CACHE_HOME=directory):
            cache = Path(directory) / "flatfind/flathub-x86_64.json"
            cache.parent.mkdir()
            cached = {REF: ["Cached name", "Cached summary"]}
            cache.write_text(json.dumps(cached), encoding="utf-8")
            with patch.dict(metadata.__globals__, flatpak_output=lambda *_: "x86_64"), \
                    patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["curl"], 12)), \
                    redirect_stderr(io.StringIO()):
                self.assertEqual(metadata(refresh=True), cached)
            self.assertEqual(json.loads(cache.read_text(encoding="utf-8")), cached)

    def test_remove_list_timeout_has_a_cli_error(self):
        stderr = io.StringIO()
        with patch.object(sys, "argv", [str(PICKER), "remove"]), \
                patch("shutil.which", return_value="/usr/bin/tool"), \
                patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["flatpak", "list"], 30)), \
                redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_error:
            runpy.run_path(str(PICKER), run_name="__main__")
        self.assertEqual(exit_error.exception.code, 1)
        self.assertEqual(stderr.getvalue(), "flatpak-companion: Flatpak list timed out after 30 seconds\n")


class MenuTests(unittest.TestCase):
    def test_migrate_shipped_rows_and_preserve_custom_rows_on_cleanup(self):
        update = desktop["menu_with_entries"]
        executable = '$HOME/.config/omarchy/plugins/wfo.flatfind/flatfind'
        action = f'xdg-terminal-exec --app-id=org.omarchy.terminal "{executable}"'
        legacy = {"icon": "󰏓", "label": "Flatpak", "after": "install.package",
                  "action": action.replace(executable, "$HOME/.local/bin/flatfind")}
        previous = {**legacy, "action": action, "when": f'[[ -x "{executable}" ]]'}
        custom = {**ENTRIES["remove.flatpak"], "label": "My Flatpak menu"}
        guarded = {
            name: value.replace("wfo.flatfind/flatpak-companion", "wfo.flatfind/flatfind")
            for name, value in ENTRIES["install.flatpak"].items()
        }
        for old_entry in (guarded, previous, legacy):
            with self.subTest(old_entry=old_entry):
                original = (
                    '{"items": {\n'
                    '  "remove.flatpak": ' + json.dumps(custom) + ',\n'
                    '  // Keep this comment\n'
                    '  "install.flatpak": ' + json.dumps(old_entry) + '\n'
                    '}}\n'
                )
                migrated = update(original, ENTRIES)
                self.assertIn(json.dumps(ENTRIES["install.flatpak"], ensure_ascii=False), migrated)
                self.assertEqual(update(migrated, ENTRIES), migrated)
                cleaned = update(migrated, ENTRIES, cleanup=True)
                self.assertIn("// Keep this comment", cleaned)
                self.assertEqual(json.loads(cleaned.replace("// Keep this comment", "")), {
                    "items": {"remove.flatpak": custom},
                })
                self.assertEqual(update(cleaned, ENTRIES, cleanup=True), cleaned)

    def test_cleanup_of_all_shipped_rows_handles_trailing_comma(self):
        original = json.dumps(ENTRIES)[:-1] + ",\n}"
        self.assertEqual(json.loads(desktop["menu_with_entries"](original, ENTRIES, cleanup=True)), {})


class LauncherTests(unittest.TestCase):
    def test_native_desktop_ids_take_precedence_over_fallback_links(self):
        reconcile = desktop["reconcile_exports"]
        scan = desktop["native_desktop_ids"]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            user, system, exports = root / "user", root / "system", root / "exports"
            local = user / "applications"
            export = exports / "applications/group-Editor.desktop"
            export.parent.mkdir(parents=True)
            export.touch()
            launcher = local / export.name
            with patch.dict(scan.__globals__, APPLICATIONS=local, EXPORT_LINKS=root / "links"):
                roots = (user, system)
                reconcile((exports,), scan(roots))
                self.assertEqual(launcher.resolve(strict=True), export)
                self.assertEqual(scan(roots), set())

                nested = system / "applications/group"
                nested.parent.mkdir(parents=True)
                # Follow aliases without traversing directory cycles.
                real = root / "native"
                real.mkdir()
                nested.symlink_to(real, target_is_directory=True)
                (real / "Editor.desktop").touch()
                (real / "loop").symlink_to(nested, target_is_directory=True)
                self.assertEqual(scan(roots), {export.name})
                reconcile((exports,), scan(roots))
                self.assertFalse(launcher.is_symlink())

                (real / "Editor.desktop").unlink()
                launcher.write_text("user override", encoding="utf-8")
                reconcile((exports,), scan(roots))
                self.assertEqual(launcher.read_text(encoding="utf-8"), "user override")
