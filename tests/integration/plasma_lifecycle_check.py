#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from tests.integration.process_control import (
    FORCE_KILL_SIGNAL,
    run_bounded,
    signal_process_group,
)

ACCENT_RENDER_MARKER = "VAPOR_ACCENT_RENDER="


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--existing-script", type=Path, required=True)
    parser.add_argument("--verify-script", type=Path, required=True)
    parser.add_argument("--theme-id", required=True)
    parser.add_argument("--uninstall-script", type=Path, required=True)
    parser.add_argument("--lockscreen-wallpaper-uri", required=True)
    return parser


def _evaluate(
    script: str,
    *,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    return run_bounded(
        [
            "qdbus-qt6",
            "org.kde.plasmashell",
            "/PlasmaShell",
            "org.kde.PlasmaShell.evaluateScript",
            script,
        ],
        timeout=timeout,
    )


def _wait_for_service(shell: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if shell.poll() is not None:
            raise RuntimeError("plasmashell exited before registering its D-Bus API")
        remaining = deadline - time.monotonic()
        try:
            result = _evaluate(
                'print("VAPOR_DBUS_READY");',
                timeout=max(0.1, min(2, remaining)),
            )
        except subprocess.TimeoutExpired:
            continue
        if result.returncode == 0:
            return
        time.sleep(0.25)
    raise RuntimeError("plasmashell did not register its D-Bus API")


def _require_evaluation_marker(
    result: subprocess.CompletedProcess[str],
    marker: str,
    context: str,
) -> None:
    detail = (result.stderr or result.stdout).strip()
    if result.returncode != 0:
        raise RuntimeError(f"{context} failed: {detail}")
    if marker not in result.stdout:
        raise RuntimeError(f"{context} did not report {marker}: {detail}")


def _require_config_value(
    filename: str,
    group: str | tuple[str, ...],
    key: str,
    expected: str,
) -> None:
    groups = (group,) if isinstance(group, str) else group

    def read(config_file: str) -> str:
        command = ["kreadconfig6", "--file", config_file]
        for group_name in groups:
            command.extend(("--group", group_name))
        command.extend(("--key", key))
        result = run_bounded(
            command,
            timeout=10,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            group_path = "/".join(groups)
            raise RuntimeError(
                f"could not read {config_file} {group_path}/{key}: {detail}"
            )
        return result.stdout.strip()

    explicit = read(filename)
    if explicit == expected:
        return
    managed_default = read(f"kdedefaults/{filename}") if not explicit else ""
    if managed_default != expected:
        group_path = "/".join(groups)
        raise RuntimeError(
            f"{filename} {group_path}/{key} has override {explicit!r} and "
            f"managed default {managed_default!r}, expected {expected!r}"
        )


def _require_theme_absent(theme_id: str) -> None:
    result = run_bounded(
        [
            "kpackagetool6",
            "--type",
            "Plasma/LookAndFeel",
            "--list",
        ],
        timeout=10,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"could not list Global Themes: {detail}")
    if theme_id in result.stdout:
        raise RuntimeError(f"uninstall left {theme_id} discoverable")


def _require_selected_global_theme(config_home: Path, expected: str) -> None:
    package_marker = config_home / "kdedefaults" / "package"
    try:
        selected = package_marker.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise RuntimeError(
            f"could not read KDE Global Theme marker {package_marker}: {error}"
        ) from error
    if selected != expected:
        raise RuntimeError(
            f"KDE Global Theme marker selects {selected!r}, expected {expected!r}"
        )


def _xdg_config_home() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME")
    return Path(configured) if configured else Path.home() / ".config"


def _xdg_data_home() -> Path:
    configured = os.environ.get("XDG_DATA_HOME")
    return Path(configured) if configured else Path.home() / ".local" / "share"


def _checked_process(
    command: list[str],
    *,
    context: str,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    try:
        result = run_bounded(command, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"{context} timed out") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"{context} failed: {detail}")
    return result


def _render_accent(accent: str, output: Path) -> dict[str, str]:
    _checked_process(
        ["plasma-apply-colorscheme", "--accent-color", accent],
        context=f"apply test accent {accent}",
        timeout=30,
    )
    rendered = _checked_process(
        [
            sys.executable,
            "-m",
            "tests.integration.plasma_accent_render_check",
            "--output",
            str(output),
        ],
        context=f"render Plasma highlight for accent {accent}",
        timeout=30,
    )
    markers = [
        line.removeprefix(ACCENT_RENDER_MARKER)
        for line in rendered.stdout.splitlines()
        if line.startswith(ACCENT_RENDER_MARKER)
    ]
    if len(markers) != 1:
        raise RuntimeError(
            f"accent render did not report exactly one result: {rendered.stdout!r}"
        )
    try:
        parsed = json.loads(markers[0])
    except json.JSONDecodeError as error:
        raise RuntimeError("accent render reported invalid JSON") from error
    if not isinstance(parsed, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in parsed.items()
    ):
        raise RuntimeError("accent render result is not a string mapping")
    return parsed


def _rgb(value: str) -> tuple[int, int, int]:
    if len(value) != 7 or not value.startswith("#"):
        raise RuntimeError(f"accent render reported an invalid color: {value!r}")
    try:
        return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))
    except ValueError as error:
        raise RuntimeError(
            f"accent render reported an invalid color: {value!r}"
        ) from error


def _require_dominant_channel(value: str, channel: int, *, label: str) -> None:
    channels = _rgb(value)
    others = channels[:channel] + channels[channel + 1 :]
    if channels[channel] <= max(others):
        raise RuntimeError(f"{label} did not render the requested hue: {value}")


def _exercise_visual_accent_updates(temporary: Path) -> None:
    renders = temporary / "accent-renders"
    red = _render_accent("#ff1744", renders / "red.png")
    green = _render_accent("#00c853", renders / "green.png")

    for accent, result in (("red", red), ("green", green)):
        if result.get("theme") != "Vapor":
            raise RuntimeError(
                f"{accent} accent render used theme {result.get('theme')!r}, "
                "expected 'Vapor'"
            )
        if result.get("swatch_pixel") != result.get("resolved_highlight"):
            raise RuntimeError(
                f"{accent} Plasma highlight swatch did not render its resolved "
                f"color: {result!r}"
            )

    _require_dominant_channel(
        red["swatch_pixel"],
        0,
        label="red Plasma highlight swatch",
    )
    _require_dominant_channel(
        green["swatch_pixel"],
        1,
        label="green Plasma highlight swatch",
    )
    _require_dominant_channel(
        red["component_pixel"],
        0,
        label="red Plasma list highlight",
    )
    _require_dominant_channel(
        green["component_pixel"],
        1,
        label="green Plasma list highlight",
    )
    if red["component_pixel"] == green["component_pixel"]:
        raise RuntimeError("Plasma list highlight ignored the changed accent color")


def run_check(
    existing_script: Path,
    verify_script: Path,
    theme_id: str,
    uninstall_script: Path,
    lockscreen_wallpaper_uri: str,
) -> None:
    with tempfile.TemporaryDirectory(prefix="vapor-shell-log-") as temporary:
        log_path = Path(temporary) / "plasmashell.log"
        wallpaper = (
            _xdg_data_home()
            / "wallpapers"
            / "Vapor"
            / "contents"
            / "images"
            / "3940x2160.jxl"
        )
        launcher_icon = (
            _xdg_data_home()
            / "icons"
            / "hicolor"
            / "scalable"
            / "places"
            / "vapor-bazzite.svg"
        )
        with log_path.open("wb") as shell_log:
            shell = subprocess.Popen(
                ["plasmashell"],
                stdout=shell_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                print("Waiting for plasmashell D-Bus API", flush=True)
                _wait_for_service(shell)
                print("Creating existing Plasma objects", flush=True)
                try:
                    created = _evaluate(
                        existing_script.read_text(encoding="utf-8"),
                        timeout=30,
                    )
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError(
                        "Plasma evaluateScript timed out while creating "
                        "existing objects"
                    ) from error
                _require_evaluation_marker(
                    created,
                    "VAPOR_EXISTING_READY",
                    "create existing objects",
                )
                print("Applying Vapor in the running Plasma session", flush=True)
                try:
                    applied = run_bounded(
                        [
                            "plasma-apply-lookandfeel",
                            "--apply",
                            theme_id,
                        ],
                        timeout=30,
                    )
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError(
                        "plasma-apply-lookandfeel timed out in the running session"
                    ) from error
                if applied.returncode != 0:
                    raise RuntimeError(
                        "plasma-apply-lookandfeel failed in the running session: "
                        f"{applied.stderr or applied.stdout}"
                    )
                _require_selected_global_theme(_xdg_config_home(), theme_id)
                _require_config_value(
                    "kdeglobals",
                    "KDE",
                    "LookAndFeelPackage",
                    theme_id,
                )
                _require_config_value(
                    "kdeglobals",
                    "General",
                    "ColorScheme",
                    "Vapor",
                )
                _require_config_value(
                    "plasmarc",
                    "Theme",
                    "name",
                    "Vapor",
                )
                _require_config_value(
                    "ksplashrc",
                    "KSplash",
                    "Theme",
                    theme_id,
                )
                _require_config_value(
                    "kscreenlockerrc",
                    "Greeter",
                    "VaporTestSentinel",
                    "keep-lockscreen",
                )
                _require_config_value(
                    "kscreenlockerrc",
                    ("Greeter", "Wallpaper", "org.kde.image", "General"),
                    "Image",
                    lockscreen_wallpaper_uri,
                )
                print("Rendering Plasma highlights under two accents", flush=True)
                _exercise_visual_accent_updates(Path(temporary))
                print("Evaluating Vapor lifecycle assertions", flush=True)
                try:
                    verified = _evaluate(
                        verify_script.read_text(encoding="utf-8"),
                        timeout=30,
                    )
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError(
                        "Plasma evaluateScript timed out during lifecycle assertions"
                    ) from error
                _require_evaluation_marker(
                    verified,
                    "VAPOR_LIFECYCLE_OK",
                    "verify Vapor lifecycle",
                )
                print("Uninstalling active Vapor in the running session", flush=True)
                try:
                    uninstalled = run_bounded(
                        ["sh", str(uninstall_script)],
                        timeout=60,
                    )
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError(
                        "active Vapor uninstall timed out in the running session"
                    ) from error
                if uninstalled.returncode != 0:
                    raise RuntimeError(
                        "active Vapor uninstall failed in the running session: "
                        f"{uninstalled.stderr or uninstalled.stdout}"
                    )
                _require_selected_global_theme(
                    _xdg_config_home(),
                    "org.kde.breeze.desktop",
                )
                _require_config_value(
                    "kscreenlockerrc",
                    "Greeter",
                    "VaporTestSentinel",
                    "keep-lockscreen",
                )
                _require_config_value(
                    "kscreenlockerrc",
                    ("Greeter", "Wallpaper", "org.kde.image", "General"),
                    "Image",
                    lockscreen_wallpaper_uri,
                )
                if not wallpaper.is_file():
                    raise RuntimeError(
                        "uninstall removed the selected lock-screen wallpaper"
                    )
                if not launcher_icon.is_file():
                    raise RuntimeError(
                        "uninstall removed the launcher icon used by a live applet"
                    )
                _require_theme_absent(theme_id)
            finally:
                signal_process_group(shell.pid, signal.SIGTERM)
                if shell.poll() is None:
                    try:
                        shell.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        signal_process_group(
                            shell.pid,
                            FORCE_KILL_SIGNAL,
                        )
                        shell.wait(timeout=10)
                shell_log.flush()
                print(
                    log_path.read_text(encoding="utf-8", errors="replace"),
                    flush=True,
                )


def main() -> int:
    arguments = _parser().parse_args()
    try:
        run_check(
            arguments.existing_script,
            arguments.verify_script,
            arguments.theme_id,
            arguments.uninstall_script,
            arguments.lockscreen_wallpaper_uri,
        )
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
