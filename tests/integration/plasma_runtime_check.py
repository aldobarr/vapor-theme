#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from tests.integration.process_control import run_bounded, signal_process_group

GLOBAL_THEME_ID = "com.valve.vapor.desktop"
DIAGNOSTICS = Path(__file__).resolve().parent / "diagnostics"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Install a Vapor release into isolated XDG roots and exercise "
            "Fedora's real Plasma 6 and Qt loaders."
        )
    )
    parser.add_argument("bundle", type=Path)
    return parser


def _record(
    label: str, command: list[str], result: subprocess.CompletedProcess[str]
) -> None:
    DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
    with (DIAGNOSTICS / "runtime.log").open(
        "a",
        encoding="utf-8",
        newline="\n",
    ) as output:
        output.write(f"## {label}\n")
        output.write(f"$ {' '.join(command)}\n")
        output.write(f"exit={result.returncode}\n")
        output.write(result.stdout)
        output.write(result.stderr)
        output.write("\n")


def _run(
    label: str,
    command: list[str],
    environment: dict[str, str],
    *,
    timeout: int = 60,
) -> str:
    try:
        result = run_bounded(
            command,
            timeout=timeout,
            environment=environment,
        )
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout if isinstance(error.stdout, str) else ""
        stderr = error.stderr if isinstance(error.stderr, str) else ""
        result = subprocess.CompletedProcess(command, 124, stdout, stderr)
        _record(label, command, result)
        detail = stderr.strip() or stdout.strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(
            f"{label} timed out after {timeout} seconds{suffix}"
        ) from error
    _record(label, command, result)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{label} failed: {detail}")
    return result.stdout


def _assert_contains(label: str, output: str, expected: str) -> None:
    if expected not in output:
        raise RuntimeError(f"{label} did not discover {expected!r}: {output!r}")


def _extract_validated_bundle(bundle: Path, destination: Path) -> Path:
    from vapor_theme.validator import validate_bundle

    version = validate_bundle(bundle)
    with tarfile.open(bundle, "r:gz") as archive:
        archive.extractall(destination, filter="data")
    root = destination / f"vapor-{version}"
    if not root.is_dir():
        raise RuntimeError("validated bundle did not extract to its expected root")
    return root


def _qt_resource_checks(
    environment: dict[str, str],
    data_home: Path,
) -> None:
    _run(
        "Qt JXL wallpaper and icon discovery",
        [
            sys.executable,
            "-m",
            "tests.integration.qt_resource_check",
            str(data_home),
        ],
        environment,
    )


def _kpackage_discovery_checks(
    environment: dict[str, str],
    wallpaper_package: Path,
    build_root: Path,
) -> None:
    source = Path(__file__).with_name("kpackage_discovery_check.cpp")
    source_root = source.parent
    cmake_build = build_root / "kpackage-build"
    binary = cmake_build / "kpackage-discovery-check"
    _run(
        "configure KPackage discovery probe",
        [
            "cmake",
            "-S",
            str(source_root),
            "-B",
            str(cmake_build),
            "-DCMAKE_BUILD_TYPE=Release",
        ],
        environment,
    )
    _run(
        "compile KPackage discovery probe",
        ["cmake", "--build", str(cmake_build), "--parallel", "2"],
        environment,
    )
    output = _run(
        "System Settings and wallpaper package discovery",
        [str(binary), str(wallpaper_package)],
        environment,
    )
    _assert_contains("System Settings package loader", output, GLOBAL_THEME_ID)
    _assert_contains("KDE image wallpaper loader", output, "3940x2160.jxl")


def _exercise_splash(environment: dict[str, str]) -> None:
    command = [
        "dbus-run-session",
        "--",
        "ksplashqml",
        "--test",
        "--window",
        GLOBAL_THEME_ID,
    ]
    process = subprocess.Popen(
        command,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        timed_out = True
        signal_process_group(process.pid, signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
    result = subprocess.CompletedProcess(
        command,
        0 if timed_out else process.returncode,
        stdout,
        stderr,
    )
    _record("render Vapor splash", command, result)
    if not timed_out and process.returncode != 0:
        detail = stderr.strip() or stdout.strip()
        raise RuntimeError(f"ksplashqml could not render Vapor: {detail}")
    resource_markers = (
        GLOBAL_THEME_ID,
        "Splash.qml",
        "bazzite_logo.svgz",
    )
    loading_failures = (
        "error loading",
        "failed to load",
        "module is not installed",
        "no such file or directory",
        "cannot open",
    )
    relevant_errors = [
        line
        for line in f"{stdout}\n{stderr}".splitlines()
        if any(marker.lower() in line.lower() for marker in resource_markers)
        and any(fragment in line.lower() for fragment in loading_failures)
    ]
    if relevant_errors:
        raise RuntimeError(
            "ksplashqml reported a Vapor loading error: " + "\n".join(relevant_errors)
        )


def _exercise_plasma_lifecycle(
    environment: dict[str, str],
    temporary: Path,
    uninstall_script: Path,
) -> None:
    lifecycle_home = temporary / "lifecycle home"
    lifecycle_config = temporary / "lifecycle config"
    lifecycle_home.mkdir()
    lifecycle_config.mkdir()
    lifecycle_environment = environment.copy()
    lifecycle_environment.update(
        {
            "HOME": str(lifecycle_home),
            "QT_QPA_PLATFORM": "wayland",
            "XDG_CONFIG_HOME": str(lifecycle_config),
        }
    )
    (lifecycle_config / "kdeglobals").write_text(
        "[General]\nColorScheme=BreezeLight\n\n"
        "[KDE]\nLookAndFeelPackage=org.kde.breeze.desktop\n",
        encoding="utf-8",
        newline="\n",
    )
    (lifecycle_config / "plasmarc").write_text(
        "[Theme]\nname=breeze\n",
        encoding="utf-8",
        newline="\n",
    )
    lockscreen_wallpaper = lifecycle_home / "user-selected-lockscreen.png"
    lockscreen_wallpaper.write_bytes(b"user-selected-lockscreen")
    lockscreen_wallpaper_uri = lockscreen_wallpaper.resolve().as_uri()
    (lifecycle_config / "kscreenlockerrc").write_text(
        (
            "[Greeter]\n"
            "VaporTestSentinel=keep-lockscreen\n\n"
            "[Greeter][Wallpaper][org.kde.image][General]\n"
            f"Image={lockscreen_wallpaper_uri}\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    existing_script = temporary / "vapor-existing.js"
    existing_script.write_text(
        (
            "function requireValue(condition, message) {\n"
            "    if (!condition) { throw new Error(message); }\n"
            "}\n"
            "const existingDesktops = desktopsForActivity(currentActivity());\n"
            "requireValue(existingDesktops.length >= 2, "
            '"existing activity lacks two screens");\n'
            "for (let index = 0; index < existingDesktops.length; ++index) {\n"
            "    const desktop = existingDesktops[index];\n"
            '    desktop.wallpaperPlugin = "org.kde.image";\n'
            "    desktop.currentConfigGroup = "
            '["Wallpaper", "org.kde.image", "General"];\n'
            '    desktop.writeConfig("Image", '
            '"file:///existing-folder-sentinel.png");\n'
            "}\n"
            "const existingPanel = new Panel;\n"
            "const existingKickoff = "
            'existingPanel.addWidget("org.kde.plasma.kickoff");\n'
            'existingKickoff.currentConfigGroup = ["General"];\n'
            'existingKickoff.writeConfig("icon", "existing-kickoff-sentinel");\n'
            "const existingTray = "
            'existingPanel.addWidget("org.kde.plasma.systemtray");\n'
            'existingTray.currentConfigGroup = ["General"];\n'
            'existingTray.writeConfig("scaleIconsToFit", false);\n'
            'print("VAPOR_EXISTING_READY");\n'
        ),
        encoding="utf-8",
        newline="\n",
    )

    verify_script = temporary / "vapor-verify.js"
    verify_script.write_text(
        (
            "function requireValue(condition, message) {\n"
            "    if (!condition) { throw new Error(message); }\n"
            "}\n"
            "const existingDesktops = desktopsForActivity(currentActivity());\n"
            "requireValue(existingDesktops.length >= 2, "
            '"existing activity lost a screen");\n'
            "for (let index = 0; index < existingDesktops.length; ++index) {\n"
            "    const desktop = existingDesktops[index];\n"
            "    desktop.currentConfigGroup = "
            '["Wallpaper", "org.kde.image", "General"];\n'
            '    requireValue(desktop.readConfig("Image") === '
            '"file:///existing-folder-sentinel.png", '
            '"existing Folder View changed");\n'
            "}\n"
            "let existingKickoffFound = false;\n"
            "let existingTrayFound = false;\n"
            "const existingPanels = panels();\n"
            "for (let panelIndex = 0; "
            "panelIndex < existingPanels.length; ++panelIndex) {\n"
            "    const kickoffWidgets = "
            'existingPanels[panelIndex].widgets("org.kde.plasma.kickoff");\n'
            "    for (let index = 0; index < kickoffWidgets.length; ++index) {\n"
            '        kickoffWidgets[index].currentConfigGroup = ["General"];\n'
            '        if (kickoffWidgets[index].readConfig("icon") === '
            '"existing-kickoff-sentinel") { existingKickoffFound = true; }\n'
            "    }\n"
            "    const trayWidgets = "
            'existingPanels[panelIndex].widgets("org.kde.plasma.systemtray");\n'
            "    for (let index = 0; index < trayWidgets.length; ++index) {\n"
            '        trayWidgets[index].currentConfigGroup = ["General"];\n'
            '        if (String(trayWidgets[index].readConfig("scaleIconsToFit")) '
            '=== "false") { existingTrayFound = true; }\n'
            "    }\n"
            "}\n"
            "requireValue(existingKickoffFound, "
            '"existing Kickoff changed");\n'
            "requireValue(existingTrayFound, "
            '"existing tray changed");\n'
            'const wallpaper = encodeURI("file://" + userDataPath("data", '
            '"wallpapers/Vapor/contents/images/3940x2160.jxl"));\n'
            'const secondActivity = createActivity("Vapor secondary activity");\n'
            "const newDesktops = desktopsForActivity(secondActivity);\n"
            "requireValue(newDesktops.length >= 2, "
            '"new activity lacks two screens");\n'
            "for (let desktopIndex = 0; "
            "desktopIndex < newDesktops.length; ++desktopIndex) {\n"
            "        const desktop = newDesktops[desktopIndex];\n"
            '        requireValue(desktop.type === "org.kde.plasma.folder", '
            '"new activity desktop is not Folder View");\n'
            '        requireValue(desktop.wallpaperPlugin === "org.kde.image", '
            '"Folder View did not select the image wallpaper plugin");\n'
            "        desktop.currentConfigGroup = "
            '["Wallpaper", "org.kde.image", "General"];\n'
            '        requireValue(desktop.readConfig("Image") === wallpaper, '
            '"Folder View did not receive Convergence");\n'
            "}\n"
            "const newPanel = new Panel;\n"
            'const kickoff = newPanel.addWidget("org.kde.plasma.kickoff");\n'
            'kickoff.currentConfigGroup = ["General"];\n'
            'requireValue(kickoff.readConfig("icon") === "vapor-bazzite", '
            '"Kickoff did not receive the Bazzite launcher icon");\n'
            'const tray = newPanel.addWidget("org.kde.plasma.systemtray");\n'
            'tray.currentConfigGroup = ["General"];\n'
            'requireValue(String(tray.readConfig("scaleIconsToFit")) === "true", '
            '"System Tray did not enable scaleIconsToFit");\n'
            'print("VAPOR_LIFECYCLE_OK activities=2");\n'
        ),
        encoding="utf-8",
        newline="\n",
    )
    lifecycle_runner = temporary / "run-vapor-lifecycle"
    lifecycle_runner.write_text(
        "#!/usr/bin/env sh\n"
        + "exec "
        + " ".join(
            shlex.quote(argument)
            for argument in (
                sys.executable,
                "-m",
                "tests.integration.plasma_lifecycle_check",
                "--existing-script",
                str(existing_script),
                "--verify-script",
                str(verify_script),
                "--theme-id",
                GLOBAL_THEME_ID,
                "--uninstall-script",
                str(uninstall_script),
                "--lockscreen-wallpaper-uri",
                lockscreen_wallpaper_uri,
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    lifecycle_runner.chmod(0o700)
    kwin_binary = _prepare_virtual_kwin(lifecycle_environment)
    _run(
        "exercise existing and new Plasma applet lifecycle",
        [
            "dbus-run-session",
            "--",
            kwin_binary,
            "--virtual",
            "--output-count",
            "2",
            "--width",
            "1280",
            "--height",
            "720",
            "--no-lockscreen",
            "--exit-with-session",
            str(lifecycle_runner),
        ],
        lifecycle_environment,
        timeout=240,
    )


def _prepare_virtual_kwin(environment: dict[str, str]) -> str:
    kwin_binary = shutil.which(
        "kwin_wayland",
        path=environment.get("PATH"),
    )
    if kwin_binary is None:
        raise RuntimeError("kwin_wayland is not installed")
    capabilities = _run(
        "inspect kwin_wayland file capabilities",
        ["getcap", kwin_binary],
        environment,
    )
    if not capabilities.strip():
        return kwin_binary
    if "cap_sys_nice" not in capabilities:
        raise RuntimeError(
            f"kwin_wayland has unexpected file capabilities: {capabilities.strip()}"
        )
    _run(
        "remove unsupported kwin_wayland file capabilities",
        ["setcap", "-r", kwin_binary],
        environment,
    )
    remaining = _run(
        "verify kwin_wayland file capabilities",
        ["getcap", kwin_binary],
        environment,
    )
    if remaining.strip():
        raise RuntimeError(
            f"kwin_wayland still has file capabilities: {remaining.strip()}"
        )
    return kwin_binary


def _wait_for_xvfb(process: subprocess.Popen[str]) -> None:
    for _ in range(30):
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise RuntimeError(f"Xvfb exited early: {stdout}{stderr}")
        if Path("/tmp/.X11-unix/X99").exists():
            return
        time.sleep(0.1)
    raise RuntimeError("Xvfb did not create display :99")


def _snapshot_config(config_home: Path) -> dict[str, bytes]:
    return {
        path.relative_to(config_home).as_posix(): path.read_bytes()
        for path in config_home.rglob("*")
        if path.is_file()
    }


def _headless_environment(
    *,
    home: Path,
    data_home: Path,
    config_home: Path,
    runtime: Path,
) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "DISPLAY": ":99",
            "HOME": str(home),
            "KDE_FULL_SESSION": "true",
            "KDE_SESSION_VERSION": "6",
            "QT_QPA_PLATFORM": "offscreen",
            "XDG_CONFIG_HOME": str(config_home),
            "XDG_CURRENT_DESKTOP": "KDE",
            "XDG_DATA_HOME": str(data_home),
            "XDG_RUNTIME_DIR": str(runtime),
        }
    )
    return environment


def _xcb_environment(headless_environment: dict[str, str]) -> dict[str, str]:
    environment = headless_environment.copy()
    environment["QT_QPA_PLATFORM"] = "xcb"
    return environment


def run_probe(bundle: Path) -> None:
    bundle = bundle.resolve()
    if not bundle.is_file():
        raise FileNotFoundError(f"bundle does not exist: {bundle}")
    DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
    (DIAGNOSTICS / "runtime.log").write_text(
        "",
        encoding="utf-8",
        newline="\n",
    )
    with tempfile.TemporaryDirectory(prefix="vapor-plasma-") as temporary_name:
        temporary = Path(temporary_name)
        home = temporary / "home"
        data_home = temporary / "xdg data"
        config_home = temporary / "xdg config"
        runtime = temporary / "runtime"
        extracted = temporary / "bundle"
        for directory in (home, data_home, config_home, runtime, extracted):
            directory.mkdir()
        runtime.chmod(0o700)
        environment = _headless_environment(
            home=home,
            data_home=data_home,
            config_home=config_home,
            runtime=runtime,
        )
        bundle_root = _extract_validated_bundle(bundle, extracted)

        custom_layout = (
            b"[Containments][42]\n"
            b"plugin=org.kde.plasma.folder\n"
            b"wallpaperplugin=org.kde.image\n\n"
            b"[Containments][42][Wallpaper][org.kde.image][General]\n"
            b"Image=file:///keep/custom-wallpaper.png\n"
        )
        lockscreen = (
            b"[Greeter][Wallpaper][org.kde.image][General]\n"
            b"Image=file:///keep/custom-lockscreen.png\n"
        )
        (config_home / "plasma-org.kde.plasma.desktop-appletsrc").write_bytes(
            custom_layout
        )
        (config_home / "kscreenlockerrc").write_bytes(lockscreen)
        (config_home / "kdeglobals").write_text(
            "[General]\nColorScheme=BreezeLight\n\n"
            "[KDE]\nLookAndFeelPackage=org.kde.breeze.desktop\n",
            encoding="utf-8",
            newline="\n",
        )
        (config_home / "plasmarc").write_text(
            "[Theme]\nname=breeze\n",
            encoding="utf-8",
            newline="\n",
        )
        (config_home / "ksplashrc").write_text(
            "[KSplash]\nEngine=KSplashQML\nTheme=org.kde.breeze.desktop\n",
            encoding="utf-8",
            newline="\n",
        )
        before_install = _snapshot_config(config_home)

        _run(
            "install release bundle",
            ["sh", str(bundle_root / "install.sh")],
            environment,
        )
        if _snapshot_config(config_home) != before_install:
            raise RuntimeError(
                "install.sh changed KDE configuration or activated Vapor"
            )

        look_and_feel = _run(
            "list native Global Themes",
            [
                "kpackagetool6",
                "--type",
                "Plasma/LookAndFeel",
                "--list",
            ],
            environment,
        )
        _assert_contains("KPackage", look_and_feel, GLOBAL_THEME_ID)
        _assert_contains(
            "plasma-apply-lookandfeel",
            _run(
                "list applicable Global Themes",
                ["plasma-apply-lookandfeel", "--list"],
                environment,
            ),
            GLOBAL_THEME_ID,
        )
        _assert_contains(
            "plasma-apply-desktoptheme",
            _run(
                "list Plasma Styles",
                ["plasma-apply-desktoptheme", "--list-themes"],
                environment,
            ),
            "Vapor",
        )
        _kpackage_discovery_checks(
            environment,
            data_home / "wallpapers" / "Vapor",
            temporary,
        )
        _qt_resource_checks(environment, data_home)

        display_environment = _xcb_environment(environment)
        xvfb = subprocess.Popen(
            [
                "Xvfb",
                ":99",
                "-screen",
                "0",
                "2560x720x24",
                "+extension",
                "RANDR",
                "-nolisten",
                "tcp",
            ],
            env=display_environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            _wait_for_xvfb(xvfb)
            _exercise_splash(display_environment)
            _exercise_plasma_lifecycle(
                display_environment,
                temporary,
                bundle_root / "uninstall.sh",
            )
        finally:
            if xvfb.poll() is None:
                signal_process_group(xvfb.pid, signal.SIGTERM)
                xvfb.communicate(timeout=10)

        if (
            config_home / "plasma-org.kde.plasma.desktop-appletsrc"
        ).read_bytes() != custom_layout:
            raise RuntimeError("uninstall changed the desktop wallpaper or layout")
        if (config_home / "kscreenlockerrc").read_bytes() != lockscreen:
            raise RuntimeError("uninstall changed lock-screen settings")
        listed_after = _run(
            "list Global Themes after uninstall",
            [
                "kpackagetool6",
                "--type",
                "Plasma/LookAndFeel",
                "--list",
            ],
            environment,
        )
        if GLOBAL_THEME_ID in listed_after:
            raise RuntimeError("uninstall left the Vapor Global Theme discoverable")


def main() -> int:
    arguments = _parser().parse_args()
    try:
        run_probe(arguments.bundle)
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print("Vapor passed native Fedora Plasma runtime checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
