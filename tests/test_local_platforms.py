# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""The local server on Windows, macOS and Linux: the API key, Documents and CLI paths.

The credential store, the platform and the environment are all mocked, so these run
the same on any OS.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from keyring.errors import KeyringLocked, NoKeyringError

import local_paths
from artcraft_api import ArtCraftAPIError, get_api_key
from craft_app_mcp import PHOTOCRAFT_VERSION, PhotoCraftBridge
from craft_sessions import load_apps
from pdfcraft_mcp import PDFCRAFT_VERSION, _pdfcraft_parameters

PLATFORMS = ("win32", "darwin", "linux")
README = Path(__file__).resolve().parent.parent / "README.md"


def fake_store(items: dict[tuple[str, str], str]):
    def get_password(service, account):
        return items.get((service, account))
    return get_password


@patch.dict(os.environ, {"ARTCRAFT_API_KEY": ""})
@patch("artcraft_api.getpass.getuser", return_value="ada")
class ApiKeyTests(unittest.TestCase):
    def test_environment_variable_wins_without_touching_the_store(self, _user):
        with (
            patch.dict(os.environ, {"ARTCRAFT_API_KEY": " env-key \n"}),
            patch("keyring.get_password") as get_password,
            patch("artcraft_api.subprocess.run") as run,
        ):
            self.assertEqual(get_api_key(), "env-key")
        get_password.assert_not_called()
        run.assert_not_called()

    def test_reads_the_os_credential_store_on_every_platform(self, _user):
        store = fake_store({("artcraft-plugin", "ada"): "stored-key\n"})
        for platform in PLATFORMS:
            with (
                self.subTest(platform=platform),
                patch("artcraft_api.sys.platform", platform),
                patch("keyring.get_password", side_effect=store),
                patch("artcraft_api.subprocess.run") as run,
            ):
                self.assertEqual(get_api_key(), "stored-key")
                run.assert_not_called()

    def test_reads_the_legacy_item_name_from_the_store(self, _user):
        store = fake_store({("artcraft-chatgpt-mcp", "ada"): "legacy-key"})
        for platform in PLATFORMS:
            with (
                self.subTest(platform=platform),
                patch("artcraft_api.sys.platform", platform),
                patch("keyring.get_password", side_effect=store) as get_password,
            ):
                self.assertEqual(get_api_key(), "legacy-key")
                self.assertEqual(
                    [call.args for call in get_password.call_args_list],
                    [("artcraft-plugin", "ada"), ("artcraft-chatgpt-mcp", "ada")],
                )

    def test_macos_still_reads_keychain_items_keyring_misses(self, _user):
        with (
            patch("artcraft_api.sys.platform", "darwin"),
            patch("keyring.get_password", return_value=None),
            patch(
                "artcraft_api.subprocess.run",
                return_value=SimpleNamespace(stdout="keychain-key\n"),
            ) as run,
        ):
            self.assertEqual(get_api_key(), "keychain-key")
        command = run.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/security")
        self.assertEqual(command[command.index("-a") + 1], "ada")
        self.assertEqual(command[command.index("-s") + 1], "artcraft-plugin")

    def test_macos_reads_keychain_when_keyring_is_not_installed(self, _user):
        with (
            patch("artcraft_api.sys.platform", "darwin"),
            patch.dict(sys.modules, {"keyring": None}),
            patch(
                "artcraft_api.subprocess.run",
                side_effect=[
                    subprocess.CalledProcessError(44, "security"),
                    SimpleNamespace(stdout="legacy-key\n"),
                ],
            ),
        ):
            self.assertEqual(get_api_key(), "legacy-key")

    def test_windows_and_linux_never_run_the_macos_tool(self, _user):
        for platform, store_name in (("win32", "Windows Credential Manager"), ("linux", "system keyring")):
            with (
                self.subTest(platform=platform),
                patch("artcraft_api.sys.platform", platform),
                patch("keyring.get_password", return_value=None),
                patch("artcraft_api.subprocess.run") as run,
            ):
                with self.assertRaisesRegex(ArtCraftAPIError, store_name) as raised:
                    get_api_key()
                run.assert_not_called()
                self.assertIn("'artcraft-plugin'", str(raised.exception))
                self.assertIn("'ada'", str(raised.exception))

    def test_a_system_without_a_store_points_at_the_environment_variable(self, _user):
        with (
            patch("artcraft_api.sys.platform", "linux"),
            patch("keyring.get_password", side_effect=NoKeyringError("no backend")),
        ):
            with self.assertRaisesRegex(ArtCraftAPIError, "ARTCRAFT_API_KEY"):
                get_api_key()

    def test_a_locked_store_is_treated_as_missing(self, _user):
        with (
            patch("artcraft_api.sys.platform", "win32"),
            patch("keyring.get_password", side_effect=KeyringLocked("locked")),
        ):
            with self.assertRaisesRegex(ArtCraftAPIError, "No ArtCraft API key was found"):
                get_api_key()

    def test_an_empty_stored_key_is_refused(self, _user):
        with (
            patch("artcraft_api.sys.platform", "linux"),
            patch("keyring.get_password", return_value="  \n"),
        ):
            with self.assertRaisesRegex(ArtCraftAPIError, "empty"):
                get_api_key()


class DocumentsAndCliPathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        patcher = patch("local_paths.Path.home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_user_dirs(self, line: str) -> None:
        config = self.home / ".config"
        config.mkdir(exist_ok=True)
        (config / "user-dirs.dirs").write_text(f"# written by xdg-user-dirs-update\n{line}\n")

    def test_cli_paths_per_platform(self):
        cases = {
            "win32": (
                {"LOCALAPPDATA": str(self.home / "AppData" / "Local")},
                self.home / "AppData" / "Local" / "artcraft-cli" / "pdfcraft" / "0.15.0" / "pdfcraft-cli.exe",
            ),
            "darwin": (
                {"XDG_DATA_HOME": str(self.home / "ignored")},
                self.home / ".local" / "share" / "artcraft-cli" / "pdfcraft" / "0.15.0" / "pdfcraft-cli",
            ),
            "linux": (
                {"XDG_DATA_HOME": str(self.home / "data")},
                self.home / "data" / "artcraft-cli" / "pdfcraft" / "0.15.0" / "pdfcraft-cli",
            ),
        }
        for platform, (environment, expected) in cases.items():
            with (
                self.subTest(platform=platform),
                patch("local_paths.sys.platform", platform),
                patch.dict(os.environ, environment),
            ):
                self.assertEqual(local_paths.craft_cli_path("pdfcraft", "0.15.0"), expected)

    def test_windows_without_localappdata_uses_the_profile_folder(self):
        with patch("local_paths.sys.platform", "win32"), patch.dict(os.environ, {"LOCALAPPDATA": ""}):
            self.assertEqual(local_paths.data_dir(), self.home / "AppData" / "Local")

    def test_linux_ignores_a_relative_xdg_data_home(self):
        with patch("local_paths.sys.platform", "linux"), patch.dict(os.environ, {"XDG_DATA_HOME": "data"}):
            self.assertEqual(local_paths.data_dir(), self.home / ".local" / "share")

    def test_windows_uses_the_known_documents_folder(self):
        onedrive = self.home / "OneDrive" / "Documents"
        with (
            patch("local_paths.sys.platform", "win32"),
            patch("local_paths._windows_documents", return_value=onedrive),
        ):
            self.assertEqual(local_paths.documents_dir(), onedrive)

    def test_windows_falls_back_to_the_profile_documents_folder(self):
        with patch("local_paths.sys.platform", "win32"):
            # Off Windows there is no shell32, so the lookup itself returns None.
            self.assertEqual(local_paths.documents_dir(), self.home / "Documents")

    def test_linux_reads_the_localised_documents_folder(self):
        self.write_user_dirs('XDG_DOCUMENTS_DIR="$HOME/Dokumente"')
        with patch("local_paths.sys.platform", "linux"), patch.dict(os.environ, {"XDG_CONFIG_HOME": ""}):
            self.assertEqual(local_paths.documents_dir(), self.home / "Dokumente")

    def test_linux_falls_back_when_user_dirs_is_missing_or_points_home(self):
        with patch("local_paths.sys.platform", "linux"), patch.dict(os.environ, {"XDG_CONFIG_HOME": ""}):
            self.assertEqual(local_paths.documents_dir(), self.home / "Documents")
            self.write_user_dirs('XDG_DOCUMENTS_DIR="$HOME/"')
            self.assertEqual(local_paths.documents_dir(), self.home / "Documents")
            self.write_user_dirs('XDG_DOCUMENTS_DIR="relative/docs"')
            self.assertEqual(local_paths.documents_dir(), self.home / "Documents")

    def test_macos_uses_home_documents(self):
        self.write_user_dirs('XDG_DOCUMENTS_DIR="$HOME/Elsewhere"')
        with patch("local_paths.sys.platform", "darwin"):
            self.assertEqual(local_paths.documents_dir(), self.home / "Documents")


class PinnedCliTests(unittest.TestCase):
    def test_local_pins_match_the_hosted_studio_and_the_readme(self):
        readme = README.read_text()
        for app, version in (("pdfcraft", PDFCRAFT_VERSION), ("photocraft", PHOTOCRAFT_VERSION)):
            with self.subTest(app=app):
                self.assertEqual(version, load_apps()[app].version)
                for asset in (
                    f"{app}-{version}-windows-x64-portable.zip",
                    f"{app}-{version}-windows-arm64-portable.zip",
                    f"{app}-cli-{version}-macos-universal.zip",
                    f"{app}-{version}-linux-x86_64.tar.gz",
                    f"{app}-{version}-linux-aarch64.tar.gz",
                ):
                    self.assertTrue(asset in readme, f"README does not name {asset}")
                for checksum in _linux_checksums(app):
                    self.assertTrue(checksum in readme, f"README lacks the {app} checksum {checksum}")

    def test_pdfcraft_is_confined_to_documents(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Documents"
            root.mkdir()
            cli_path = Path(temporary) / "pdfcraft-cli"
            cli_path.touch()
            cli_path.chmod(0o755)
            with (
                patch("pdfcraft_mcp.PDFCRAFT_ROOT", root),
                patch.dict(os.environ, {"PDFCRAFT_CLI_PATH": str(cli_path)}),
            ):
                parameters = _pdfcraft_parameters()
            resolved = str(root.resolve())
        self.assertEqual(parameters.command, str(cli_path))
        self.assertEqual(
            parameters.args,
            [
                "mcp",
                "--root", resolved,
                "--automation-read-root", resolved,
                "--automation-write-root", resolved,
            ],
        )


class PhotoCraftHiddenToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_desktop_only_tools_are_hidden_as_in_studio(self):
        listed = SimpleNamespace(
            tools=[SimpleNamespace(name=name) for name in ("layer_add", "control_call", "ui_screenshot")]
        )

        async def run(_self, _operation):
            return listed

        with patch.object(PhotoCraftBridge, "run", run):
            result = await PhotoCraftBridge().list_tools()
        self.assertEqual([tool.name for tool in result.tools], ["layer_add"])


def _linux_checksums(app: str) -> list[str]:
    manifest = json.loads((README.parent / "craft_apps.json").read_text())
    entry = next(item for item in manifest["apps"] if item["name"] == app)
    return list(entry["sha256"].values())


if __name__ == "__main__":
    unittest.main()
