import hashlib
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from tests.test_bundle_validation import run_cli
from tests.test_compiler_identity import REPO_ROOT, create_source_fixture

DESKTOP_SPEC_PATH = (
    "spec_files/steamdeck-kde-presets/steamdeck-kde-presets-desktop.spec"
)


def expected_theme_fingerprint(
    inputs: dict[str, str],
    *,
    bazzite_source: Path,
    steam_commit: str = "dddddddddddddddddddddddddddddddddddddddd",
    project_theme_recipe: str | None = "system-color-delegation-v1",
) -> str:
    non_visual_bazzite_inputs = {
        "bazzite:LICENSE",
        "bazzite:spec_files/steamdeck-kde-presets/LICENSE",
        f"bazzite:{DESKTOP_SPEC_PATH}",
        "steam:usr/share/plasma/desktoptheme/Vapor/colors",
    }
    relevant = {
        name: digest
        for name, digest in inputs.items()
        if name not in non_visual_bazzite_inputs
        and "/contents/icons/" not in name
        and not name.endswith("/contents/splash/images/deck_logo.svgz")
        and "/contents/plasmoidsetupscripts/" not in name
    }
    spec = (bazzite_source / DESKTOP_SPEC_PATH).read_text(encoding="utf-8")
    patch_declarations = re.findall(
        r"(?m)^\s*Patch(\d*):\s*(\S+)\s*$",
        spec,
    )
    ordered_theme_patches = [
        f"bazzite:spec_files/steamdeck-kde-presets/{patch_name}"
        for _, patch_name in sorted(
            (int(number_text or "0"), patch_name)
            for number_text, patch_name in patch_declarations
        )
    ]
    steam_tag_match = re.search(
        r"(?m)^\s*%define\s+packagever\s+(\S+)\s*$",
        spec,
    )
    if steam_tag_match is None:
        raise AssertionError("fixture desktop spec has no Steam preset tag")
    identity = {
        "ordered_theme_patches": ordered_theme_patches,
        "payload_inputs": relevant,
        "steam_presets": {
            "commit": steam_commit,
            "tag": steam_tag_match.group(1),
        },
    }
    if project_theme_recipe is not None:
        identity["project_theme_recipe"] = project_theme_recipe
    return hashlib.sha256(
        json.dumps(
            identity,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


class UpdaterTests(unittest.TestCase):
    def test_drafts_and_prereleases_are_ignored_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            state = temporary / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "last_checked_stable": "stable-44.20260701",
                        "heartbeat_month": "2026-07",
                        "theme_fingerprint": "existing-fingerprint",
                        "version": "44.20260701.1",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            original_pins = pins.read_bytes()
            original_state = state.read_bytes()

            for release_kind in ("draft", "prerelease"):
                release = temporary / f"{release_kind}.json"
                release.write_text(
                    json.dumps(
                        {
                            "draft": release_kind == "draft",
                            "prerelease": release_kind == "prerelease",
                            "published_at": "2026-07-30T12:00:00Z",
                            "tag_name": "stable-44.20260730",
                        }
                    ),
                    encoding="utf-8",
                )
                plan = temporary / f"{release_kind}-plan.json"
                artifact = temporary / f"{release_kind}.tar.gz"
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "vapor_theme",
                        "update",
                        "--release-json",
                        str(release),
                        "--bazzite-source",
                        str(bazzite),
                        "--steam-source",
                        str(steam),
                        "--bazzite-commit",
                        "cccccccccccccccccccccccccccccccccccccccc",
                        "--steam-commit",
                        "dddddddddddddddddddddddddddddddddddddddd",
                        "--pins",
                        str(pins),
                        "--state",
                        str(state),
                        "--artifact",
                        str(artifact),
                        "--plan",
                        str(plan),
                        "--checked-at",
                        "2026-07-30T13:00:00Z",
                    ],
                    cwd=REPO_ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                decision = json.loads(plan.read_text(encoding="utf-8"))
                self.assertEqual(decision["action"], "ignored")
                self.assertEqual(decision["reason"], release_kind)
                self.assertEqual(decision["incident"], "close")
                self.assertEqual(pins.read_bytes(), original_pins)
                self.assertEqual(state.read_bytes(), original_state)
                self.assertFalse(artifact.exists())

    def test_unchanged_stable_release_updates_state_once_without_artifact(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            pinned = json.loads(pins.read_text(encoding="utf-8"))
            fingerprint = expected_theme_fingerprint(
                pinned["inputs"],
                bazzite_source=bazzite,
            )
            state = temporary / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "last_checked_stable": "stable-44.20260701",
                        "heartbeat_month": "2026-06",
                        "theme_fingerprint": fingerprint,
                        "version": "44.20260701.1",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            release = temporary / "release.json"
            release.write_text(
                json.dumps(
                    {
                        "draft": False,
                        "prerelease": False,
                        "published_at": "2026-07-30T12:00:00Z",
                        "tag_name": "44.20260730",
                    }
                ),
                encoding="utf-8",
            )
            plan = temporary / "plan.json"
            artifact = temporary / "vapor.tar.gz"
            command = [
                sys.executable,
                "-m",
                "vapor_theme",
                "update",
                "--release-json",
                str(release),
                "--bazzite-source",
                str(bazzite),
                "--steam-source",
                str(steam),
                "--bazzite-commit",
                "cccccccccccccccccccccccccccccccccccccccc",
                "--steam-commit",
                "dddddddddddddddddddddddddddddddddddddddd",
                "--pins",
                str(pins),
                "--state",
                str(state),
                "--artifact",
                str(artifact),
                "--plan",
                str(plan),
                "--checked-at",
                "2026-07-30T13:00:00Z",
            ]

            first = subprocess.run(
                command,
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            first_plan = json.loads(plan.read_text(encoding="utf-8"))
            self.assertEqual(first_plan["action"], "state-only")
            self.assertEqual(
                first_plan["reason"],
                "stable-without-theme-change",
            )
            updated_state_bytes = state.read_bytes()
            updated_state = json.loads(updated_state_bytes)
            self.assertEqual(
                updated_state["last_checked_stable"],
                "44.20260730",
            )
            self.assertEqual(updated_state["heartbeat_month"], "2026-07")
            self.assertEqual(
                updated_state["last_successful_check"],
                "2026-07-30T13:00:00Z",
            )
            self.assertFalse(artifact.exists())
            pins_after_first = pins.read_bytes()

            second = subprocess.run(
                command,
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            second_plan = json.loads(plan.read_text(encoding="utf-8"))
            self.assertEqual(second_plan["action"], "none")
            self.assertEqual(second_plan["reason"], "already-checked")
            self.assertEqual(state.read_bytes(), updated_state_bytes)
            self.assertEqual(pins.read_bytes(), pins_after_first)
            self.assertFalse(artifact.exists())

    def test_system_color_delegation_migration_creates_a_new_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            pinned = json.loads(pins.read_text(encoding="utf-8"))
            legacy_fingerprint = expected_theme_fingerprint(
                pinned["inputs"],
                bazzite_source=bazzite,
                project_theme_recipe=None,
            )
            state = temporary / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "last_checked_stable": "44.20260730",
                        "heartbeat_month": "2026-08",
                        "theme_fingerprint": legacy_fingerprint,
                        "version": "44.20260730.1",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            release = temporary / "release.json"
            release.write_text(
                json.dumps(
                    {
                        "draft": False,
                        "prerelease": False,
                        "published_at": "2026-07-30T12:00:00Z",
                        "tag_name": "44.20260730",
                    }
                ),
                encoding="utf-8",
            )
            artifact = temporary / "vapor.tar.gz"
            plan = temporary / "plan.json"

            result = run_cli(
                "update",
                "--release-json",
                str(release),
                "--bazzite-source",
                str(bazzite),
                "--steam-source",
                str(steam),
                "--bazzite-commit",
                "cccccccccccccccccccccccccccccccccccccccc",
                "--steam-commit",
                "dddddddddddddddddddddddddddddddddddddddd",
                "--pins",
                str(pins),
                "--state",
                str(state),
                "--artifact",
                str(artifact),
                "--plan",
                str(plan),
                "--checked-at",
                "2026-08-06T13:00:00Z",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            decision = json.loads(plan.read_text(encoding="utf-8"))
            self.assertEqual(decision["action"], "release")
            self.assertEqual(decision["version"], "44.20260730.2")
            self.assertNotEqual(
                decision["theme_fingerprint"],
                legacy_fingerprint,
            )
            self.assertTrue(artifact.is_file())

    def test_steam_revision_change_releases_identical_tracked_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            pinned = json.loads(pins.read_text(encoding="utf-8"))
            state = temporary / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "last_checked_stable": "stable-44.20260730",
                        "heartbeat_month": "2026-07",
                        "theme_fingerprint": expected_theme_fingerprint(
                            pinned["inputs"],
                            bazzite_source=bazzite,
                            steam_commit=pinned["steam_presets"]["commit"],
                        ),
                        "version": "44.20260730.1",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            release = temporary / "release.json"
            release.write_text(
                json.dumps(
                    {
                        "draft": False,
                        "prerelease": False,
                        "published_at": "2026-07-30T12:00:00Z",
                        "tag_name": "stable-44.20260730",
                    }
                ),
                encoding="utf-8",
            )
            artifact = temporary / "Vapor.tar.gz"
            plan = temporary / "plan.json"

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vapor_theme",
                    "update",
                    "--release-json",
                    str(release),
                    "--bazzite-source",
                    str(bazzite),
                    "--steam-source",
                    str(steam),
                    "--bazzite-commit",
                    pinned["bazzite"]["commit"],
                    "--steam-commit",
                    "dddddddddddddddddddddddddddddddddddddddd",
                    "--pins",
                    str(pins),
                    "--state",
                    str(state),
                    "--artifact",
                    str(artifact),
                    "--plan",
                    str(plan),
                    "--checked-at",
                    "2026-07-30T13:00:00Z",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            decision = json.loads(plan.read_text(encoding="utf-8"))
            self.assertEqual(decision["action"], "release")
            self.assertEqual(decision["version"], "44.20260730.2")
            updated_pins = json.loads(pins.read_text(encoding="utf-8"))
            self.assertEqual(
                updated_pins["steam_presets"]["commit"],
                "dddddddddddddddddddddddddddddddddddddddd",
            )
            with tarfile.open(artifact, "r:gz") as bundle:
                provenance_members = [
                    member
                    for member in bundle.getmembers()
                    if member.name.endswith("/provenance.json")
                ]
                self.assertEqual(len(provenance_members), 1)
                provenance_file = bundle.extractfile(provenance_members[0])
                self.assertIsNotNone(provenance_file)
                provenance = json.load(provenance_file)
            self.assertEqual(
                provenance["source_pins"]["steam_presets"]["commit"],
                "dddddddddddddddddddddddddddddddddddddddd",
            )

    def test_excluded_deck_artwork_change_does_not_create_a_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            pinned = json.loads(pins.read_text(encoding="utf-8"))
            state = temporary / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "last_checked_stable": "44.20260701",
                        "heartbeat_month": "2026-06",
                        "theme_fingerprint": expected_theme_fingerprint(
                            pinned["inputs"],
                            bazzite_source=bazzite,
                        ),
                        "version": "44.20260701.1",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            deck_icon = (
                steam
                / "usr"
                / "share"
                / "plasma"
                / "look-and-feel"
                / "com.valve.vapor.deck.desktop"
                / "contents"
                / "icons"
                / "deck_icon.png"
            )
            deck_icon.write_bytes(b"changed-but-still-excluded")
            release = temporary / "release.json"
            release.write_text(
                json.dumps(
                    {
                        "draft": False,
                        "prerelease": False,
                        "published_at": "2026-07-30T12:00:00Z",
                        "tag_name": "44.20260730",
                    }
                ),
                encoding="utf-8",
            )
            artifact = temporary / "unexpected.tar.gz"
            plan = temporary / "plan.json"
            original_pins = pins.read_bytes()

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vapor_theme",
                    "update",
                    "--release-json",
                    str(release),
                    "--bazzite-source",
                    str(bazzite),
                    "--steam-source",
                    str(steam),
                    "--bazzite-commit",
                    "cccccccccccccccccccccccccccccccccccccccc",
                    "--steam-commit",
                    "dddddddddddddddddddddddddddddddddddddddd",
                    "--pins",
                    str(pins),
                    "--state",
                    str(state),
                    "--artifact",
                    str(artifact),
                    "--plan",
                    str(plan),
                    "--checked-at",
                    "2026-07-30T13:00:00Z",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            decision = json.loads(plan.read_text(encoding="utf-8"))
            self.assertEqual(decision["action"], "state-only")
            self.assertEqual(decision["reason"], "stable-without-theme-change")
            self.assertEqual(pins.read_bytes(), original_pins)
            self.assertFalse(artifact.exists())

    def test_rpm_and_license_metadata_changes_do_not_create_a_release(self) -> None:
        metadata_changes = {
            "LICENSE": "\nCopyright metadata update only.\n",
            DESKTOP_SPEC_PATH: (
                "\n%changelog\n"
                "* Thu Jul 30 2026 Packager <packager@example.com> - 0.30-2\n"
                "- Packaging metadata update only.\n"
            ),
        }
        for relative_path, appended_text in metadata_changes.items():
            with (
                self.subTest(relative_path=relative_path),
                tempfile.TemporaryDirectory() as temporary_directory,
            ):
                temporary = Path(temporary_directory)
                steam, bazzite, pins = create_source_fixture(temporary)
                pinned = json.loads(pins.read_text(encoding="utf-8"))
                state = temporary / "state.json"
                state.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "last_checked_stable": "44.20260701",
                            "heartbeat_month": "2026-06",
                            "theme_fingerprint": expected_theme_fingerprint(
                                pinned["inputs"],
                                bazzite_source=bazzite,
                            ),
                            "version": "44.20260701.1",
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                metadata_path = bazzite / relative_path
                metadata_path.write_text(
                    metadata_path.read_text(encoding="utf-8") + appended_text,
                    encoding="utf-8",
                    newline="\n",
                )
                release = temporary / "release.json"
                release.write_text(
                    json.dumps(
                        {
                            "draft": False,
                            "prerelease": False,
                            "published_at": "2026-07-30T12:00:00Z",
                            "tag_name": "44.20260730",
                        }
                    ),
                    encoding="utf-8",
                )
                artifact = temporary / "unexpected.tar.gz"
                plan = temporary / "plan.json"
                original_pins = pins.read_bytes()

                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "vapor_theme",
                        "update",
                        "--release-json",
                        str(release),
                        "--bazzite-source",
                        str(bazzite),
                        "--steam-source",
                        str(steam),
                        "--bazzite-commit",
                        "cccccccccccccccccccccccccccccccccccccccc",
                        "--steam-commit",
                        "dddddddddddddddddddddddddddddddddddddddd",
                        "--pins",
                        str(pins),
                        "--state",
                        str(state),
                        "--artifact",
                        str(artifact),
                        "--plan",
                        str(plan),
                        "--checked-at",
                        "2026-07-30T13:00:00Z",
                    ],
                    cwd=REPO_ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                decision = json.loads(plan.read_text(encoding="utf-8"))
                self.assertEqual(decision["action"], "state-only")
                self.assertEqual(
                    decision["reason"],
                    "stable-without-theme-change",
                )
                self.assertEqual(pins.read_bytes(), original_pins)
                self.assertFalse(artifact.exists())

    def test_renumbered_interacting_patches_build_and_report_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            pinned = json.loads(pins.read_text(encoding="utf-8"))
            state = temporary / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "last_checked_stable": "44.20260701",
                        "heartbeat_month": "2026-06",
                        "theme_fingerprint": expected_theme_fingerprint(
                            pinned["inputs"],
                            bazzite_source=bazzite,
                        ),
                        "version": "44.20260701.1",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            spec = bazzite / DESKTOP_SPEC_PATH
            spec.write_text(
                spec.read_text(encoding="utf-8").replace(
                    (
                        "Patch1: bazzite_logo.patch\n"
                        "Patch2: ublue.patch\n"
                        "Patch3: splash.patch\n"
                    ),
                    (
                        "Patch3: bazzite_logo.patch\n"
                        "Patch2: ublue.patch\n"
                        "Patch1: splash.patch\n"
                    ),
                ),
                encoding="utf-8",
                newline="\n",
            )
            release = temporary / "release.json"
            release.write_text(
                json.dumps(
                    {
                        "draft": False,
                        "prerelease": False,
                        "published_at": "2026-07-30T12:00:00Z",
                        "tag_name": "44.20260730",
                    }
                ),
                encoding="utf-8",
            )
            artifact = temporary / "unexpected.tar.gz"
            plan = temporary / "plan.json"
            original_pins = pins.read_bytes()
            original_state = state.read_bytes()

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vapor_theme",
                    "update",
                    "--release-json",
                    str(release),
                    "--bazzite-source",
                    str(bazzite),
                    "--steam-source",
                    str(steam),
                    "--bazzite-commit",
                    "cccccccccccccccccccccccccccccccccccccccc",
                    "--steam-commit",
                    "dddddddddddddddddddddddddddddddddddddddd",
                    "--pins",
                    str(pins),
                    "--state",
                    str(state),
                    "--artifact",
                    str(artifact),
                    "--plan",
                    str(plan),
                    "--checked-at",
                    "2026-07-30T13:00:00Z",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            decision = json.loads(plan.read_text(encoding="utf-8"))
            self.assertEqual(decision["action"], "failure")
            self.assertEqual(decision["incident"]["action"], "open-or-update")
            self.assertEqual(pins.read_bytes(), original_pins)
            self.assertEqual(state.read_bytes(), original_state)
            self.assertFalse(artifact.exists())

    def test_duplicate_patch_numbers_report_failure_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            pinned = json.loads(pins.read_text(encoding="utf-8"))
            state = temporary / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "last_checked_stable": "44.20260701",
                        "heartbeat_month": "2026-06",
                        "theme_fingerprint": expected_theme_fingerprint(
                            pinned["inputs"],
                            bazzite_source=bazzite,
                        ),
                        "version": "44.20260701.1",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            spec = bazzite / DESKTOP_SPEC_PATH
            spec.write_text(
                spec.read_text(encoding="utf-8").replace(
                    (
                        "Patch1: bazzite_logo.patch\n"
                        "Patch2: ublue.patch\n"
                        "Patch3: splash.patch\n"
                    ),
                    (
                        "Patch: bazzite_logo.patch\n"
                        "Patch2: ublue.patch\n"
                        "Patch0: splash.patch\n"
                    ),
                ),
                encoding="utf-8",
                newline="\n",
            )
            release = temporary / "release.json"
            release.write_text(
                json.dumps(
                    {
                        "draft": False,
                        "prerelease": False,
                        "published_at": "2026-07-30T12:00:00Z",
                        "tag_name": "44.20260730",
                    }
                ),
                encoding="utf-8",
            )
            artifact = temporary / "unexpected.tar.gz"
            plan = temporary / "plan.json"
            original_pins = pins.read_bytes()
            original_state = state.read_bytes()

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vapor_theme",
                    "update",
                    "--release-json",
                    str(release),
                    "--bazzite-source",
                    str(bazzite),
                    "--steam-source",
                    str(steam),
                    "--bazzite-commit",
                    "cccccccccccccccccccccccccccccccccccccccc",
                    "--steam-commit",
                    "dddddddddddddddddddddddddddddddddddddddd",
                    "--pins",
                    str(pins),
                    "--state",
                    str(state),
                    "--artifact",
                    str(artifact),
                    "--plan",
                    str(plan),
                    "--checked-at",
                    "2026-07-30T13:00:00Z",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            decision = json.loads(plan.read_text(encoding="utf-8"))
            self.assertEqual(decision["action"], "failure")
            self.assertEqual(decision["incident"]["action"], "open-or-update")
            self.assertIn("desktop spec declares Patch0 twice", decision["error"])
            self.assertEqual(pins.read_bytes(), original_pins)
            self.assertEqual(state.read_bytes(), original_state)
            self.assertFalse(artifact.exists())

    def test_older_stable_release_is_ignored_without_downgrade_or_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            state = temporary / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "last_checked_stable": "44.20260730",
                        "heartbeat_month": "2026-07",
                        "theme_fingerprint": "current-fingerprint",
                        "version": "44.20260730.1",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            (bazzite / "spec_files" / "steamdeck-kde-presets" / "plasmarc").write_text(
                "[AdaptiveTransparency]\nenabled=true\ncontrast=0.25\n",
                encoding="utf-8",
                newline="\n",
            )
            release = temporary / "release.json"
            release.write_text(
                json.dumps(
                    {
                        "draft": False,
                        "prerelease": False,
                        "published_at": "2026-07-21T12:00:00Z",
                        "tag_name": "44.20260721",
                    }
                ),
                encoding="utf-8",
            )
            artifact = temporary / "stale.tar.gz"
            plan = temporary / "stale-plan.json"
            original_pins = pins.read_bytes()
            original_state = state.read_bytes()

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vapor_theme",
                    "update",
                    "--release-json",
                    str(release),
                    "--bazzite-source",
                    str(bazzite),
                    "--steam-source",
                    str(steam),
                    "--bazzite-commit",
                    "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                    "--steam-commit",
                    "ffffffffffffffffffffffffffffffffffffffff",
                    "--pins",
                    str(pins),
                    "--state",
                    str(state),
                    "--artifact",
                    str(artifact),
                    "--plan",
                    str(plan),
                    "--checked-at",
                    "2026-07-30T13:00:00Z",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            decision = json.loads(plan.read_text(encoding="utf-8"))
            self.assertEqual(decision["action"], "ignored")
            self.assertEqual(decision["reason"], "older-than-current")
            self.assertEqual(decision["incident"], "close")
            self.assertEqual(pins.read_bytes(), original_pins)
            self.assertEqual(state.read_bytes(), original_state)
            self.assertFalse(artifact.exists())

    def test_theme_change_uses_the_selected_revision_before_updating_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            pinned = json.loads(pins.read_text(encoding="utf-8"))
            old_fingerprint = expected_theme_fingerprint(
                pinned["inputs"],
                bazzite_source=bazzite,
            )
            state = temporary / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "last_checked_stable": "stable-44.20260701",
                        "heartbeat_month": "2026-07",
                        "theme_fingerprint": old_fingerprint,
                        "version": "44.20260701.1",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            plasmarc = bazzite / "spec_files" / "steamdeck-kde-presets" / "plasmarc"
            plasmarc.write_text(
                "[AdaptiveTransparency]\nenabled=true\ncontrast=0.25\n",
                encoding="utf-8",
                newline="\n",
            )
            release = temporary / "release.json"
            release.write_text(
                json.dumps(
                    {
                        "draft": False,
                        "prerelease": False,
                        "published_at": "2026-07-30T12:00:00Z",
                        "tag_name": "stable-44.20260730",
                    }
                ),
                encoding="utf-8",
            )
            artifact = temporary / "vapor.tar.gz"
            plan = temporary / "plan.json"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vapor_theme",
                    "update",
                    "--release-json",
                    str(release),
                    "--bazzite-source",
                    str(bazzite),
                    "--steam-source",
                    str(steam),
                    "--bazzite-commit",
                    "cccccccccccccccccccccccccccccccccccccccc",
                    "--steam-commit",
                    "dddddddddddddddddddddddddddddddddddddddd",
                    "--pins",
                    str(pins),
                    "--state",
                    str(state),
                    "--artifact",
                    str(artifact),
                    "--plan",
                    str(plan),
                    "--checked-at",
                    "2026-07-30T13:00:00Z",
                    "--candidate-version",
                    "44.20260730.2",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            decision = json.loads(plan.read_text(encoding="utf-8"))
            self.assertEqual(decision["action"], "release")
            self.assertEqual(decision["tag"], "v44.20260730.2")
            self.assertEqual(decision["version"], "44.20260730.2")
            self.assertEqual(
                decision["artifact_sha256"],
                hashlib.sha256(artifact.read_bytes()).hexdigest(),
            )
            validation = run_cli("validate", "--bundle", str(artifact))
            self.assertEqual(validation.returncode, 0, validation.stderr)

            new_pins = json.loads(pins.read_text(encoding="utf-8"))
            self.assertEqual(
                new_pins["project_version"],
                "44.20260730.2",
            )
            self.assertEqual(
                new_pins["bazzite"]["commit"],
                "cccccccccccccccccccccccccccccccccccccccc",
            )
            self.assertEqual(
                new_pins["bazzite"]["stable_release"],
                "44.20260730",
            )
            self.assertEqual(new_pins["steam_presets"]["tag"], "0.30")
            self.assertEqual(
                new_pins["steam_presets"]["commit"],
                "dddddddddddddddddddddddddddddddddddddddd",
            )
            self.assertEqual(
                new_pins["inputs"]["bazzite:spec_files/steamdeck-kde-presets/plasmarc"],
                hashlib.sha256(plasmarc.read_bytes()).hexdigest(),
            )
            new_state = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(new_state["version"], "44.20260730.2")
            self.assertEqual(
                new_state["theme_fingerprint"],
                decision["theme_fingerprint"],
            )
            self.assertEqual(
                new_state["last_checked_stable"],
                "stable-44.20260730",
            )

    def test_failed_theme_change_rolls_back_and_requests_one_incident(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            pinned = json.loads(pins.read_text(encoding="utf-8"))
            fingerprint = expected_theme_fingerprint(
                pinned["inputs"],
                bazzite_source=bazzite,
            )
            state = temporary / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "last_checked_stable": "stable-44.20260701",
                        "heartbeat_month": "2026-07",
                        "theme_fingerprint": fingerprint,
                        "version": "44.20260701.1",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            broken_patch = (
                bazzite / "spec_files" / "steamdeck-kde-presets" / "splash.patch"
            )
            broken_patch.write_text(
                broken_patch.read_text(encoding="utf-8").replace(
                    "@@",
                    "@@ malformed",
                    1,
                ),
                encoding="utf-8",
                newline="\n",
            )
            release = temporary / "release.json"
            release.write_text(
                json.dumps(
                    {
                        "draft": False,
                        "prerelease": False,
                        "published_at": "2026-07-30T12:00:00Z",
                        "tag_name": "stable-44.20260730",
                    }
                ),
                encoding="utf-8",
            )
            artifact = temporary / "vapor.tar.gz"
            plan = temporary / "plan.json"
            original_pins = pins.read_bytes()
            original_state = state.read_bytes()
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vapor_theme",
                    "update",
                    "--release-json",
                    str(release),
                    "--bazzite-source",
                    str(bazzite),
                    "--steam-source",
                    str(steam),
                    "--bazzite-commit",
                    "cccccccccccccccccccccccccccccccccccccccc",
                    "--steam-commit",
                    "dddddddddddddddddddddddddddddddddddddddd",
                    "--pins",
                    str(pins),
                    "--state",
                    str(state),
                    "--artifact",
                    str(artifact),
                    "--plan",
                    str(plan),
                    "--checked-at",
                    "2026-07-30T13:00:00Z",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("failed to apply splash.patch", result.stderr)
            failure = json.loads(plan.read_text(encoding="utf-8"))
            self.assertEqual(failure["action"], "failure")
            self.assertEqual(
                failure["incident"]["action"],
                "open-or-update",
            )
            self.assertEqual(
                failure["incident"]["key"],
                "vapor-updater",
            )
            self.assertEqual(pins.read_bytes(), original_pins)
            self.assertEqual(state.read_bytes(), original_state)
            self.assertFalse(artifact.exists())


if __name__ == "__main__":
    unittest.main()
