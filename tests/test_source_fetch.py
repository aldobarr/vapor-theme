import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_bundle_validation import run_cli
from tests.test_compiler_identity import REPO_ROOT, create_source_fixture


def _commit_repository(repository: Path) -> str:
    subprocess.run(
        ["git", "init", "-q", str(repository)],
        check=True,
        text=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "add", "."],
        check=True,
        text=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Vapor Tests",
            "-c",
            "user.email=vapor-tests@example.invalid",
            "-C",
            str(repository),
            "commit",
            "-q",
            "-m",
            "fixture",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    return subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


class SourceFetchTests(unittest.TestCase):
    def test_checkout_bytes_do_not_inherit_machine_line_ending_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins_path = create_source_fixture(temporary)
            tracked = steam / "usr/share/color-schemes/Vapor.colors"
            tracked.write_bytes(
                b"[General]\nName=Vapor\n[Colors:Window]\nBackgroundNormal=35,38,41\n"
            )
            steam_commit = _commit_repository(steam)
            bazzite_commit = _commit_repository(bazzite)
            expected = tracked.read_bytes()
            pins = json.loads(pins_path.read_text(encoding="utf-8"))
            pins["inputs"]["steam:usr/share/color-schemes/Vapor.colors"] = (
                hashlib.sha256(expected).hexdigest()
            )
            pins["steam_presets"].update(
                {
                    "commit": steam_commit,
                    "repository": str(steam),
                }
            )
            pins["bazzite"].update(
                {
                    "commit": bazzite_commit,
                    "repository": str(bazzite),
                }
            )
            pins_path.write_text(
                json.dumps(pins, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            global_config = temporary / "hostile-git-config"
            global_config.write_text(
                "[core]\n\tautocrlf = true\n",
                encoding="utf-8",
                newline="\n",
            )
            environment = os.environ.copy()
            environment["GIT_CONFIG_GLOBAL"] = str(global_config)
            output = temporary / "fetched"

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vapor_theme",
                    "fetch-sources",
                    "--pins",
                    str(pins_path),
                    "--output",
                    str(output),
                ],
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (output / "steam/usr/share/color-schemes/Vapor.colors").read_bytes(),
                expected,
            )

    def test_fetches_and_verifies_exact_pinned_git_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins_path = create_source_fixture(temporary)
            steam_commit = _commit_repository(steam)
            bazzite_commit = _commit_repository(bazzite)
            pins = json.loads(pins_path.read_text(encoding="utf-8"))
            pins["steam_presets"].update(
                {
                    "commit": steam_commit,
                    "repository": str(steam),
                }
            )
            pins["bazzite"].update(
                {
                    "commit": bazzite_commit,
                    "repository": str(bazzite),
                }
            )
            pins_path.write_text(
                json.dumps(pins, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            output = temporary / "fetched"

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vapor_theme",
                    "fetch-sources",
                    "--pins",
                    str(pins_path),
                    "--output",
                    str(output),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", str(output / "steam"), "rev-parse", "HEAD"],
                    check=True,
                    text=True,
                    capture_output=True,
                ).stdout.strip(),
                steam_commit,
            )
            self.assertEqual(
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(output / "bazzite"),
                        "rev-parse",
                        "HEAD",
                    ],
                    check=True,
                    text=True,
                    capture_output=True,
                ).stdout.strip(),
                bazzite_commit,
            )

            artifact = temporary / "Vapor.tar.gz"
            build = run_cli(
                "build",
                "--steam-source",
                str(output / "steam"),
                "--bazzite-source",
                str(output / "bazzite"),
                "--pins",
                str(pins_path),
                "--output",
                str(artifact),
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            validation = run_cli("validate", "--bundle", str(artifact))
            self.assertEqual(validation.returncode, 0, validation.stderr)

    def test_failed_fetch_leaves_no_partial_source_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins_path = create_source_fixture(temporary)
            pins = json.loads(pins_path.read_text(encoding="utf-8"))
            pins["steam_presets"].update(
                {
                    "commit": "0" * 40,
                    "repository": str(steam),
                }
            )
            pins["bazzite"].update(
                {
                    "commit": _commit_repository(bazzite),
                    "repository": str(bazzite),
                }
            )
            pins_path.write_text(
                json.dumps(pins, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            output = temporary / "fetched"

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vapor_theme",
                    "fetch-sources",
                    "--pins",
                    str(pins_path),
                    "--output",
                    str(output),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("could not fetch pinned commit", result.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
