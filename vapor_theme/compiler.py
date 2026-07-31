from __future__ import annotations

import gzip
import shutil
import tarfile
import tempfile
from pathlib import Path

from vapor_theme.bundle_contract import (
    GLOBAL_THEME_ID,
    LAUNCHER_ICON,
    WALLPAPER_IMAGE,
)
from vapor_theme.io_utils import (
    ensure_json_object,
    read_json_object,
    run_checked,
    sha256_file,
    write_json_file,
)
from vapor_theme.records import SourcePins, parse_source_pins
from vapor_theme.source_contract import (
    declared_theme_patches,
    required_source_path,
    verify_pinned_source_inputs,
)


def _copy_text(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        source.read_text(encoding="utf-8"),
        encoding="utf-8",
        newline="\n",
    )


def _replace_ini_section(
    source: str,
    section: str,
    entries: list[str],
) -> str:
    section_header = f"[{section}]"
    lines = source.splitlines()
    result: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index] != section_header:
            result.append(lines[index])
            index += 1
            continue
        index += 1
        while index < len(lines) and not lines[index].startswith("["):
            index += 1
    while result and not result[-1]:
        result.pop()
    if not entries:
        return "\n".join(result) + "\n"
    if result:
        result.append("")
    result.extend([section_header, *entries])
    return "\n".join(result) + "\n"


def _write_portable_setup_scripts(destination: Path) -> None:
    setup = destination / "contents" / "plasmoidsetupscripts"
    write_scripts = {
        "org.kde.plasma.folder.js": (
            'applet.wallpaperPlugin = "org.kde.image";\n'
            'applet.currentConfigGroup = ["Wallpaper", "org.kde.image", '
            '"General"];\n'
            'const wallpaper = userDataPath("data", '
            f'"{WALLPAPER_IMAGE}");\n'
            'const wallpaperUrl = encodeURI("file://" + wallpaper);\n'
            'applet.writeConfig("Image", wallpaperUrl);\n'
            "applet.reloadConfig();\n"
        ),
        "org.kde.plasma.kickoff.js": (
            'applet.currentConfigGroup = ["General"];\n'
            f'applet.writeConfig("icon", "{LAUNCHER_ICON}");\n'
            "applet.reloadConfig();\n"
        ),
        "org.kde.plasma.systemtray.js": (
            'applet.currentConfigGroup = ["General"];\n'
            'applet.writeConfig("scaleIconsToFit", true);\n'
            "applet.reloadConfig();\n"
        ),
    }
    for name, content in write_scripts.items():
        path = setup / name
        path.parent.mkdir(parents=True, exist_ok=True)
        guarded_content = (
            'if (typeof applet !== "undefined" && applet) {\n'
            + "".join(f"    {line}" for line in content.splitlines(keepends=True))
            + "}\n"
        )
        path.write_text(guarded_content, encoding="utf-8", newline="\n")


def _copy_global_theme(
    steam_source: Path,
    bazzite_source: Path,
    payload: Path,
    version: str,
) -> None:
    source = required_source_path(
        steam_source,
        "usr/share/plasma/look-and-feel/com.valve.vapor.deck.desktop",
    )
    destination = payload / "plasma" / "look-and-feel" / GLOBAL_THEME_ID
    shutil.copytree(source, destination)

    metadata_path = destination / "metadata.json"
    metadata = read_json_object(metadata_path)
    plugin = ensure_json_object(
        metadata.setdefault("KPlugin", {}),
        label="Global Theme KPlugin metadata",
    )
    plugin["Id"] = GLOBAL_THEME_ID
    plugin["Name"] = "Vapor"
    plugin["Description"] = "Vapor - SteamOS theme variant of KDE Breeze Dark"
    plugin["Version"] = version
    metadata["KPackageStructure"] = "Plasma/LookAndFeel"
    write_json_file(metadata_path, metadata)

    defaults_path = destination / "contents" / "defaults"
    defaults = defaults_path.read_text(encoding="utf-8")
    defaults = _replace_ini_section(defaults, "KSplash", [])
    defaults = _replace_ini_section(defaults, "Wallpaper", ["Image=Vapor"])
    defaults = _replace_ini_section(
        defaults,
        "ksplashrc][KSplash",
        ["Engine=KSplashQML", f"Theme={GLOBAL_THEME_ID}"],
    )
    defaults_path.write_text(defaults, encoding="utf-8", newline="\n")

    deck_logo = destination / "contents" / "splash" / "images" / "deck_logo.svgz"
    if deck_logo.exists():
        deck_logo.unlink()
    deck_icons = destination / "contents" / "icons"
    if deck_icons.exists():
        shutil.rmtree(deck_icons)
    bazzite_logo = required_source_path(
        bazzite_source,
        "spec_files/steamdeck-kde-presets/bazzite_logo.svgz",
    )
    logo_destination = (
        destination / "contents" / "splash" / "images" / "bazzite_logo.svgz"
    )
    logo_destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(bazzite_logo, logo_destination)

    splash_path = destination / "contents" / "splash" / "Splash.qml"
    splash = splash_path.read_text(encoding="utf-8")
    splash_path.write_text(
        splash.replace("deck_logo.svgz", "bazzite_logo.svgz"),
        encoding="utf-8",
        newline="\n",
    )
    _write_portable_setup_scripts(destination)


def _copy_plasma_style(
    steam_source: Path,
    bazzite_source: Path,
    payload: Path,
    version: str,
) -> None:
    source = required_source_path(
        steam_source,
        "usr/share/plasma/desktoptheme/Vapor",
    )
    destination = payload / "plasma" / "desktoptheme" / "Vapor"
    shutil.copytree(source, destination)

    metadata_path = destination / "metadata.json"
    metadata = read_json_object(metadata_path)
    plugin = ensure_json_object(
        metadata.setdefault("KPlugin", {}),
        label="Plasma Style KPlugin metadata",
    )
    plugin["Version"] = version
    write_json_file(metadata_path, metadata)

    plasmarc = required_source_path(
        bazzite_source,
        "spec_files/steamdeck-kde-presets/plasmarc",
    )
    _copy_text(plasmarc, destination / "plasmarc")
    colors = destination / "colors"
    if colors.exists():
        colors.write_text(
            colors.read_text(encoding="utf-8"),
            encoding="utf-8",
            newline="\n",
        )


def _copy_color_scheme(steam_source: Path, payload: Path) -> None:
    source = required_source_path(
        steam_source,
        "usr/share/color-schemes/Vapor.colors",
    )
    destination = payload / "color-schemes" / "Vapor.colors"
    _copy_text(source, destination)


def _copy_wallpaper(bazzite_source: Path, payload: Path) -> None:
    wallpaper_root = payload / "wallpapers" / "Vapor"
    wallpaper_root.mkdir(parents=True)
    write_json_file(
        wallpaper_root / "metadata.json",
        {
            "KPackageStructure": "Wallpaper/Images",
            "KPlugin": {
                "Authors": [{"Name": "Bazzite contributors"}],
                "Id": "Vapor",
                "License": "Apache-2.0",
                "Name": "Convergence",
                "Website": "https://github.com/ublue-os/bazzite",
            },
        },
    )
    source = required_source_path(
        bazzite_source,
        "system_files/desktop/kinoite/usr/share/wallpapers/convergence.jxl",
    )
    destination = payload / WALLPAPER_IMAGE
    destination.parent.mkdir(parents=True)
    shutil.copyfile(source, destination)


def _copy_launcher_icon(bazzite_source: Path, payload: Path) -> None:
    source = required_source_path(
        bazzite_source,
        "system_files/overrides/usr/share/icons/hicolor/scalable/places/"
        "distributor-logo.svg",
    )
    destination = (
        payload / "icons" / "hicolor" / "scalable" / "places" / f"{LAUNCHER_ICON}.svg"
    )
    destination.parent.mkdir(parents=True)
    shutil.copyfile(source, destination)


def _prepare_patched_steam_source(
    steam_source: Path,
    bazzite_source: Path,
    destination: Path,
) -> None:
    shutil.copytree(
        steam_source,
        destination,
        symlinks=True,
        ignore=shutil.ignore_patterns(".git"),
    )
    for patch in declared_theme_patches(bazzite_source):
        run_checked(
            ["git"],
            "apply",
            "--recount",
            "--whitespace=nowarn",
            str(patch.resolve()),
            failure=f"failed to apply {patch.name}",
            working_directory=destination,
        )


def _write_bundle_metadata(
    release_root: Path,
    payload: Path,
    pins: SourcePins,
) -> None:
    files = {
        path.relative_to(payload).as_posix(): sha256_file(path)
        for path in sorted(payload.rglob("*"))
        if path.is_file()
    }
    write_json_file(
        release_root / "manifest.json",
        {
            "files": files,
            "global_theme_id": GLOBAL_THEME_ID,
            "schema_version": 1,
            "version": str(pins["project_version"]),
        },
    )
    write_json_file(
        release_root / "provenance.json",
        {
            "project_version": str(pins["project_version"]),
            "schema_version": 1,
            "source_pins": pins,
        },
    )
    _copy_text(
        Path(__file__).resolve().parent.parent / "THIRD_PARTY_NOTICES.md",
        release_root / "THIRD_PARTY_NOTICES.md",
    )


def _copy_license_files(bazzite_source: Path, release_root: Path) -> None:
    licenses = release_root / "LICENSES"
    licenses.mkdir()
    shutil.copyfile(
        Path(__file__).resolve().parent.parent / "LICENSE",
        licenses / "AGPL-3.0-only.txt",
    )
    shutil.copyfile(
        required_source_path(bazzite_source, "LICENSE"),
        licenses / "Apache-2.0.txt",
    )
    shutil.copyfile(
        required_source_path(
            bazzite_source,
            "spec_files/steamdeck-kde-presets/LICENSE",
        ),
        licenses / "GPL-2.0.txt",
    )


def _write_runtime_files(release_root: Path) -> None:
    library = release_root / "lib"
    library.mkdir()
    runtime_source = Path(__file__).with_name("install_runtime.py")
    _copy_text(runtime_source, library / "vapor_installer.py")
    runtime_package = library / "vapor_theme"
    runtime_package.mkdir()
    _copy_text(Path(__file__).with_name("__init__.py"), runtime_package / "__init__.py")
    _copy_text(
        Path(__file__).with_name("bundle_contract.py"),
        runtime_package / "bundle_contract.py",
    )
    for module_name in ("io_utils.py", "records.py"):
        _copy_text(
            Path(__file__).with_name(module_name),
            runtime_package / module_name,
        )
    template_directory = Path(__file__).resolve().parent.parent / "templates"
    for name in ("install.sh", "uninstall.sh"):
        _copy_text(
            required_source_path(template_directory, name),
            release_root / name,
        )


def _write_reproducible_archive(
    release_root: Path,
    output_path: Path,
    *,
    mtime: int,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    paths = [release_root, *sorted(release_root.rglob("*"))]
    with (
        output_path.open("wb") as raw_output,
        gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=raw_output,
            mtime=mtime,
        ) as compressed,
        tarfile.open(
            fileobj=compressed,
            mode="w",
            format=tarfile.PAX_FORMAT,
        ) as archive,
    ):
        for path in paths:
            relative = path.relative_to(release_root.parent).as_posix()
            info = tarfile.TarInfo(relative)
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = mtime
            if path.is_dir():
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                archive.addfile(info)
            else:
                info.type = tarfile.REGTYPE
                info.mode = (
                    0o755 if path.name in {"install.sh", "uninstall.sh"} else 0o644
                )
                info.size = path.stat().st_size
                with path.open("rb") as source:
                    archive.addfile(info, source)


def build_release(
    *,
    steam_source: Path,
    bazzite_source: Path,
    pins_path: Path,
    output_path: Path,
) -> None:
    pins = parse_source_pins(read_json_object(pins_path, label="pins JSON"))
    verify_pinned_source_inputs(
        pins,
        steam_source=steam_source,
        bazzite_source=bazzite_source,
    )
    version = str(pins["project_version"])
    root_name = f"vapor-{version}"

    with tempfile.TemporaryDirectory(prefix="vapor-build-") as temporary:
        temporary_root = Path(temporary)
        patched_steam_source = temporary_root / "patched-steam-source"
        _prepare_patched_steam_source(
            steam_source,
            bazzite_source,
            patched_steam_source,
        )
        release_root = temporary_root / root_name
        payload = release_root / "payload"
        payload.mkdir(parents=True)

        _copy_global_theme(
            patched_steam_source,
            bazzite_source,
            payload,
            version,
        )
        _copy_plasma_style(
            patched_steam_source,
            bazzite_source,
            payload,
            version,
        )
        _copy_color_scheme(patched_steam_source, payload)
        _copy_wallpaper(bazzite_source, payload)
        _copy_launcher_icon(bazzite_source, payload)
        _write_bundle_metadata(release_root, payload, pins)
        _copy_license_files(bazzite_source, release_root)
        _write_runtime_files(release_root)

        _write_reproducible_archive(
            release_root,
            output_path,
            mtime=int(pins.get("source_date_epoch", 0)),
        )
