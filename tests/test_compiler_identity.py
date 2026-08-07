import difflib
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_vapor_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "vapor_theme", *arguments],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def write_text(root: Path, relative_path: str, content: str) -> None:
    destination = root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8", newline="\n")


def write_bytes(root: Path, relative_path: str, content: bytes) -> None:
    destination = root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)


def write_patch(
    root: Path,
    patch_name: str,
    target_path: str,
    before: str,
    after: str,
) -> None:
    patch = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{target_path}",
            tofile=f"b/{target_path}",
        )
    )
    write_text(
        root,
        f"spec_files/steamdeck-kde-presets/{patch_name}",
        patch,
    )


def git_binary_patch(
    root: Path,
    target_path: str,
    before: bytes,
    after: bytes,
) -> str:
    write_bytes(root, target_path, before)
    subprocess.run(
        ["git", "-C", str(root), "init", "-q"],
        text=True,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "add", "--", target_path],
        text=True,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Vapor Tests",
            "-c",
            "user.email=vapor-tests@example.invalid",
            "commit",
            "-q",
            "-m",
            "binary baseline",
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    write_bytes(root, target_path, after)
    result = subprocess.run(
        ["git", "-C", str(root), "diff", "--binary", "--", target_path],
        text=True,
        capture_output=True,
        check=True,
    )
    write_bytes(root, target_path, before)
    return result.stdout


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_source_fixture(root: Path) -> tuple[Path, Path, Path]:
    steam = root / "steam source"
    bazzite = root / "bazzite source"

    write_text(
        steam,
        "usr/share/color-schemes/Vapor.colors",
        "[General]\nName=Vapor\n[Colors:Window]\nBackgroundNormal=35,38,41\n",
    )
    write_text(
        steam,
        "usr/share/plasma/desktoptheme/Vapor/metadata.json",
        json.dumps(
            {
                "KPlugin": {
                    "Id": "com.valve.vapor",
                    "Name": "Vapor",
                    "Version": "0.1",
                    "License": "GPL version 3",
                },
                "X-Plasma-API": "6.0",
            }
        ),
    )
    write_text(
        steam,
        "usr/share/plasma/desktoptheme/Vapor/colors",
        "[Colors:Window]\nBackgroundNormal=35,38,41\n",
    )
    look_and_feel_metadata = (
        json.dumps(
            {
                "KPackageStructure": "Plasma/LookAndFeel",
                "KPlugin": {
                    "Id": "com.valve.vapor.deck.desktop",
                    "Name": "Vapor (Steam Deck)",
                    "Description": "Vapor with Steam Deck tweaks",
                    "Version": "0.01",
                    "License": "GPLv2+",
                },
            },
            indent=2,
        )
        + "\n"
    )
    look_and_feel_path = (
        "usr/share/plasma/look-and-feel/com.valve.vapor.deck.desktop/metadata.json"
    )
    write_text(steam, look_and_feel_path, look_and_feel_metadata)
    write_text(
        steam,
        "usr/share/plasma/look-and-feel/com.valve.vapor.deck.desktop/contents/defaults",
        ("[kdeglobals][General]\nColorScheme=Vapor\n[plasmarc][Theme]\nname=Vapor\n"),
    )
    splash_path = (
        "usr/share/plasma/look-and-feel/com.valve.vapor.deck.desktop/"
        "contents/splash/Splash.qml"
    )
    original_splash = (
        "Image {\n"
        '    source: "images/deck_logo.svgz"\n'
        "    asynchronous: false\n"
        "    width: 100\n"
        "}\n"
    )
    write_text(steam, splash_path, original_splash)
    write_bytes(
        steam,
        "usr/share/plasma/look-and-feel/com.valve.vapor.deck.desktop/"
        "contents/splash/images/deck_logo.svgz",
        b"deck-logo",
    )
    write_bytes(
        steam,
        "usr/share/plasma/look-and-feel/com.valve.vapor.deck.desktop/"
        "contents/splash/images/busywidget.svgz",
        b"busy-widget",
    )
    write_bytes(
        steam,
        "usr/share/plasma/look-and-feel/com.valve.vapor.deck.desktop/"
        "contents/icons/deck_icon.png",
        b"excluded-deck-icon",
    )
    write_bytes(
        steam,
        "usr/share/plasma/look-and-feel/com.valve.vapor.deck.desktop/"
        "contents/previews/preview.png",
        b"preview",
    )
    folder_path = (
        "usr/share/plasma/look-and-feel/com.valve.vapor.deck.desktop/"
        "contents/plasmoidsetupscripts/org.kde.plasma.folder.js"
    )
    original_folder = (
        'applet.writeConfig("Image", '
        '"/usr/share/wallpapers/Steam Deck Logo Default.jpg")\n'
    )
    write_text(steam, folder_path, original_folder)
    write_text(
        steam,
        "usr/share/plasma/look-and-feel/com.valve.vapor.deck.desktop/"
        "contents/plasmoidsetupscripts/org.kde.plasma.kickoff.js",
        'applet.writeConfig("icon", "distributor-logo-steamdeck")\n',
    )
    write_text(
        steam,
        "usr/share/plasma/look-and-feel/com.valve.vapor.deck.desktop/"
        "contents/plasmoidsetupscripts/org.kde.plasma.systemtray.js",
        'applet.writeConfig("scaleIconsToFit", false)\n',
    )

    # Sentinels which must never leak into the desktop-only release.
    write_text(steam, "usr/share/konsole/Vapor.profile", "forbidden\n")
    write_text(
        steam,
        "usr/share/plasma/look-and-feel/com.valve.vgui.desktop/metadata.json",
        "{}\n",
    )
    write_text(steam, "etc/xdg/gtk-3.0/settings.ini", "forbidden\n")
    write_text(steam, "usr/bin/steamos-add-to-steam", "forbidden\n")

    write_bytes(
        bazzite,
        "spec_files/steamdeck-kde-presets/bazzite_logo.svgz",
        b"bazzite-logo",
    )
    write_text(
        bazzite,
        "spec_files/steamdeck-kde-presets/plasmarc",
        "[AdaptiveTransparency]\nenabled=true\n",
    )
    write_bytes(
        bazzite,
        "system_files/desktop/kinoite/usr/share/wallpapers/convergence.jxl",
        b"fixture-jxl",
    )
    write_text(
        bazzite,
        "system_files/overrides/usr/share/icons/hicolor/scalable/places/"
        "distributor-logo.svg",
        '<svg xmlns="http://www.w3.org/2000/svg"/>\n',
    )
    write_text(
        bazzite,
        "spec_files/steamdeck-kde-presets/kscreenlockerrc",
        "[Greeter][Wallpaper][org.kde.image][General]\n"
        "Image=/usr/share/wallpapers/convergence.jxl\n",
    )
    write_text(bazzite, "LICENSE", "Apache-2.0 fixture license\n")
    write_text(
        bazzite,
        "spec_files/steamdeck-kde-presets/LICENSE",
        "GPL-2.0 fixture license\n",
    )
    logo_patched_splash = original_splash.replace(
        "deck_logo.svgz",
        "bazzite_logo.svgz",
    )
    write_patch(
        bazzite,
        "bazzite_logo.patch",
        splash_path,
        original_splash,
        logo_patched_splash,
    )
    write_patch(
        bazzite,
        "ublue.patch",
        folder_path,
        original_folder,
        original_folder.replace(
            "Steam Deck Logo Default.jpg",
            "convergence.jxl",
        ),
    )
    write_patch(
        bazzite,
        "splash.patch",
        splash_path,
        logo_patched_splash,
        logo_patched_splash.replace(
            "asynchronous: false\n    width: 100",
            "asynchronous: true\n    width: 50",
        ),
    )
    patched_metadata = (
        look_and_feel_metadata.replace(
            "com.valve.vapor.deck.desktop",
            "com.valve.vapor.desktop",
        )
        .replace("Vapor (Steam Deck)", "Vapor")
        .replace(
            "Vapor with Steam Deck tweaks",
            "Vapor - SteamOS theme variant of KDE Breeze Dark",
        )
    )
    write_patch(
        bazzite,
        "vapor-metadata.patch",
        look_and_feel_path,
        look_and_feel_metadata,
        patched_metadata,
    )
    write_text(
        bazzite,
        "spec_files/steamdeck-kde-presets/steamdeck-kde-presets-desktop.spec",
        (
            "%define packagever 0.30\n"
            "Source0: steamdeck-kde-presets-0.30.tar.gz\n"
            "Patch1: bazzite_logo.patch\n"
            "Patch2: ublue.patch\n"
            "Patch3: splash.patch\n"
            "Patch4: vapor-metadata.patch\n"
        ),
    )

    input_hashes: dict[str, str] = {}
    steam_tracked_roots = (
        "usr/share/color-schemes/Vapor.colors",
        "usr/share/plasma/desktoptheme/Vapor",
        "usr/share/plasma/look-and-feel/com.valve.vapor.deck.desktop",
    )
    for tracked in steam_tracked_roots:
        candidate = steam / tracked
        files = [candidate] if candidate.is_file() else candidate.rglob("*")
        for source_file in files:
            if source_file.is_file():
                relative = source_file.relative_to(steam).as_posix()
                input_hashes[f"steam:{relative}"] = sha256(source_file)

    bazzite_tracked = (
        "LICENSE",
        "spec_files/steamdeck-kde-presets/LICENSE",
        "spec_files/steamdeck-kde-presets/bazzite_logo.svgz",
        "spec_files/steamdeck-kde-presets/plasmarc",
        "spec_files/steamdeck-kde-presets/bazzite_logo.patch",
        "spec_files/steamdeck-kde-presets/ublue.patch",
        "spec_files/steamdeck-kde-presets/splash.patch",
        "spec_files/steamdeck-kde-presets/vapor-metadata.patch",
        "spec_files/steamdeck-kde-presets/steamdeck-kde-presets-desktop.spec",
        "system_files/desktop/kinoite/usr/share/wallpapers/convergence.jxl",
        "system_files/overrides/usr/share/icons/hicolor/scalable/places/"
        "distributor-logo.svg",
    )
    for tracked in bazzite_tracked:
        source_file = bazzite / tracked
        input_hashes[f"bazzite:{tracked}"] = sha256(source_file)

    pins = root / "pins.json"
    pins.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_version": "44.20260730.1",
                "source_date_epoch": 0,
                "bazzite": {
                    "repository": "https://github.com/ublue-os/bazzite",
                    "stable_release": "44.20260730",
                    "commit": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                },
                "steam_presets": {
                    "repository": "https://gitlab.com/evlaV/steamdeck-kde-presets",
                    "tag": "0.30",
                    "commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                },
                "inputs": dict(sorted(input_hashes.items())),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return steam, bazzite, pins


class CompilerIdentityTests(unittest.TestCase):
    def test_builds_exact_vapor_identity_without_non_desktop_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            archive = temporary / "vapor.tar.gz"

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vapor_theme",
                    "build",
                    "--steam-source",
                    str(steam),
                    "--bazzite-source",
                    str(bazzite),
                    "--pins",
                    str(pins),
                    "--output",
                    str(archive),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            with tarfile.open(archive, "r:gz") as release:
                names = release.getnames()
                root = "vapor-44.20260730.1"
                metadata_member = release.extractfile(
                    f"{root}/payload/plasma/look-and-feel/"
                    "com.valve.vapor.desktop/metadata.json"
                )
                self.assertIsNotNone(metadata_member)
                metadata = json.load(metadata_member)

            self.assertEqual(metadata["KPlugin"]["Id"], "com.valve.vapor.desktop")
            self.assertEqual(metadata["KPlugin"]["Name"], "Vapor")
            self.assertEqual(metadata["KPlugin"]["Version"], "44.20260730.1")
            self.assertEqual(
                metadata["KPackageStructure"],
                "Plasma/LookAndFeel",
            )
            self.assertIn(
                f"{root}/payload/plasma/desktoptheme/Vapor/metadata.json",
                names,
            )
            self.assertIn(
                f"{root}/payload/color-schemes/Vapor.colors",
                names,
            )
            for forbidden in (
                "com.valve.vapor.deck",
                "deck_icon",
                "deck_logo",
                "vgui",
                "konsole",
                "gtk-",
                "steamos-add-to-steam",
                "kscreenlockerrc",
                "gaming",
                "gamescope",
            ):
                self.assertFalse(
                    any(forbidden in name.lower() for name in names),
                    f"forbidden release entry contains {forbidden!r}: {names}",
                )

    def test_plasma_style_delegates_accent_colors_to_system_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            archive = temporary / "vapor.tar.gz"

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vapor_theme",
                    "build",
                    "--steam-source",
                    str(steam),
                    "--bazzite-source",
                    str(bazzite),
                    "--pins",
                    str(pins),
                    "--output",
                    str(archive),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            with tarfile.open(archive, "r:gz") as release:
                names = set(release.getnames())

            root = "vapor-44.20260730.1/payload"
            self.assertNotIn(
                f"{root}/plasma/desktoptheme/Vapor/colors",
                names,
            )
            self.assertIn(f"{root}/color-schemes/Vapor.colors", names)

    def test_builds_reproducible_portable_visual_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            first_archive = temporary / "first.tar.gz"
            second_archive = temporary / "second.tar.gz"

            def build(output: Path) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "vapor_theme",
                        "build",
                        "--steam-source",
                        str(steam),
                        "--bazzite-source",
                        str(bazzite),
                        "--pins",
                        str(pins),
                        "--output",
                        str(output),
                    ],
                    cwd=REPO_ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                )

            first = build(first_archive)
            self.assertEqual(first.returncode, 0, first.stderr)
            for source_file in (*steam.rglob("*"), *bazzite.rglob("*")):
                if source_file.is_file():
                    os.utime(source_file, (1_700_000_123, 1_700_000_123))
            second = build(second_archive)
            self.assertEqual(second.returncode, 0, second.stderr)

            self.assertEqual(
                hashlib.sha256(first_archive.read_bytes()).hexdigest(),
                hashlib.sha256(second_archive.read_bytes()).hexdigest(),
            )

            root = "vapor-44.20260730.1"
            with tarfile.open(first_archive, "r:gz") as release:
                members = release.getmembers()
                names = {member.name for member in members}

                def read_text(relative_path: str) -> str:
                    member = release.extractfile(f"{root}/{relative_path}")
                    self.assertIsNotNone(member)
                    return member.read().decode("utf-8")

                required = {
                    f"{root}/payload/wallpapers/Vapor/metadata.json",
                    f"{root}/payload/wallpapers/Vapor/contents/images/3940x2160.jxl",
                    f"{root}/payload/icons/hicolor/scalable/places/vapor-bazzite.svg",
                    f"{root}/manifest.json",
                    f"{root}/provenance.json",
                    f"{root}/THIRD_PARTY_NOTICES.md",
                    f"{root}/LICENSES/AGPL-3.0-only.txt",
                    f"{root}/LICENSES/Apache-2.0.txt",
                    f"{root}/LICENSES/GPL-2.0.txt",
                }
                self.assertTrue(required <= names, required - names)
                self.assertEqual(
                    read_text("LICENSES/AGPL-3.0-only.txt"),
                    (REPO_ROOT / "LICENSE").read_text(encoding="utf-8"),
                )

                global_theme = "payload/plasma/look-and-feel/com.valve.vapor.desktop/"
                folder_script = read_text(
                    global_theme
                    + "contents/plasmoidsetupscripts/org.kde.plasma.folder.js"
                )
                kickoff_script = read_text(
                    global_theme
                    + "contents/plasmoidsetupscripts/org.kde.plasma.kickoff.js"
                )
                tray_script = read_text(
                    global_theme + "contents/plasmoidsetupscripts/"
                    "org.kde.plasma.systemtray.js"
                )
                splash = read_text(global_theme + "contents/splash/Splash.qml")
                defaults = read_text(global_theme + "contents/defaults")
                plasmarc = read_text("payload/plasma/desktoptheme/Vapor/plasmarc")
                manifest = json.loads(read_text("manifest.json"))
                provenance = json.loads(read_text("provenance.json"))
                wallpaper_metadata = json.loads(
                    read_text("payload/wallpapers/Vapor/metadata.json")
                )
                notices = read_text("THIRD_PARTY_NOTICES.md")

            self.assertIn('userDataPath("data",', folder_script)
            self.assertIn(
                "wallpapers/Vapor/contents/images/3940x2160.jxl", folder_script
            )
            self.assertNotIn("/usr/", folder_script)
            self.assertIn('"vapor-bazzite"', kickoff_script)
            self.assertNotIn("distributor-logo-steamdeck", kickoff_script)
            self.assertIn(
                'applet.writeConfig("scaleIconsToFit", true)',
                tray_script,
            )
            self.assertIn("applet.reloadConfig()", tray_script)
            self.assertNotIn("SystrayContainmentId", tray_script)
            self.assertIn("bazzite_logo.svgz", splash)
            self.assertNotIn("deck_logo.svgz", splash)
            self.assertIn("[Wallpaper]\nImage=Vapor", defaults)
            self.assertIn(
                "[ksplashrc][KSplash]\n"
                "Engine=KSplashQML\n"
                "Theme=com.valve.vapor.desktop",
                defaults,
            )
            self.assertNotIn("\n[KSplash]\n", defaults)
            self.assertIn("[AdaptiveTransparency]\nenabled=true", plasmarc)
            self.assertEqual(
                wallpaper_metadata["KPackageStructure"],
                "Wallpaper/Images",
            )
            self.assertEqual(manifest["version"], "44.20260730.1")
            self.assertIn(
                "plasma/look-and-feel/com.valve.vapor.desktop/metadata.json",
                manifest["files"],
            )
            self.assertEqual(
                provenance["source_pins"]["bazzite"]["commit"],
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            )
            self.assertEqual(
                provenance["source_pins"],
                json.loads(pins.read_text(encoding="utf-8")),
            )
            self.assertEqual(
                notices,
                (REPO_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8"),
            )
            self.assertTrue(all(member.uid == 0 for member in members))
            self.assertTrue(all(member.gid == 0 for member in members))
            self.assertTrue(all(member.mtime == 0 for member in members))
            self.assertTrue(
                all(member.isfile() or member.isdir() for member in members)
            )

    def test_applies_declared_theme_patches_and_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            archive = temporary / "vapor.tar.gz"

            def build(output: Path) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "vapor_theme",
                        "build",
                        "--steam-source",
                        str(steam),
                        "--bazzite-source",
                        str(bazzite),
                        "--pins",
                        str(pins),
                        "--output",
                        str(output),
                    ],
                    cwd=REPO_ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                )

            result = build(archive)
            self.assertEqual(result.returncode, 0, result.stderr)
            with tarfile.open(archive, "r:gz") as release:
                splash_member = release.extractfile(
                    "vapor-44.20260730.1/payload/plasma/look-and-feel/"
                    "com.valve.vapor.desktop/contents/splash/Splash.qml"
                )
                self.assertIsNotNone(splash_member)
                splash = splash_member.read().decode("utf-8")
            self.assertIn("asynchronous: true", splash)
            self.assertIn("width: 50", splash)

            (bazzite / "spec_files/steamdeck-kde-presets/splash.patch").write_text(
                "tampered\n",
                encoding="utf-8",
            )
            rejected_archive = temporary / "rejected.tar.gz"
            rejected = build(rejected_archive)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("unexpected source pin", rejected.stderr.lower())
            self.assertFalse(rejected_archive.exists())

    def test_rejects_an_unpinned_patch_that_deletes_a_vapor_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            target = "usr/share/plasma/desktoptheme/Vapor/colors"
            original = (steam / target).read_text(encoding="utf-8")
            deletion_patch = "".join(
                difflib.unified_diff(
                    original.splitlines(keepends=True),
                    [],
                    fromfile=f"a/{target}",
                    tofile="/dev/null",
                )
            )
            write_text(
                bazzite,
                "spec_files/steamdeck-kde-presets/delete-vapor-colors.patch",
                deletion_patch,
            )
            desktop_spec = (
                bazzite
                / "spec_files"
                / "steamdeck-kde-presets"
                / "steamdeck-kde-presets-desktop.spec"
            )
            desktop_spec.write_text(
                desktop_spec.read_text(encoding="utf-8")
                + "Patch5: delete-vapor-colors.patch\n",
                encoding="utf-8",
                newline="\n",
            )
            pinned = json.loads(pins.read_text(encoding="utf-8"))
            pinned["inputs"][
                "bazzite:spec_files/steamdeck-kde-presets/"
                "steamdeck-kde-presets-desktop.spec"
            ] = sha256(desktop_spec)
            pins.write_text(
                json.dumps(pinned, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            archive = temporary / "should-not-build.tar.gz"

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vapor_theme",
                    "build",
                    "--steam-source",
                    str(steam),
                    "--bazzite-source",
                    str(bazzite),
                    "--pins",
                    str(pins),
                    "--output",
                    str(archive),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("declared theme patch is not pinned", result.stderr.lower())
            self.assertFalse(archive.exists())

    def test_rejects_an_unpinned_binary_patch_for_a_vapor_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            target = "usr/share/plasma/desktoptheme/Vapor/widgets/preview.bin"
            patch_text = git_binary_patch(
                steam,
                target,
                b"\x00vapor-before\xff",
                b"\x00vapor-after\xfe",
            )
            self.assertIn("GIT binary patch", patch_text)
            write_text(
                bazzite,
                "spec_files/steamdeck-kde-presets/binary-vapor.patch",
                patch_text,
            )
            desktop_spec = (
                bazzite
                / "spec_files"
                / "steamdeck-kde-presets"
                / "steamdeck-kde-presets-desktop.spec"
            )
            desktop_spec.write_text(
                desktop_spec.read_text(encoding="utf-8")
                + "Patch5: binary-vapor.patch\n",
                encoding="utf-8",
                newline="\n",
            )
            pinned = json.loads(pins.read_text(encoding="utf-8"))
            pinned["inputs"][f"steam:{target}"] = sha256(steam / target)
            pinned["inputs"][
                "bazzite:spec_files/steamdeck-kde-presets/"
                "steamdeck-kde-presets-desktop.spec"
            ] = sha256(desktop_spec)
            pins.write_text(
                json.dumps(pinned, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            archive = temporary / "should-not-build.tar.gz"

            result = run_vapor_cli(
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

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("declared theme patch is not pinned", result.stderr.lower())
            self.assertFalse(archive.exists())

    def test_ignores_an_unrelated_binary_rpm_patch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            target = "usr/lib/rpm/macros.d/binary-rpm-data.bin"
            patch_text = git_binary_patch(
                steam,
                target,
                b"\x00rpm-before\xff",
                b"\x00rpm-after\xfe",
            )
            self.assertIn("GIT binary patch", patch_text)
            write_text(
                bazzite,
                "spec_files/steamdeck-kde-presets/binary-rpm.patch",
                patch_text,
            )
            desktop_spec = (
                bazzite
                / "spec_files"
                / "steamdeck-kde-presets"
                / "steamdeck-kde-presets-desktop.spec"
            )
            desktop_spec.write_text(
                desktop_spec.read_text(encoding="utf-8") + "Patch5: binary-rpm.patch\n",
                encoding="utf-8",
                newline="\n",
            )
            pinned = json.loads(pins.read_text(encoding="utf-8"))
            pinned["inputs"][
                "bazzite:spec_files/steamdeck-kde-presets/"
                "steamdeck-kde-presets-desktop.spec"
            ] = sha256(desktop_spec)
            pins.write_text(
                json.dumps(pinned, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            archive = temporary / "vapor.tar.gz"

            result = run_vapor_cli(
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

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(archive.is_file())

    def test_ignores_a_patch_for_a_color_scheme_sibling_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            target = "usr/share/color-schemes/Vapor.colors.backup"
            before = "backup before\n"
            write_text(steam, target, before)
            write_patch(
                bazzite,
                "color-scheme-backup.patch",
                target,
                before,
                "backup after\n",
            )
            desktop_spec = (
                bazzite
                / "spec_files"
                / "steamdeck-kde-presets"
                / "steamdeck-kde-presets-desktop.spec"
            )
            desktop_spec.write_text(
                desktop_spec.read_text(encoding="utf-8")
                + "Patch5: color-scheme-backup.patch\n",
                encoding="utf-8",
                newline="\n",
            )
            pinned = json.loads(pins.read_text(encoding="utf-8"))
            pinned["inputs"][
                "bazzite:spec_files/steamdeck-kde-presets/"
                "steamdeck-kde-presets-desktop.spec"
            ] = sha256(desktop_spec)
            pins.write_text(
                json.dumps(pinned, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            archive = temporary / "vapor.tar.gz"

            result = run_vapor_cli(
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

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(archive.is_file())

    def test_rejects_unpinned_files_inside_selected_theme_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            write_text(
                steam,
                "usr/share/plasma/look-and-feel/"
                "com.valve.vapor.deck.desktop/contents/unexpected.sh",
                "unexpected\n",
            )
            archive = temporary / "vapor.tar.gz"

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vapor_theme",
                    "build",
                    "--steam-source",
                    str(steam),
                    "--bazzite-source",
                    str(bazzite),
                    "--pins",
                    str(pins),
                    "--output",
                    str(archive),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unpinned source input", result.stderr.lower())
            self.assertFalse(archive.exists())

    def test_rejects_pinned_input_reached_through_parent_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            steam, bazzite, pins = create_source_fixture(temporary)
            wallpaper_directory = (
                bazzite
                / "system_files"
                / "desktop"
                / "kinoite"
                / "usr"
                / "share"
                / "wallpapers"
            )
            outside = temporary / "outside-wallpapers"
            wallpaper_directory.rename(outside)
            try:
                wallpaper_directory.symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(
                    "creating directory symlinks requires Windows developer "
                    f"mode; Fedora CI exercises this: {error}"
                )
            archive = temporary / "escaped.tar.gz"

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vapor_theme",
                    "build",
                    "--steam-source",
                    str(steam),
                    "--bazzite-source",
                    str(bazzite),
                    "--pins",
                    str(pins),
                    "--output",
                    str(archive),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("escapes its source root", result.stderr.lower())
            self.assertFalse(archive.exists())


if __name__ == "__main__":
    unittest.main()
