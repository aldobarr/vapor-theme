from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from vapor_theme.bundle_contract import safe_relative_path
from vapor_theme.io_utils import sha256_file
from vapor_theme.records import SourcePins

DESKTOP_SPEC = "spec_files/steamdeck-kde-presets/steamdeck-kde-presets-desktop.spec"
THEME_PATCH_FILES = ("usr/share/color-schemes/Vapor.colors",)
THEME_PATCH_DIRECTORIES = (
    "usr/share/plasma/desktoptheme/Vapor",
    "usr/share/plasma/look-and-feel/com.valve.vapor.deck.desktop",
)
STEAM_TRACKED_ROOTS = (
    "usr/share/color-schemes/Vapor.colors",
    "usr/share/plasma/desktoptheme/Vapor",
    "usr/share/plasma/look-and-feel/com.valve.vapor.deck.desktop",
)
BAZZITE_REQUIRED_INPUTS = (
    "LICENSE",
    "spec_files/steamdeck-kde-presets/LICENSE",
    "spec_files/steamdeck-kde-presets/bazzite_logo.svgz",
    "spec_files/steamdeck-kde-presets/plasmarc",
    DESKTOP_SPEC,
    "system_files/desktop/kinoite/usr/share/wallpapers/convergence.jxl",
    "system_files/overrides/usr/share/icons/hicolor/scalable/places/"
    "distributor-logo.svg",
)
NON_VISUAL_FINGERPRINT_INPUTS = {
    "bazzite:LICENSE",
    "bazzite:spec_files/steamdeck-kde-presets/LICENSE",
    f"bazzite:{DESKTOP_SPEC}",
}


def required_source_path(root: Path, relative_path: str) -> Path:
    relative = safe_relative_path(relative_path, label="source input path")
    path = root.joinpath(*relative.parts)
    if not path.exists():
        raise FileNotFoundError(f"required source input is missing: {path}")
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(
            f"source input escapes its source root: {relative_path}"
        ) from error
    return path


def _desktop_spec_text(bazzite_source: Path) -> str:
    return required_source_path(bazzite_source, DESKTOP_SPEC).read_text(
        encoding="utf-8"
    )


def declared_theme_patches(bazzite_source: Path) -> tuple[Path, ...]:
    patch_declarations = re.findall(
        r"(?m)^\s*Patch(\d*):\s*(\S+)\s*$",
        _desktop_spec_text(bazzite_source),
    )
    patches_by_number: dict[int, str] = {}
    for number_text, patch_name in patch_declarations:
        patch_number = int(number_text or "0")
        if patch_number in patches_by_number:
            raise ValueError(f"desktop spec declares Patch{patch_number} twice")
        patches_by_number[patch_number] = patch_name

    selected: list[Path] = []
    for _, patch_name in sorted(patches_by_number.items()):
        patch = required_source_path(
            bazzite_source,
            f"spec_files/steamdeck-kde-presets/{patch_name}",
        )
        patch_text = patch.read_text(encoding="utf-8")
        target_paths = re.findall(
            r"(?m)^(?:---\s+a/|\+\+\+\s+b/|(?:rename|copy)\s+(?:from|to)\s+)(\S+)",
            patch_text,
        )
        target_paths.extend(
            path
            for paths in re.findall(
                r"(?m)^diff --git a/(\S+) b/(\S+)$",
                patch_text,
            )
            for path in paths
        )
        if any(
            target in THEME_PATCH_FILES
            or any(
                target == directory or target.startswith(f"{directory}/")
                for directory in THEME_PATCH_DIRECTORIES
            )
            for target in target_paths
        ):
            selected.append(patch)
    return tuple(selected)


def steam_preset_tag(bazzite_source: Path) -> str:
    match = re.search(
        r"(?m)^\s*%define\s+packagever\s+(\S+)\s*$",
        _desktop_spec_text(bazzite_source),
    )
    if match is None:
        raise ValueError("desktop spec does not declare %define packagever")
    return match.group(1)


def tracked_source_files(
    *,
    steam_source: Path,
    bazzite_source: Path,
) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for relative_path in STEAM_TRACKED_ROOTS:
        candidate = required_source_path(steam_source, relative_path)
        paths = (candidate,) if candidate.is_file() else candidate.rglob("*")
        for path in paths:
            if path.is_symlink():
                raise ValueError(f"tracked Steam input is a symlink: {path}")
            if path.is_file():
                relative = path.relative_to(steam_source).as_posix()
                files[f"steam:{relative}"] = path

    bazzite_paths = {
        *(
            required_source_path(bazzite_source, relative)
            for relative in BAZZITE_REQUIRED_INPUTS
        ),
        *declared_theme_patches(bazzite_source),
    }
    for path in bazzite_paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"tracked Bazzite input is not a regular file: {path}")
        relative = path.relative_to(bazzite_source).as_posix()
        files[f"bazzite:{relative}"] = path
    return dict(sorted(files.items()))


def collect_source_inputs(
    *,
    steam_source: Path,
    bazzite_source: Path,
) -> dict[str, str]:
    return _hash_source_files(
        tracked_source_files(
            steam_source=steam_source,
            bazzite_source=bazzite_source,
        )
    )


def _hash_source_files(files: dict[str, Path]) -> dict[str, str]:
    return {name: sha256_file(path) for name, path in files.items()}


def theme_fingerprint(
    inputs: dict[str, str],
    *,
    bazzite_source: Path,
    steam_commit: str,
) -> str:
    look_and_feel = "steam:usr/share/plasma/look-and-feel/com.valve.vapor.deck.desktop/"
    excluded_prefixes = (
        f"{look_and_feel}contents/icons/",
        f"{look_and_feel}contents/plasmoidsetupscripts/",
    )
    excluded_files = {
        f"{look_and_feel}contents/splash/images/deck_logo.svgz",
    }
    payload_inputs = {
        path: digest
        for path, digest in inputs.items()
        if path not in NON_VISUAL_FINGERPRINT_INPUTS
        and path not in excluded_files
        and not any(path.startswith(prefix) for prefix in excluded_prefixes)
    }
    ordered_theme_patches = [
        f"bazzite:{path.relative_to(bazzite_source).as_posix()}"
        for path in declared_theme_patches(bazzite_source)
    ]
    encoded = json.dumps(
        {
            "ordered_theme_patches": ordered_theme_patches,
            "payload_inputs": payload_inputs,
            "steam_presets": {
                "commit": steam_commit,
                "tag": steam_preset_tag(bazzite_source),
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verify_pinned_source_inputs(
    pins: SourcePins,
    *,
    steam_source: Path,
    bazzite_source: Path,
) -> None:
    expected = pins["inputs"]
    tracked = tracked_source_files(
        steam_source=steam_source,
        bazzite_source=bazzite_source,
    )
    tracked_names = set(tracked)
    expected_names = set(expected)
    missing = sorted(tracked_names - expected_names)
    if missing:
        input_name = missing[0]
        if input_name.startswith("steam:"):
            raise ValueError(f"unpinned source input: {input_name}")
        relative = input_name.removeprefix("bazzite:")
        if relative in BAZZITE_REQUIRED_INPUTS:
            raise ValueError(f"required source input is not pinned: {input_name}")
        raise ValueError(f"declared theme patch is not pinned: {input_name}")
    unexpected = sorted(expected_names - tracked_names)
    if unexpected:
        raise ValueError(f"unexpected source pin: {unexpected[0]}")

    actual = _hash_source_files(tracked)
    for input_name, actual_hash in actual.items():
        expected_hash = expected[input_name]
        if actual_hash != expected_hash:
            raise ValueError(
                "hash mismatch for "
                f"{input_name}: expected {expected_hash}, got {actual_hash}"
            )
