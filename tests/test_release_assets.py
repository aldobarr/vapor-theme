import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_bundle_validation import run_cli
from tests.test_compiler_identity import REPO_ROOT, create_source_fixture


class ReleaseAssetTests(unittest.TestCase):
    def test_existing_release_assets_are_verified_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            assets = [
                temporary / "Vapor-v44.20260730.1.tar.gz",
                temporary / "SHA256SUMS",
                temporary / "Vapor-v44.20260730.1.provenance.json",
            ]
            for index, asset in enumerate(assets):
                asset.write_bytes(f"asset-{index}\n".encode())
            tag_object = "a" * 40
            marker = run_cli(
                "release-owner-marker",
                "--tag-object",
                tag_object,
            )
            self.assertEqual(marker.returncode, 0, marker.stderr)

            def release_json(
                *,
                body: str,
                included: int = 3,
                draft: bool = False,
                prerelease: bool = False,
                published_at: str | None = "2026-07-31T16:07:52Z",
            ) -> Path:
                path = temporary / (
                    f"release-{included}-{draft}-{prerelease}-"
                    f"{published_at is not None}-{len(body)}.json"
                )
                path.write_text(
                    json.dumps(
                        {
                            "id": 42,
                            "tag_name": "v44.20260730.1",
                            "body": body,
                            "draft": draft,
                            "prerelease": prerelease,
                            "published_at": published_at,
                            "assets": [
                                {
                                    "id": 100 + index,
                                    "name": asset.name,
                                    "state": "uploaded",
                                    "digest": "sha256:"
                                    + hashlib.sha256(asset.read_bytes()).hexdigest(),
                                }
                                for index, asset in enumerate(assets[:included])
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                return path

            completed = run_cli(
                "plan-release-upload",
                "--release-json",
                str(release_json(body="A manually authored completed release.")),
                "--tag",
                "v44.20260730.1",
                "--tag-object",
                tag_object,
                *sum((["--asset", str(asset)] for asset in assets), []),
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.splitlines(), ["release_id=42"])

            incomplete_foreign = run_cli(
                "plan-release-upload",
                "--release-json",
                str(
                    release_json(
                        body="A manually authored incomplete release.", included=2
                    )
                ),
                "--tag",
                "v44.20260730.1",
                "--tag-object",
                tag_object,
                *sum((["--asset", str(asset)] for asset in assets), []),
            )
            self.assertNotEqual(incomplete_foreign.returncode, 0)
            self.assertIn("not owned by Vapor automation", incomplete_foreign.stderr)

            incomplete_owned = run_cli(
                "plan-release-upload",
                "--release-json",
                str(
                    release_json(
                        body=f"Automated release. {marker.stdout.strip()}",
                        included=2,
                    )
                ),
                "--tag",
                "v44.20260730.1",
                "--tag-object",
                tag_object,
                *sum((["--asset", str(asset)] for asset in assets), []),
            )
            self.assertEqual(incomplete_owned.returncode, 0, incomplete_owned.stderr)
            self.assertEqual(
                incomplete_owned.stdout.splitlines(),
                ["release_id=42", f"asset={assets[2]}"],
            )

            damaged = json.loads(
                release_json(body=marker.stdout.strip()).read_text(encoding="utf-8")
            )
            damaged["assets"][0]["digest"] = "sha256:" + "0" * 64
            damaged_path = temporary / "damaged.json"
            damaged_path.write_text(json.dumps(damaged), encoding="utf-8")
            mismatch = run_cli(
                "plan-release-upload",
                "--release-json",
                str(damaged_path),
                "--tag",
                "v44.20260730.1",
                "--tag-object",
                tag_object,
                *sum((["--asset", str(asset)] for asset in assets), []),
            )
            self.assertNotEqual(mismatch.returncode, 0)
            self.assertIn("different content", mismatch.stderr)

            for unpublished in (
                release_json(body=marker.stdout.strip(), draft=True),
                release_json(body=marker.stdout.strip(), prerelease=True),
                release_json(body=marker.stdout.strip(), published_at=None),
            ):
                with self.subTest(release=unpublished.name):
                    result = run_cli(
                        "plan-release-upload",
                        "--release-json",
                        str(unpublished),
                        "--tag",
                        "v44.20260730.1",
                        "--tag-object",
                        tag_object,
                        *sum((["--asset", str(asset)] for asset in assets), []),
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("published stable release", result.stderr)

    def test_prepares_versioned_archive_checksum_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            artifact = temporary / "Vapor.tar.gz"
            build = run_cli(
                "build",
                "--steam-source",
                str(steam),
                "--bazzite-source",
                str(bazzite),
                "--pins",
                str(pins),
                "--output",
                str(artifact),
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            plan = temporary / "plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "action": "release",
                        "artifact": "Vapor.tar.gz",
                        "artifact_sha256": digest,
                        "incident": "close",
                        "schema_version": 1,
                        "stable_release": "stable-44.20260730",
                        "tag": "v44.20260730.1",
                        "theme_fingerprint": "test-fingerprint",
                        "version": "44.20260730.1",
                    }
                ),
                encoding="utf-8",
            )
            output = temporary / "release"

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vapor_theme",
                    "prepare-release",
                    "--bundle",
                    str(artifact),
                    "--plan",
                    str(plan),
                    "--output",
                    str(output),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            archive = output / "Vapor-v44.20260730.1.tar.gz"
            provenance = output / "Vapor-v44.20260730.1.provenance.json"
            self.assertEqual(archive.read_bytes(), artifact.read_bytes())
            self.assertEqual(
                (output / "SHA256SUMS").read_text(encoding="utf-8"),
                f"{digest}  {archive.name}\n",
            )
            provenance_data = json.loads(provenance.read_text(encoding="utf-8"))
            self.assertEqual(
                provenance_data["project_version"],
                "44.20260730.1",
            )
            self.assertEqual(
                provenance_data["release"]["archive_sha256"],
                digest,
            )
            self.assertEqual(
                provenance_data["release"]["tag"],
                "v44.20260730.1",
            )
            self.assertEqual(
                provenance_data["source_pins"],
                json.loads(pins.read_text(encoding="utf-8")),
            )
            validation = run_cli("validate", "--bundle", str(archive))
            self.assertEqual(validation.returncode, 0, validation.stderr)

    def test_rejects_a_plan_for_different_bytes_without_partial_assets(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            artifact = temporary / "Vapor.tar.gz"
            build = run_cli(
                "build",
                "--steam-source",
                str(steam),
                "--bazzite-source",
                str(bazzite),
                "--pins",
                str(pins),
                "--output",
                str(artifact),
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            plan = temporary / "plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "action": "release",
                        "artifact": "Vapor.tar.gz",
                        "artifact_sha256": "0" * 64,
                        "incident": "close",
                        "schema_version": 1,
                        "stable_release": "stable-44.20260730",
                        "tag": "v44.20260730.1",
                        "theme_fingerprint": "test-fingerprint",
                        "version": "44.20260730.1",
                    }
                ),
                encoding="utf-8",
            )
            output = temporary / "release"

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vapor_theme",
                    "prepare-release",
                    "--bundle",
                    str(artifact),
                    "--plan",
                    str(plan),
                    "--output",
                    str(output),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("artifact checksum does not match", result.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
