from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from subprocess import CompletedProcess

from vapor_theme.bundle_contract import (
    GLOBAL_THEME_ID,
    safe_relative_path,
    validate_manifested_payload,
)
from vapor_theme.io_utils import (
    command_from_environment,
    read_json_object,
    run_checked,
    sha256_file,
    write_json_atomic,
)
from vapor_theme.records import (
    BundleManifest,
    DottedVersion,
    InstalledState,
    parse_bundle_manifest,
    parse_installed_state,
)

KPACKAGE_TYPE = "Plasma/LookAndFeel"
BREEZE_GLOBAL_THEME_ID = "org.kde.breeze.desktop"
BREEZE_DARK_GLOBAL_THEME_ID = "org.kde.breezedark.desktop"
KCONFIG_MISSING_VALUE = "__VAPOR_THEME_KCONFIG_VALUE_IS_MISSING__"
STATE_RELATIVE_PATH = PurePosixPath("vapor-theme/install-state.json")
GLOBAL_THEME_ROOT = PurePosixPath(f"plasma/look-and-feel/{GLOBAL_THEME_ID}")
COMPONENT_ROOTS_BY_NAME = {
    "color_scheme": PurePosixPath("color-schemes/Vapor.colors"),
    "launcher_icon": PurePosixPath("icons/hicolor/scalable/places/vapor-bazzite.svg"),
    "plasma_style": PurePosixPath("plasma/desktoptheme/Vapor"),
    "global_theme": GLOBAL_THEME_ROOT,
    "wallpaper": PurePosixPath("wallpapers/Vapor"),
}
COMPONENT_ROOTS = tuple(COMPONENT_ROOTS_BY_NAME.values())


@dataclass(frozen=True)
class ListedComponentProbe:
    command_variable: str
    default_command: tuple[str, ...]
    arguments: tuple[str, ...]
    expected: str
    component: str
    failure: str


LISTED_COMPONENT_PROBES = (
    ListedComponentProbe(
        command_variable="VAPOR_DESKTOP_THEME_COMMAND",
        default_command=("plasma-apply-desktoptheme",),
        arguments=("--list-themes",),
        expected="Vapor",
        component="Plasma Style",
        failure="Plasma Style discovery failed",
    ),
    ListedComponentProbe(
        command_variable="VAPOR_COLOR_SCHEME_COMMAND",
        default_command=("plasma-apply-colorscheme",),
        arguments=("--list-schemes",),
        expected="Vapor",
        component="color scheme",
        failure="color scheme discovery failed",
    ),
    ListedComponentProbe(
        command_variable="VAPOR_WALLPAPER_COMMAND",
        default_command=("kpackagetool6",),
        arguments=("--type", "Wallpaper/Images", "--list"),
        expected="Vapor",
        component="wallpaper package",
        failure="wallpaper package discovery failed",
    ),
)


def _validate_bundle(bundle_root: Path) -> BundleManifest:
    manifest = parse_bundle_manifest(
        read_json_object(bundle_root / "manifest.json"),
        expected_global_theme_id=GLOBAL_THEME_ID,
    )
    payload = bundle_root / "payload"
    validate_manifested_payload(payload, manifest)
    return manifest


def _xdg_home(variable: str, fallback: PurePosixPath) -> Path:
    configured = os.environ.get(variable)
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            raise ValueError(f"{variable} must be an absolute path")
        return path
    home = os.environ.get("HOME")
    if not home:
        raise ValueError(f"HOME is required when {variable} is unset")
    return Path(home).joinpath(*fallback.parts)


def _xdg_data_home() -> Path:
    return _xdg_home("XDG_DATA_HOME", PurePosixPath(".local/share"))


def _run_kpackage(
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> CompletedProcess[str]:
    return run_checked(
        command_from_environment("VAPOR_KPACKAGE_COMMAND", ["kpackagetool6"]),
        *arguments,
        failure="KPackage operation failed",
        environment=environment,
    )


def _require_listed(
    result: CompletedProcess[str],
    expected: str,
    *,
    component: str,
) -> None:
    if expected not in result.stdout.split():
        raise RuntimeError(f"KDE did not discover the Vapor {component}")


def _run_listed_component(
    probe: ListedComponentProbe,
    *,
    environment: dict[str, str] | None,
) -> None:
    result = run_checked(
        command_from_environment(
            probe.command_variable,
            list(probe.default_command),
        ),
        *probe.arguments,
        failure=probe.failure,
        environment=environment,
    )
    _require_listed(result, probe.expected, component=probe.component)


def _validate_component_discovery(
    data_home: Path,
    *,
    environment: dict[str, str] | None = None,
) -> None:
    discovery_environment = (
        os.environ.copy() if environment is None else environment.copy()
    )
    discovery_environment["QT_QPA_PLATFORM"] = "offscreen"
    for probe in LISTED_COMPONENT_PROBES:
        _run_listed_component(
            probe,
            environment=discovery_environment,
        )

    icon = run_checked(
        command_from_environment(
            "VAPOR_ICON_FINDER_COMMAND",
            ["kiconfinder6"],
        ),
        "vapor-bazzite",
        failure="launcher icon discovery failed",
        environment=discovery_environment,
    )
    icon_path = Path(icon.stdout.strip())
    expected_icon = (
        data_home / "icons" / "hicolor" / "scalable" / "places" / "vapor-bazzite.svg"
    )
    if not icon_path.is_file() or icon_path.resolve() != expected_icon.resolve():
        raise RuntimeError("KDE did not discover the installed Vapor launcher icon")

    qt_plugins = run_checked(
        command_from_environment(
            "VAPOR_QT_PATHS_COMMAND",
            ["qtpaths6"],
        ),
        "--plugin-dir",
        failure="Qt image plugin discovery failed",
        environment=discovery_environment,
    )
    imageformats = Path(qt_plugins.stdout.strip()) / "imageformats"
    if not any(imageformats.glob("kimg_jxl.*")):
        raise RuntimeError("Qt does not have a discoverable JPEG XL image plugin")


def _destination(data_home: Path, relative: PurePosixPath) -> Path:
    destination = data_home.joinpath(*relative.parts)
    try:
        destination.resolve().relative_to(data_home.resolve())
    except ValueError as error:
        raise RuntimeError(
            f"Vapor path escapes the XDG data root: {relative.as_posix()}"
        ) from error
    return destination


def _validate_installed_state(
    data_home: Path,
    state: InstalledState,
    *,
    allow_missing: bool = False,
) -> None:
    if (
        state.get("schema_version") != 1
        or state.get("global_theme_id") != GLOBAL_THEME_ID
    ):
        raise RuntimeError("installed Vapor ownership state is invalid")
    files = state.get("files")
    if not isinstance(files, dict) or not files:
        raise RuntimeError("installed Vapor ownership state has no files")
    actual_paths: set[str] = set()
    for root in COMPONENT_ROOTS:
        destination = _destination(data_home, root)
        if destination.is_symlink():
            raise RuntimeError(
                f"installed Vapor path became a symlink: {root.as_posix()}"
            )
        if destination.is_file():
            actual_paths.add(root.as_posix())
        elif destination.is_dir():
            for path in destination.rglob("*"):
                if path.is_symlink():
                    raise RuntimeError(
                        "installed Vapor path became a symlink: "
                        f"{path.relative_to(data_home).as_posix()}"
                    )
                if path.is_file():
                    actual_paths.add(path.relative_to(data_home).as_posix())
    tracked_paths = set(files)
    if actual_paths - tracked_paths or (
        not allow_missing and actual_paths != tracked_paths
    ):
        raise RuntimeError(
            "installed Vapor files were added, removed, or moved; "
            "refusing to overwrite them"
        )
    for relative_path, expected_hash in sorted(files.items()):
        path = _destination(
            data_home,
            safe_relative_path(relative_path, label="manifest path"),
        )
        if allow_missing and not path.is_file():
            continue
        if sha256_file(path) != expected_hash:
            raise RuntimeError(
                "installed Vapor file was modified; refusing to overwrite it: "
                + relative_path
            )


def _write_state(
    data_home: Path,
    manifest: BundleManifest,
    *,
    retained: dict[str, str] | None = None,
) -> None:
    state_path = _destination(data_home, STATE_RELATIVE_PATH)
    state: InstalledState = {
        "files": manifest["files"],
        "global_theme_id": GLOBAL_THEME_ID,
        "schema_version": 1,
        "version": manifest["version"],
    }
    if retained:
        state["retained"] = retained
    write_json_atomic(state_path, state)


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"could not remove owned Vapor path: {path}")


def _xdg_config_home() -> Path:
    return _xdg_home("XDG_CONFIG_HOME", PurePosixPath(".config"))


def _read_kconfig_file(
    file_name: str,
    group: str,
    key: str,
    default: str,
) -> str:
    result = run_checked(
        command_from_environment("VAPOR_KREADCONFIG_COMMAND", ["kreadconfig6"]),
        "--file",
        file_name,
        "--group",
        group,
        "--key",
        key,
        "--default",
        default,
        failure=f"reading {file_name} [{group}] {key} failed",
    )
    return result.stdout.strip()


def _read_kconfig(
    file_name: str,
    group: str,
    key: str,
    default: str,
) -> str:
    explicit_value = _read_kconfig_file(
        file_name,
        group,
        key,
        KCONFIG_MISSING_VALUE,
    )
    if explicit_value != KCONFIG_MISSING_VALUE:
        return explicit_value

    managed_default = _read_kconfig_file(
        f"kdedefaults/{file_name}",
        group,
        key,
        KCONFIG_MISSING_VALUE,
    )
    if managed_default != KCONFIG_MISSING_VALUE:
        return managed_default
    return default


def _write_kconfig(
    file_name: str,
    group: str,
    key: str,
    value: str,
) -> None:
    run_checked(
        command_from_environment("VAPOR_KWRITECONFIG_COMMAND", ["kwriteconfig6"]),
        "--file",
        file_name,
        "--group",
        group,
        "--key",
        key,
        value,
        failure=f"writing {file_name} [{group}] {key} failed",
    )


def _apply_breeze() -> None:
    run_checked(
        command_from_environment(
            "VAPOR_APPLY_LOOKANDFEEL_COMMAND",
            ["plasma-apply-lookandfeel"],
        ),
        "--apply",
        BREEZE_GLOBAL_THEME_ID,
        "--keep-auto",
        failure="applying Breeze before Vapor removal failed",
    )


def _file_contains(config_home: Path, file_name: str, token: str) -> bool:
    path = config_home / file_name
    if not path.is_file():
        return False
    content = path.read_text(encoding="utf-8", errors="replace").replace("\\", "/")
    return token in content


def _is_within_component(
    relative_path: str,
    component_root: PurePosixPath,
) -> bool:
    path = PurePosixPath(relative_path)
    return path == component_root or path.is_relative_to(component_root)


def uninstall() -> tuple[str, dict[str, str]]:
    data_home = _xdg_data_home()
    state_path = _destination(data_home, STATE_RELATIVE_PATH)
    if not state_path.is_file():
        return "Vapor is not installed", {}
    state = parse_installed_state(
        read_json_object(state_path),
        expected_global_theme_id=GLOBAL_THEME_ID,
    )
    _validate_installed_state(data_home, state, allow_missing=True)

    active_theme = _read_kconfig(
        "kdeglobals",
        "KDE",
        "LookAndFeelPackage",
        BREEZE_GLOBAL_THEME_ID,
    )
    if active_theme == GLOBAL_THEME_ID:
        _apply_breeze()
        active_theme = _read_kconfig(
            "kdeglobals",
            "KDE",
            "LookAndFeelPackage",
            BREEZE_GLOBAL_THEME_ID,
        )
        if active_theme == GLOBAL_THEME_ID:
            raise RuntimeError("Breeze application left Vapor active")

    automatic_replacements = {
        "DefaultLightLookAndFeel": BREEZE_GLOBAL_THEME_ID,
        "DefaultDarkLookAndFeel": BREEZE_DARK_GLOBAL_THEME_ID,
    }
    for key, replacement in automatic_replacements.items():
        current = _read_kconfig(
            "kdeglobals",
            "KDE",
            key,
            replacement,
        )
        if current == GLOBAL_THEME_ID:
            _write_kconfig("kdeglobals", "KDE", key, replacement)
            verified = _read_kconfig(
                "kdeglobals",
                "KDE",
                key,
                replacement,
            )
            if verified == GLOBAL_THEME_ID:
                raise RuntimeError(f"automatic theme reference remains in {key}")

    config_home = _xdg_config_home()
    retained: dict[str, str] = {}
    if _read_kconfig("plasmarc", "Theme", "name", "default") == "Vapor":
        retained["plasma_style"] = "Vapor Plasma Style is still selected"
    if _read_kconfig("kdeglobals", "General", "ColorScheme", "BreezeLight") == "Vapor":
        retained["color_scheme"] = "Vapor color scheme is still selected"
    if (
        _read_kconfig(
            "ksplashrc",
            "KSplash",
            "Theme",
            BREEZE_GLOBAL_THEME_ID,
        )
        == GLOBAL_THEME_ID
    ):
        retained["global_theme"] = "Vapor splash is still selected"

    reference_files = (
        "plasma-org.kde.plasma.desktop-appletsrc",
        "kscreenlockerrc",
    )
    if any(
        _file_contains(config_home, file_name, "wallpapers/Vapor/")
        for file_name in reference_files
    ):
        retained["wallpaper"] = (
            "Convergence is still selected by a desktop or lock screen"
        )
    if _file_contains(
        config_home,
        "plasma-org.kde.plasma.desktop-appletsrc",
        "vapor-bazzite",
    ):
        retained["launcher_icon"] = (
            "the Bazzite launcher icon is still selected by an applet"
        )

    for component_name, component_root in COMPONENT_ROOTS_BY_NAME.items():
        if component_name in retained:
            continue
        destination = _destination(data_home, component_root)
        if not destination.exists():
            continue
        if component_name == "global_theme":
            _run_kpackage(
                "--type",
                KPACKAGE_TYPE,
                "--remove",
                GLOBAL_THEME_ID,
            )
            if destination.exists():
                raise RuntimeError("KPackage did not remove the Vapor Global Theme")
        else:
            _remove_path(destination)

    if retained:
        retained_roots = {
            COMPONENT_ROOTS_BY_NAME[component_name] for component_name in retained
        }
        retained_files = {
            relative_path: expected_hash
            for relative_path, expected_hash in state["files"].items()
            if any(_is_within_component(relative_path, root) for root in retained_roots)
        }
        retained_state: BundleManifest = {
            "files": retained_files,
            "global_theme_id": GLOBAL_THEME_ID,
            "schema_version": 1,
            "version": state["version"],
        }
        _write_state(data_home, retained_state, retained=retained)
    else:
        state_path.unlink(missing_ok=True)
        with suppress(OSError):
            state_path.parent.rmdir()
    return f"Uninstalled Vapor {state['version']}", retained


def install(bundle_root: Path) -> str:
    bundle_root = bundle_root.resolve()
    manifest = _validate_bundle(bundle_root)
    data_home = _xdg_data_home()
    data_home.mkdir(parents=True, exist_ok=True)
    state_path = _destination(data_home, STATE_RELATIVE_PATH)
    upgrading = False
    if state_path.exists():
        state = parse_installed_state(
            read_json_object(state_path),
            expected_global_theme_id=GLOBAL_THEME_ID,
        )
        _validate_installed_state(data_home, state)
        if state.get("version") == manifest.get("version") and state.get(
            "files"
        ) == manifest.get("files"):
            return f"{manifest['version']} (already installed)"
        current_version = DottedVersion.parse(
            state["version"],
            "installed Vapor version",
        )
        incoming_version = DottedVersion.parse(
            manifest["version"],
            "incoming Vapor version",
        )
        if incoming_version < current_version:
            raise RuntimeError(
                f"refusing to downgrade Vapor from {state.get('version')} "
                f"to {manifest.get('version')}"
            )
        if incoming_version == current_version:
            raise RuntimeError("the same Vapor version has different payload bytes")
        upgrading = True
    else:
        conflicts = [
            relative.as_posix()
            for relative in COMPONENT_ROOTS
            if _destination(data_home, relative).exists()
            or _destination(data_home, relative).is_symlink()
        ]
        if conflicts:
            raise RuntimeError(
                "refusing to overwrite a pre-existing path: " + conflicts[0]
            )

    stage = Path(tempfile.mkdtemp(prefix=".vapor-stage-", dir=data_home))
    backup = (
        Path(tempfile.mkdtemp(prefix=".vapor-backup-", dir=data_home))
        if upgrading
        else None
    )
    backed_up: list[tuple[Path, Path]] = []
    installed_companions: list[Path] = []
    global_theme_install_attempted = False
    global_theme_installed = False
    discard_backup = False
    try:
        staged_payload = stage / "payload"
        shutil.copytree(bundle_root / "payload", staged_payload)
        for relative_path, expected_hash in manifest["files"].items():
            path = staged_payload.joinpath(
                *safe_relative_path(relative_path, label="manifest path").parts
            )
            if sha256_file(path) != expected_hash:
                raise RuntimeError(f"staged payload hash mismatch: {relative_path}")

        staged_global_theme = _destination(staged_payload, GLOBAL_THEME_ROOT)
        preflight_root = stage / ".vapor-kpackage-preflight-root"
        preflight_environment = os.environ.copy()
        preflight_environment.update(
            {
                "HOME": str(preflight_root / "home"),
                "XDG_CACHE_HOME": str(preflight_root / "cache"),
                "XDG_CONFIG_HOME": str(preflight_root / "config"),
                "XDG_DATA_HOME": str(preflight_root / "data"),
                "XDG_STATE_HOME": str(preflight_root / "state"),
            }
        )
        preflight_data_home = Path(preflight_environment["XDG_DATA_HOME"])
        for relative in COMPONENT_ROOTS:
            if relative == GLOBAL_THEME_ROOT:
                continue
            source = _destination(staged_payload, relative)
            destination = _destination(preflight_data_home, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)
        _run_kpackage(
            "--type",
            KPACKAGE_TYPE,
            "--install",
            str(staged_global_theme),
            environment=preflight_environment,
        )
        preflight_listed = _run_kpackage(
            "--type",
            KPACKAGE_TYPE,
            "--list",
            environment=preflight_environment,
        )
        _require_listed(
            preflight_listed,
            GLOBAL_THEME_ID,
            component="Global Theme",
        )
        _validate_component_discovery(
            preflight_data_home,
            environment=preflight_environment,
        )
        _run_kpackage(
            "--type",
            KPACKAGE_TYPE,
            "--remove",
            GLOBAL_THEME_ID,
            environment=preflight_environment,
        )

        if backup is not None:
            for relative in COMPONENT_ROOTS:
                destination = _destination(data_home, relative)
                if not destination.exists():
                    continue
                backup_destination = _destination(backup, relative)
                backup_destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(destination, backup_destination)
                backed_up.append((backup_destination, destination))

        for relative in COMPONENT_ROOTS:
            if relative == GLOBAL_THEME_ROOT:
                continue
            source = _destination(staged_payload, relative)
            destination = _destination(data_home, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
            installed_companions.append(destination)

        global_theme_install_attempted = True
        _run_kpackage(
            "--type",
            KPACKAGE_TYPE,
            "--install",
            str(staged_global_theme),
        )
        global_theme_installed = True
        listed = _run_kpackage("--type", KPACKAGE_TYPE, "--list")
        _require_listed(listed, GLOBAL_THEME_ID, component="Global Theme")
        _validate_component_discovery(data_home)
        _write_state(data_home, manifest)
        discard_backup = True
    except BaseException as install_error:
        try:
            installed_global_theme = _destination(data_home, GLOBAL_THEME_ROOT)
            if global_theme_install_attempted:
                if global_theme_installed:
                    try:
                        _run_kpackage(
                            "--type",
                            KPACKAGE_TYPE,
                            "--remove",
                            GLOBAL_THEME_ID,
                        )
                    except RuntimeError:
                        _remove_path(installed_global_theme)
                else:
                    _remove_path(installed_global_theme)
            for destination in reversed(installed_companions):
                _remove_path(destination)
            for backup_source, destination in reversed(backed_up):
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup_source, destination)
            if not upgrading:
                state_path.unlink(missing_ok=True)
        except BaseException as rollback_error:
            recovery = (
                f"; recovery backup retained at {backup}" if backup is not None else ""
            )
            raise RuntimeError(
                f"Vapor installation failed and rollback also failed{recovery}: "
                f"{rollback_error}"
            ) from install_error
        discard_backup = True
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        if backup is not None and discard_backup:
            shutil.rmtree(backup, ignore_errors=True)

    return str(manifest["version"])


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vapor-installer")
    commands = parser.add_subparsers(dest="command", required=True)
    install_parser = commands.add_parser("install")
    install_parser.add_argument("--bundle-root", type=Path, required=True)
    commands.add_parser("uninstall")
    return parser


def main() -> int:
    arguments = create_parser().parse_args()
    try:
        if arguments.command == "install":
            version = install(arguments.bundle_root)
            print(f"Installed Vapor {version}")
            return 0
        if arguments.command == "uninstall":
            message, retained = uninstall()
            print(message)
            for component, reason in sorted(retained.items()):
                print(f"Retained {component}: {reason}")
            return 0
        raise AssertionError(f"unhandled command: {arguments.command}")
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
