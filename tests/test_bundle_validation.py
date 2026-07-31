import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from tests.test_compiler_identity import REPO_ROOT, create_source_fixture


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "vapor_theme", *arguments],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


class BundleValidationTests(unittest.TestCase):
    def test_rejects_project_version_not_derived_from_pinned_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins_path = create_source_fixture(temporary)
            pins = json.loads(pins_path.read_text(encoding="utf-8"))
            pins["project_version"] = "44.20260729.1"
            pins_path.write_text(
                json.dumps(pins, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )

            rejected = run_cli(
                "build",
                "--steam-source",
                str(steam),
                "--bazzite-source",
                str(bazzite),
                "--pins",
                str(pins_path),
                "--output",
                str(temporary / "mislabeled.tar.gz"),
            )

            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("project_version must be derived", rejected.stderr)
            self.assertFalse((temporary / "mislabeled.tar.gz").exists())

    def test_rejects_tampered_source_hashes_in_embedded_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            archive = temporary / "vapor.tar.gz"
            built = run_cli(
                "build",
                "--steam-source",
                str(steam),
                "--bazzite-source",
                str(bazzite),
                "--pins",
                str(pins),
                "--output",
                str(archive),
            )
            self.assertEqual(built.returncode, 0, built.stderr)

            extracted = temporary / "bundle"
            with tarfile.open(archive, "r:gz") as release:
                release.extractall(extracted, filter="data")
            bundle = extracted / "vapor-44.20260730.1"
            provenance_path = bundle / "provenance.json"
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            first_input = next(iter(provenance["source_pins"]["inputs"]))
            provenance["source_pins"]["inputs"][first_input] = "tampered"
            provenance_path.write_text(
                json.dumps(provenance, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )

            invalid = run_cli("validate", "--bundle", str(bundle))

            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("source pin hash", invalid.stderr.lower())

    def test_requires_vapors_own_defaults_for_global_theme_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            archive = temporary / "vapor.tar.gz"
            built = run_cli(
                "build",
                "--steam-source",
                str(steam),
                "--bazzite-source",
                str(bazzite),
                "--pins",
                str(pins),
                "--output",
                str(archive),
            )
            self.assertEqual(built.returncode, 0, built.stderr)

            valid = run_cli("validate", "--bundle", str(archive))
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertIn("valid vapor bundle", valid.stdout.lower())

            extracted = temporary / "extracted"
            with tarfile.open(archive, "r:gz") as release:
                release.extractall(extracted, filter="data")
            bundle = extracted / "vapor-44.20260730.1"
            defaults = (
                bundle
                / "payload"
                / "plasma"
                / "look-and-feel"
                / "com.valve.vapor.desktop"
                / "contents"
                / "defaults"
            )
            defaults.unlink()
            manifest_path = bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            del manifest["files"][
                "plasma/look-and-feel/com.valve.vapor.desktop/contents/defaults"
            ]
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )

            invalid = run_cli("validate", "--bundle", str(bundle))
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("contents/defaults", invalid.stderr)


if __name__ == "__main__":
    unittest.main()
