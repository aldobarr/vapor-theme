from __future__ import annotations

from pathlib import Path, PurePosixPath

from vapor_theme.io_utils import read_json_object, sha256_file
from vapor_theme.records import BundleManifest, DottedVersion

GLOBAL_THEME_ID = "com.valve.vapor.desktop"
WALLPAPER_IMAGE = "wallpapers/Vapor/contents/images/3940x2160.jxl"
LAUNCHER_ICON = "vapor-bazzite"
EXPECTED_PAYLOAD_FILES = frozenset(
    {
        "color-schemes/Vapor.colors",
        f"icons/hicolor/scalable/places/{LAUNCHER_ICON}.svg",
        "plasma/desktoptheme/Vapor/colors",
        "plasma/desktoptheme/Vapor/metadata.json",
        "plasma/desktoptheme/Vapor/plasmarc",
        f"plasma/look-and-feel/{GLOBAL_THEME_ID}/contents/defaults",
        (
            f"plasma/look-and-feel/{GLOBAL_THEME_ID}/contents/"
            "plasmoidsetupscripts/org.kde.plasma.folder.js"
        ),
        (
            f"plasma/look-and-feel/{GLOBAL_THEME_ID}/contents/"
            "plasmoidsetupscripts/org.kde.plasma.kickoff.js"
        ),
        (
            f"plasma/look-and-feel/{GLOBAL_THEME_ID}/contents/"
            "plasmoidsetupscripts/org.kde.plasma.systemtray.js"
        ),
        f"plasma/look-and-feel/{GLOBAL_THEME_ID}/contents/previews/preview.png",
        (
            f"plasma/look-and-feel/{GLOBAL_THEME_ID}/contents/"
            "splash/images/bazzite_logo.svgz"
        ),
        (
            f"plasma/look-and-feel/{GLOBAL_THEME_ID}/contents/"
            "splash/images/busywidget.svgz"
        ),
        f"plasma/look-and-feel/{GLOBAL_THEME_ID}/contents/splash/Splash.qml",
        f"plasma/look-and-feel/{GLOBAL_THEME_ID}/metadata.json",
        WALLPAPER_IMAGE,
        "wallpapers/Vapor/metadata.json",
    }
)


def safe_relative_path(value: str, *, label: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if "\\" in value or path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe {label}: {value}")
    return path


def require_regular_file(
    root: Path,
    relative_path: str,
    *,
    missing: str,
) -> Path:
    relative = safe_relative_path(relative_path, label="required file path")
    path = root.joinpath(*relative.parts)
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"{missing}: {relative_path}")
    return path


def collect_regular_file_paths(
    root: Path,
    *,
    subject: str,
) -> set[str]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"{subject} must be a real directory")
    files: set[str] = set()
    for path in root.rglob("*"):
        relative_path = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError(f"{subject} contains a symlink: {relative_path}")
        if path.is_file():
            files.add(relative_path)
        elif not path.is_dir():
            raise ValueError(f"{subject} contains a special file: {relative_path}")
    return files


def verify_manifested_payload(
    payload: Path,
    manifest: BundleManifest,
) -> set[str]:
    """Verify that a payload exactly matches its ownership manifest."""
    actual_paths = collect_regular_file_paths(
        payload,
        subject="bundle payload",
    )

    files = manifest["files"]
    if actual_paths != set(files):
        missing = sorted(set(files) - actual_paths)
        unlisted = sorted(actual_paths - set(files))
        raise ValueError(
            f"manifest payload mismatch; missing={missing}, unlisted={unlisted}"
        )

    for relative_path, expected_hash in sorted(files.items()):
        posix_path = safe_relative_path(relative_path, label="manifest path")
        path = payload.joinpath(*posix_path.parts)
        if sha256_file(path) != expected_hash:
            raise ValueError(f"manifest hash mismatch: {relative_path}")
    return actual_paths


def validate_manifested_payload(
    payload: Path,
    manifest: BundleManifest,
) -> None:
    actual_paths = verify_manifested_payload(payload, manifest)
    validate_payload(payload, manifest["version"], actual_paths)


def _validate_global_theme(payload: Path, version: str) -> None:
    relative_base = f"plasma/look-and-feel/{GLOBAL_THEME_ID}"
    base = payload.joinpath(*PurePosixPath(relative_base).parts)
    metadata = read_json_object(
        require_regular_file(
            payload,
            f"{relative_base}/metadata.json",
            missing="required payload file is missing",
        )
    )
    plugin = metadata.get("KPlugin")
    if not isinstance(plugin, dict):
        raise ValueError("Global Theme metadata lacks KPlugin")
    required_metadata = {
        "Id": GLOBAL_THEME_ID,
        "Name": "Vapor",
        "Version": version,
    }
    for key, expected in required_metadata.items():
        if plugin.get(key) != expected:
            raise ValueError(f"Global Theme KPlugin.{key} must be {expected!r}")
    if metadata.get("KPackageStructure") != "Plasma/LookAndFeel":
        raise ValueError("Global Theme KPackageStructure must be 'Plasma/LookAndFeel'")
    DottedVersion.parse(version, "Global Theme version")
    for forbidden in (
        "manifest.json",
        "metadata.desktop",
        "contents/icons/deck_icon.png",
        "contents/splash/images/deck_logo.svgz",
    ):
        if (base / forbidden).exists():
            raise ValueError(f"Global Theme must not contain {forbidden}")

    required_owned_files = (
        "contents/defaults",
        "contents/previews/preview.png",
        "contents/splash/Splash.qml",
        "contents/splash/images/bazzite_logo.svgz",
        "contents/plasmoidsetupscripts/org.kde.plasma.folder.js",
        "contents/plasmoidsetupscripts/org.kde.plasma.kickoff.js",
        "contents/plasmoidsetupscripts/org.kde.plasma.systemtray.js",
    )
    for relative_path in required_owned_files:
        if not (base / relative_path).is_file():
            raise ValueError(
                f"Vapor must own {relative_path}; Breeze fallback is not acceptable"
            )

    defaults = (base / "contents" / "defaults").read_text(encoding="utf-8")
    required_defaults = (
        "ColorScheme=Vapor",
        "[plasmarc][Theme]\nname=Vapor",
        "[Wallpaper]\nImage=Vapor",
        f"Theme={GLOBAL_THEME_ID}",
    )
    for expected in required_defaults:
        if expected not in defaults:
            raise ValueError(f"Global Theme defaults lack {expected!r}")
    if "\n[KSplash]\n" in defaults:
        raise ValueError("Global Theme contains a stale top-level KSplash group")

    splash = (base / "contents" / "splash" / "Splash.qml").read_text(encoding="utf-8")
    if "bazzite_logo.svgz" not in splash or "deck_logo.svgz" in splash:
        raise ValueError("Global Theme splash does not resolve Bazzite branding")

    scripts = base / "contents" / "plasmoidsetupscripts"
    folder = (scripts / "org.kde.plasma.folder.js").read_text(encoding="utf-8")
    kickoff = (scripts / "org.kde.plasma.kickoff.js").read_text(encoding="utf-8")
    tray = (scripts / "org.kde.plasma.systemtray.js").read_text(encoding="utf-8")
    if WALLPAPER_IMAGE not in folder or "/usr/" in folder:
        raise ValueError("Folder View setup does not use the portable wallpaper")
    if f'"{LAUNCHER_ICON}"' not in kickoff:
        raise ValueError("Kickoff setup does not use the owned Bazzite icon")
    if (
        'applet.writeConfig("scaleIconsToFit", true)' not in tray
        or "SystrayContainmentId" in tray
    ):
        raise ValueError("system-tray setup is not compatible with Plasma 6")


def _validate_companions(payload: Path, version: str) -> None:
    style_path = require_regular_file(
        payload,
        "plasma/desktoptheme/Vapor/metadata.json",
        missing="required payload file is missing",
    )
    style = read_json_object(style_path)
    style_plugin = style.get("KPlugin")
    if (
        not isinstance(style_plugin, dict)
        or style_plugin.get("Name") != "Vapor"
        or style_plugin.get("Version") != version
    ):
        raise ValueError("Plasma Style metadata identity/version is invalid")
    if style.get("X-Plasma-API") != "6.0":
        raise ValueError("Plasma Style must declare X-Plasma-API 6.0")
    plasmarc = require_regular_file(
        payload,
        "plasma/desktoptheme/Vapor/plasmarc",
        missing="required payload file is missing",
    ).read_text(encoding="utf-8")
    if "[AdaptiveTransparency]\nenabled=true" not in plasmarc:
        raise ValueError("Plasma Style does not enable adaptive transparency")

    require_regular_file(
        payload,
        "color-schemes/Vapor.colors",
        missing="required payload file is missing",
    )
    wallpaper_metadata = read_json_object(
        require_regular_file(
            payload,
            "wallpapers/Vapor/metadata.json",
            missing="required payload file is missing",
        )
    )
    wallpaper_plugin = wallpaper_metadata.get("KPlugin")
    if (
        not isinstance(wallpaper_plugin, dict)
        or wallpaper_plugin.get("Id") != "Vapor"
        or wallpaper_plugin.get("Name") != "Convergence"
        or wallpaper_metadata.get("KPackageStructure") != "Wallpaper/Images"
    ):
        raise ValueError("Convergence wallpaper metadata is invalid")
    require_regular_file(
        payload,
        WALLPAPER_IMAGE,
        missing="required payload file is missing",
    )
    require_regular_file(
        payload,
        f"icons/hicolor/scalable/places/{LAUNCHER_ICON}.svg",
        missing="required payload file is missing",
    )


def validate_payload(
    payload: Path,
    version: str,
    actual_files: set[str],
) -> None:
    """Validate the complete desktop-only Vapor payload contract."""
    actual_file_set = frozenset(actual_files)
    if actual_file_set != EXPECTED_PAYLOAD_FILES:
        missing = sorted(EXPECTED_PAYLOAD_FILES - actual_file_set)
        unexpected = sorted(actual_file_set - EXPECTED_PAYLOAD_FILES)
        raise ValueError(
            "payload violates the exact desktop-only scope; "
            f"missing={missing}, unexpected={unexpected}"
        )
    _validate_global_theme(payload, version)
    _validate_companions(payload, version)
