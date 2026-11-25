from contextlib import redirect_stderr
import gzip
import io
import os
import json
from pathlib import Path
import runpy
import shutil
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
            cache = Path(directory) / "flatpak-companion/flathub-x86_64.json"
            cache.parent.mkdir()
            cached = {REF: ["Cached name", "Cached summary"]}
            cache.write_text(json.dumps(cached), encoding="utf-8")
            with patch.dict(metadata.__globals__, flatpak_output=lambda *_: "x86_64"), \
                    patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["curl"], 12)), \
                    redirect_stderr(io.StringIO()):
                self.assertEqual(metadata(refresh=True), cached)
            self.assertEqual(json.loads(cache.read_text(encoding="utf-8")), cached)

    def test_default_installation_alias_lists_default_system_apps(self):
        output = "org.example.Test/x86_64/stable\tTest\t\t\treview\tsystem\n"
        with patch.object(sys, "argv", [str(PICKER), "remove", "--installation", "default"]), \
                patch("shutil.which", return_value="/usr/bin/tool"), \
                patch("subprocess.run", side_effect=[
                    subprocess.CompletedProcess([], 0, output),
                    subprocess.CompletedProcess([], 130, ""),
                ]) as run, self.assertRaises(SystemExit) as result:
            runpy.run_path(str(PICKER), run_name="__main__")
        self.assertEqual(result.exception.code, 0)
        self.assertIn("--system", run.call_args_list[0].args[0])
        self.assertIn("org.example.Test", run.call_args_list[1].kwargs["input"])
        self.assertEqual(run.call_count, 2)

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
    def test_add_and_remove_shipped_rows_while_preserving_custom_rows(self):
        update = desktop["menu_with_entries"]
        custom = {**ENTRIES["remove.flatpak"], "label": "My Flatpak menu"}
        original = (
            '{"items": {\n'
            '  // Keep this comment\n'
            '  "remove.flatpak": ' + json.dumps(custom) + '\n'
            '}}\n'
        )
        updated = update(original, ENTRIES)
        self.assertIn(json.dumps(ENTRIES["install.flatpak"], ensure_ascii=False), updated)
        self.assertEqual(update(updated, ENTRIES), updated)
        cleaned = update(updated, ENTRIES, cleanup=True)
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


    def test_removed_fallback_stays_hidden_until_app_is_uninstalled(self):
        reconcile = desktop["reconcile_exports"]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            exports = root / "exports"
            first = exports / "applications/org.example.Editor.desktop"
            first.parent.mkdir(parents=True)
            first.touch()
            local = root / "local"
            launcher = local / first.name
            with patch.dict(reconcile.__globals__, APPLICATIONS=local, EXPORT_LINKS=root / "links"):
                reconcile((exports,), set())
                launcher.unlink()
                second = first.with_name("org.example.Other.desktop")
                second.touch()
                reconcile((exports,), set())
                self.assertFalse(launcher.is_symlink())
                self.assertTrue((local / second.name).is_file())
                first.unlink()
                reconcile((exports,), set())
                first.touch()
                reconcile((exports,), set())
                self.assertTrue(launcher.is_file())

    def test_watcher_retries_a_failed_directory_scan(self):
        watch = desktop["watch_exports"]
        reconcile_calls = []
        with patch.dict(watch.__globals__, installation_exports=lambda: (Path("/test"),),
                        native_desktop_ids=lambda _: set(),
                        reconcile_exports=lambda *args: reconcile_calls.append(args)), \
                patch.dict(watch.__globals__, directory_stamp=unittest.mock.Mock(
                    side_effect=[PermissionError("unreadable"), (1, 2, 3, 4)])), \
                patch("time.monotonic", side_effect=[1, 2, 3]), \
                patch("select.select", side_effect=[([], [], []), ([], [], []), ([sys.stdin], [], [])]), \
                patch("sys.stdin", io.TextIOWrapper(io.BytesIO())), redirect_stderr(io.StringIO()):
            watch()
        self.assertEqual(reconcile_calls, [((Path("/test"),), set())])


class LanguageTests(unittest.TestCase):
    def test_next_picker_uses_omarchy_language_with_regional_fallback(self):
        with TemporaryDirectory() as directory:
            language = Path(directory) / "language"
            with patch.dict(os.environ, OMARCHY_LANGUAGE_FILE=str(language)):
                for code, expected in (("da-DK", "Søg i Flatpak"), ("en", "Search Flatpak")):
                    language.write_text(code)
                    fresh = runpy.run_path(str(PICKER))
                    self.assertEqual(fresh["tr"]("searchInstall"), expected)


class SetupTests(unittest.TestCase):
    def test_installed_cli_consent_and_cleanup(self):
        with TemporaryDirectory() as directory:
            home = Path(directory)
            tools = home / "bin"
            tools.mkdir()
            (tools / "omarchy-shell").write_text("#!/bin/sh\nexit 0\n")
            (tools / "omarchy").write_text(
                '#!/bin/sh\nprintf "%s\\n" "$*" >> "$HOME/calls"\n'
                'if [ "$1 $2" = "plugin list" ]; then '
                'echo \'[{"id":"wfoyt.flatpak-companion","enabled":true}]\'; fi\n'
            )
            (tools / "systemctl").write_text("#!/bin/sh\nexit 0\n")
            for tool in tools.iterdir():
                tool.chmod(0o755)
            env = {**os.environ, "HOME": str(home), "PATH": f"{tools}:{os.environ['PATH']}",
                   "XDG_CONFIG_HOME": str(home / ".config"), "XDG_DATA_HOME": str(home / "data")}

            def run(*args, answer="", check=True):
                return subprocess.run(args, input=answer, capture_output=True, text=True, env=env, check=check)

            run(ROOT / "install")
            cli = home / ".local/bin/flatpak-companion"
            helper = cli.resolve().parent / "omarchy/desktop.py"
            menu = home / ".config/omarchy/extensions/omarchy-menu.jsonc"
            login = home / ".config/uwsm/env.d/60-flatpak-companion.sh"
            marker = cli.resolve().parent / ".desktop-integration-enabled"
            self.assertIn("setup", run(cli, "--help").stdout)
            service = home / ".config/systemd/user/flatpak-companion-cleanup.service"
            generated_unit = service.read_bytes()
            service.write_text("# Existing user unit\n")
            result = run(sys.executable, helper, "--install-cli", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(service.read_text(), "# Existing user unit\n")
            service.write_bytes(generated_unit)
            cli.unlink()
            run(sys.executable, helper)  # Startup after a marketplace install.
            self.assertTrue(cli.is_symlink())
            for answer in ("", "\n", "no\n"):
                run(cli, "setup", answer=answer)
                self.assertFalse(menu.exists())
                self.assertFalse(login.is_symlink())
                self.assertFalse(marker.exists())
                self.assertFalse((home / "data/applications").exists())
            self.assertNotIn("plugin enable", (home / "calls").read_text())

            login.parent.mkdir(parents=True)
            login.write_text("existing login config")
            self.assertNotEqual(run(cli, "setup", answer="yes\n", check=False).returncode, 0)
            self.assertEqual(login.read_text(), "existing login config")
            self.assertFalse(menu.exists())
            login.unlink()

            menu.parent.mkdir(parents=True)
            custom = {"label": "My removal picker"}
            menu.write_text(json.dumps({"remove.flatpak": custom}))
            for _ in range(2):
                run(cli, "setup", answer="y\n")
                self.assertEqual(json.loads(menu.read_text()),
                                 {"install.flatpak": ENTRIES["install.flatpak"], "remove.flatpak": custom})
                self.assertTrue(login.resolve(strict=True).is_file())
                self.assertTrue(marker.is_file())
            self.assertEqual((home / "calls").read_text().count("plugin disable"), 1)
            installed = cli.resolve().parent
            saved_helper = home / "data/flatpak-companion/removal/desktop.py"
            self.assertIn(str(saved_helper), service.read_text())
            run(sys.executable, saved_helper, "--cleanup-removed", installed)
            self.assertTrue(marker.is_file())
            self.assertTrue(cli.is_symlink())
            shutil.rmtree(installed)
            run(sys.executable, saved_helper, "--cleanup-removed", installed)
            self.assertFalse(saved_helper.exists())
            self.assertFalse(service.exists())
            self.assertEqual(json.loads(menu.read_text()), {"remove.flatpak": custom})
            self.assertFalse(marker.exists())
            self.assertFalse(cli.is_symlink())
            self.assertFalse(login.is_symlink())
