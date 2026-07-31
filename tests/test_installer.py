import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from tests.test_bundle_validation import run_cli
from tests.test_compiler_identity import create_source_fixture, write_text


def create_fake_kpackage_tool(root: Path) -> tuple[Path, Path]:
    tool = root / "fake_kpackagetool.py"
    log = root / "kpackage-operations.jsonl"
    tool.write_text(
        (
            "import json\n"
            "import os\n"
            "import shutil\n"
            "import sys\n"
            "from pathlib import Path\n"
            "\n"
            "args = sys.argv[1:]\n"
            "data_home = Path(os.environ['XDG_DATA_HOME'])\n"
            "package_root = data_home / 'plasma' / 'look-and-feel'\n"
            "package_root.mkdir(parents=True, exist_ok=True)\n"
            "log = Path(os.environ['VAPOR_TEST_KPACKAGE_LOG'])\n"
            "with log.open('a', encoding='utf-8') as output:\n"
            "    output.write(json.dumps(args) + '\\n')\n"
            "if '--install' in args:\n"
            "    source = Path(args[args.index('--install') + 1])\n"
            "    metadata = json.loads((source / 'metadata.json').read_text())\n"
            "    fail_preflight_version = os.environ.get(\n"
            "        'VAPOR_TEST_FAIL_PREFLIGHT_VERSION'\n"
            "    )\n"
            "    if (\n"
            "        metadata['KPlugin'].get('Version') == fail_preflight_version\n"
            "        and '.vapor-kpackage-preflight-' in data_home.as_posix()\n"
            "    ):\n"
            "        print('injected preflight failure', file=sys.stderr)\n"
            "        raise SystemExit(1)\n"
            "    destination = package_root / metadata['KPlugin']['Id']\n"
            "    if destination.exists():\n"
            "        print('package already exists', file=sys.stderr)\n"
            "        raise SystemExit(1)\n"
            "    shutil.copytree(source, destination)\n"
            "    fail_version = os.environ.get('VAPOR_TEST_FAIL_INSTALL_VERSION')\n"
            "    if (\n"
            "        metadata['KPlugin'].get('Version') == fail_version\n"
            "        and '.vapor-kpackage-preflight-' not in data_home.as_posix()\n"
            "    ):\n"
            "        print('injected install failure', file=sys.stderr)\n"
            "        raise SystemExit(1)\n"
            "elif '--remove' in args:\n"
            "    package_id = args[args.index('--remove') + 1]\n"
            "    if os.environ.get('VAPOR_TEST_FAIL_KPACKAGE_REMOVE') == package_id:\n"
            "        print('injected KPackage removal failure', file=sys.stderr)\n"
            "        raise SystemExit(1)\n"
            "    shutil.rmtree(package_root / package_id, ignore_errors=True)\n"
            "elif '--list' in args:\n"
            "    for package in sorted(package_root.glob('*')):\n"
            "        if package.is_dir():\n"
            "            print(package.name)\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    return tool, log


def create_fake_kconfig_tools(root: Path) -> tuple[Path, Path]:
    tool = root / "fake_kconfig.py"
    log = root / "kconfig-operations.jsonl"
    tool.write_text(
        (
            "import configparser\n"
            "import json\n"
            "import os\n"
            "import sys\n"
            "from pathlib import Path\n"
            "\n"
            "mode, args = sys.argv[1], sys.argv[2:]\n"
            "config_home = Path(os.environ['XDG_CONFIG_HOME'])\n"
            "log = Path(os.environ['VAPOR_TEST_KCONFIG_LOG'])\n"
            "with log.open('a', encoding='utf-8') as output:\n"
            "    output.write(json.dumps([mode, *args]) + '\\n')\n"
            "\n"
            "def load(name):\n"
            "    parser = configparser.ConfigParser()\n"
            "    parser.optionxform = str\n"
            "    path = config_home / name\n"
            "    if path.exists():\n"
            "        parser.read(path, encoding='utf-8')\n"
            "    return parser, path\n"
            "\n"
            "def save(parser, path):\n"
            "    path.parent.mkdir(parents=True, exist_ok=True)\n"
            "    with path.open('w', encoding='utf-8', newline='\\n') as output:\n"
            "        parser.write(output, space_around_delimiters=False)\n"
            "\n"
            "if mode == 'read':\n"
            "    name = args[args.index('--file') + 1]\n"
            "    group = args[args.index('--group') + 1]\n"
            "    key = args[args.index('--key') + 1]\n"
            "    default = args[args.index('--default') + 1]\n"
            "    parser, _ = load(name)\n"
            "    print(parser.get(group, key, fallback=default))\n"
            "elif mode == 'write':\n"
            "    name = args[args.index('--file') + 1]\n"
            "    group = args[args.index('--group') + 1]\n"
            "    key_index = args.index('--key')\n"
            "    key, value = args[key_index + 1], args[key_index + 2]\n"
            "    parser, path = load(name)\n"
            "    if not parser.has_section(group):\n"
            "        parser.add_section(group)\n"
            "    parser.set(group, key, value)\n"
            "    save(parser, path)\n"
            "elif mode == 'apply':\n"
            "    theme = args[args.index('--apply') + 1]\n"
            "    kdeglobals, kdeglobals_path = load('kdeglobals')\n"
            "    if not kdeglobals.has_section('KDE'):\n"
            "        kdeglobals.add_section('KDE')\n"
            "    if not kdeglobals.has_section('General'):\n"
            "        kdeglobals.add_section('General')\n"
            "    kdeglobals.set('KDE', 'LookAndFeelPackage', theme)\n"
            "    kdeglobals.set('General', 'ColorScheme', 'BreezeLight')\n"
            "    save(kdeglobals, kdeglobals_path)\n"
            "    plasmarc, plasmarc_path = load('plasmarc')\n"
            "    if not plasmarc.has_section('Theme'):\n"
            "        plasmarc.add_section('Theme')\n"
            "    plasmarc.set('Theme', 'name', 'default')\n"
            "    save(plasmarc, plasmarc_path)\n"
            "    ksplash, ksplash_path = load('ksplashrc')\n"
            "    if not ksplash.has_section('KSplash'):\n"
            "        ksplash.add_section('KSplash')\n"
            "    ksplash.set('KSplash', 'Theme', theme)\n"
            "    save(ksplash, ksplash_path)\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    return tool, log


def create_fake_component_commands(root: Path) -> dict[str, str]:
    tool = root / "fake_component_discovery.py"
    plugin_root = root / "qt-plugins"
    imageformats = plugin_root / "imageformats"
    imageformats.mkdir(parents=True, exist_ok=True)
    (imageformats / "kimg_jxl.so").write_bytes(b"fake-jxl-plugin")
    tool.write_text(
        (
            "import json\n"
            "import os\n"
            "import sys\n"
            "from pathlib import Path\n"
            "\n"
            "mode = sys.argv[1]\n"
            "log = Path(os.environ['VAPOR_TEST_COMPONENT_LOG'])\n"
            "with log.open('a', encoding='utf-8') as output:\n"
            "    output.write(mode + '\\n')\n"
            "if (\n"
            "    mode in {'desktop', 'colors'}\n"
            "    and os.environ.get('QT_QPA_PLATFORM') != 'offscreen'\n"
            "):\n"
            "    print('GUI discovery was not headless', file=sys.stderr)\n"
            "    raise SystemExit(1)\n"
            "data_home = Path(os.environ['XDG_DATA_HOME'])\n"
            "global_theme = (\n"
            "    data_home / 'plasma' / 'look-and-feel'\n"
            "    / 'com.valve.vapor.desktop'\n"
            ")\n"
            "metadata_path = global_theme / 'metadata.json'\n"
            "version = ''\n"
            "if metadata_path.is_file():\n"
            "    version = json.loads(metadata_path.read_text())['KPlugin']['Version']\n"
            "fail_version = os.environ.get(\n"
            "    'VAPOR_TEST_FAIL_COMPONENT_DISCOVERY_VERSION'\n"
            ")\n"
            "if (\n"
            "    version == fail_version\n"
            "    and '.vapor-kpackage-preflight-' not in data_home.as_posix()\n"
            "):\n"
            "    print('injected component discovery failure', file=sys.stderr)\n"
            "    raise SystemExit(1)\n"
            "paths = {\n"
            "    'desktop': data_home / 'plasma/desktoptheme/Vapor/metadata.json',\n"
            "    'colors': data_home / 'color-schemes/Vapor.colors',\n"
            "    'wallpaper': data_home / 'wallpapers/Vapor/metadata.json',\n"
            "    'icon': (\n"
            "        data_home\n"
            "        / 'icons/hicolor/scalable/places/vapor-bazzite.svg'\n"
            "    ),\n"
            "}\n"
            "if mode == 'qt':\n"
            f"    print({str(plugin_root)!r})\n"
            "elif mode in paths:\n"
            "    if not paths[mode].is_file():\n"
            "        print(f'{mode} was not discovered', file=sys.stderr)\n"
            "        raise SystemExit(1)\n"
            "    if mode == 'wallpaper':\n"
            "        wallpaper_metadata = json.loads(paths[mode].read_text())\n"
            "        if wallpaper_metadata.get('KPackageStructure') != "
            "'Wallpaper/Images':\n"
            "            print('wallpaper package type was not discovered', "
            "file=sys.stderr)\n"
            "            raise SystemExit(1)\n"
            "    print(paths[mode] if mode == 'icon' else 'Vapor')\n"
            "else:\n"
            "    raise SystemExit(f'unknown discovery mode: {mode}')\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    return {
        "VAPOR_TEST_COMPONENT_LOG": str(root / "component-discovery.jsonl"),
        "VAPOR_COLOR_SCHEME_COMMAND": json.dumps([sys.executable, str(tool), "colors"]),
        "VAPOR_DESKTOP_THEME_COMMAND": json.dumps(
            [sys.executable, str(tool), "desktop"]
        ),
        "VAPOR_ICON_FINDER_COMMAND": json.dumps([sys.executable, str(tool), "icon"]),
        "VAPOR_QT_PATHS_COMMAND": json.dumps([sys.executable, str(tool), "qt"]),
        "VAPOR_WALLPAPER_COMMAND": json.dumps([sys.executable, str(tool), "wallpaper"]),
    }


def build_and_extract_bundle(root: Path) -> Path:
    steam, bazzite, pins = create_source_fixture(root)
    archive = root / "vapor.tar.gz"
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
    if built.returncode != 0:
        raise AssertionError(built.stderr)
    extracted = root / "bundle"
    with tarfile.open(archive, "r:gz") as release:
        release.extractall(extracted, filter="data")
    return extracted / "vapor-44.20260730.1"


def run_bundle_script(
    bundle: Path,
    mode: str,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    shell = shutil.which("sh")
    if shell is None and os.name == "nt":
        candidate = Path("C:/Program Files/Git/bin/sh.exe")
        if candidate.is_file():
            shell = str(candidate)
    if shell is None:
        raise AssertionError("a POSIX sh is required to test the release wrappers")

    run_environment = environment.copy()
    if os.name == "nt":
        shim_directory = bundle.parent / ".vapor-test-bin"
        shim_directory.mkdir(exist_ok=True)
        python_shim = shim_directory / "python3"
        python_shim.write_text(
            (f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" "$@"\n'),
            encoding="utf-8",
            newline="\n",
        )
        python_shim.chmod(0o755)
        run_environment["PATH"] = (
            f"{shim_directory}{os.pathsep}{run_environment.get('PATH', '')}"
        )

    return subprocess.run(
        [shell, str(bundle / f"{mode}.sh")],
        env=run_environment,
        text=True,
        capture_output=True,
        check=False,
    )


def create_directory_link(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise OSError(result.stderr or result.stdout)
        return
    link.symlink_to(target, target_is_directory=True)


class InstallerTests(unittest.TestCase):
    def test_install_rejects_an_intermediate_link_outside_xdg_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            bundle = build_and_extract_bundle(temporary)
            fake_tool, tool_log = create_fake_kpackage_tool(temporary)
            component_commands = create_fake_component_commands(temporary)
            data_home = temporary / "managed data"
            outside = temporary / "outside data"
            outside.mkdir()
            create_directory_link(data_home / "plasma", outside)
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(temporary / "unused home"),
                    "XDG_DATA_HOME": str(data_home),
                    "XDG_CONFIG_HOME": str(temporary / "config"),
                    "VAPOR_KPACKAGE_COMMAND": json.dumps(
                        [sys.executable, str(fake_tool)]
                    ),
                    "VAPOR_TEST_KPACKAGE_LOG": str(tool_log),
                    **component_commands,
                }
            )

            rejected = run_bundle_script(bundle, "install", environment)

            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("escapes the XDG data root", rejected.stderr)
            self.assertEqual(list(outside.rglob("*")), [])
            self.assertFalse(
                (data_home / "vapor-theme" / "install-state.json").exists()
            )

    def test_uninstall_rejects_an_intermediate_link_outside_xdg_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            bundle = build_and_extract_bundle(temporary)
            fake_kpackage, kpackage_log = create_fake_kpackage_tool(temporary)
            fake_kconfig, kconfig_log = create_fake_kconfig_tools(temporary)
            component_commands = create_fake_component_commands(temporary)
            data_home = temporary / "managed data"
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(temporary / "unused home"),
                    "XDG_DATA_HOME": str(data_home),
                    "XDG_CONFIG_HOME": str(temporary / "config"),
                    "VAPOR_KPACKAGE_COMMAND": json.dumps(
                        [sys.executable, str(fake_kpackage)]
                    ),
                    "VAPOR_KREADCONFIG_COMMAND": json.dumps(
                        [sys.executable, str(fake_kconfig), "read"]
                    ),
                    "VAPOR_KWRITECONFIG_COMMAND": json.dumps(
                        [sys.executable, str(fake_kconfig), "write"]
                    ),
                    "VAPOR_APPLY_LOOKANDFEEL_COMMAND": json.dumps(
                        [sys.executable, str(fake_kconfig), "apply"]
                    ),
                    "VAPOR_TEST_KPACKAGE_LOG": str(kpackage_log),
                    "VAPOR_TEST_KCONFIG_LOG": str(kconfig_log),
                    **component_commands,
                }
            )
            installed = run_bundle_script(bundle, "install", environment)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            outside = temporary / "outside plasma"
            (data_home / "plasma").rename(outside)
            create_directory_link(data_home / "plasma", outside)
            outside_snapshot = {
                path.relative_to(outside).as_posix(): path.read_bytes()
                for path in outside.rglob("*")
                if path.is_file()
            }

            rejected = run_bundle_script(bundle, "uninstall", environment)

            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("escapes the XDG data root", rejected.stderr)
            self.assertEqual(
                {
                    path.relative_to(outside).as_posix(): path.read_bytes()
                    for path in outside.rglob("*")
                    if path.is_file()
                },
                outside_snapshot,
            )
            self.assertTrue((data_home / "vapor-theme" / "install-state.json").exists())

    @unittest.skipIf(
        os.name == "nt",
        "creating symlinks requires Windows developer mode; Fedora CI exercises this",
    )
    def test_initial_install_preserves_a_dangling_user_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            bundle = build_and_extract_bundle(temporary)
            fake_tool, tool_log = create_fake_kpackage_tool(temporary)
            data_home = temporary / "managed data"
            conflict = (
                data_home
                / "icons"
                / "hicolor"
                / "scalable"
                / "places"
                / "vapor-bazzite.svg"
            )
            conflict.parent.mkdir(parents=True)
            conflict.symlink_to("missing-user-owned-target.svg")
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(temporary / "unused home"),
                    "XDG_DATA_HOME": str(data_home),
                    "XDG_CONFIG_HOME": str(temporary / "config"),
                    "VAPOR_KPACKAGE_COMMAND": json.dumps(
                        [sys.executable, str(fake_tool)]
                    ),
                    "VAPOR_TEST_KPACKAGE_LOG": str(tool_log),
                }
            )

            rejected = run_bundle_script(bundle, "install", environment)

            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("pre-existing path", rejected.stderr)
            self.assertTrue(conflict.is_symlink())
            self.assertEqual(os.readlink(conflict), "missing-user-owned-target.svg")
            self.assertFalse(
                (
                    data_home / "plasma" / "look-and-feel" / "com.valve.vapor.desktop"
                ).exists()
            )
            self.assertFalse(
                (data_home / "vapor-theme" / "install-state.json").exists()
            )

    def test_rejects_manifested_non_desktop_payload_before_replacing_anything(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            bundle = build_and_extract_bundle(temporary)
            forbidden_relative = "konsole/Vapor.profile"
            forbidden = bundle / "payload" / Path(forbidden_relative)
            forbidden.parent.mkdir()
            forbidden.write_text("[Appearance]\nColorScheme=Vapor\n", encoding="utf-8")
            manifest_path = bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"][forbidden_relative] = hashlib.sha256(
                forbidden.read_bytes()
            ).hexdigest()
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            fake_tool, tool_log = create_fake_kpackage_tool(temporary)
            data_home = temporary / "forbidden data"
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(temporary / "unused home"),
                    "XDG_DATA_HOME": str(data_home),
                    "VAPOR_KPACKAGE_COMMAND": json.dumps(
                        [sys.executable, str(fake_tool)]
                    ),
                    "VAPOR_TEST_KPACKAGE_LOG": str(tool_log),
                }
            )

            rejected = run_bundle_script(bundle, "install", environment)

            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("exact desktop-only scope", rejected.stderr)
            self.assertFalse(tool_log.exists())
            self.assertFalse(data_home.exists())

    def test_rejects_semantically_invalid_payload_before_replacing_anything(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            bundle = build_and_extract_bundle(temporary)
            defaults_relative = (
                "plasma/look-and-feel/com.valve.vapor.desktop/contents/defaults"
            )
            defaults = bundle / "payload" / Path(defaults_relative)
            defaults.write_text(
                defaults.read_text(encoding="utf-8").replace(
                    "ColorScheme=Vapor",
                    "ColorScheme=Broken",
                ),
                encoding="utf-8",
                newline="\n",
            )
            manifest_path = bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"][defaults_relative] = hashlib.sha256(
                defaults.read_bytes()
            ).hexdigest()
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            fake_tool, tool_log = create_fake_kpackage_tool(temporary)
            data_home = temporary / "invalid data"
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(temporary / "unused home"),
                    "XDG_DATA_HOME": str(data_home),
                    "VAPOR_KPACKAGE_COMMAND": json.dumps(
                        [sys.executable, str(fake_tool)]
                    ),
                    "VAPOR_TEST_KPACKAGE_LOG": str(tool_log),
                }
            )

            rejected = run_bundle_script(bundle, "install", environment)

            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("defaults lack", rejected.stderr)
            self.assertFalse(tool_log.exists())
            self.assertFalse(data_home.exists())

    def test_installs_user_locally_without_activating_or_changing_kde_config(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            bundle = build_and_extract_bundle(temporary)
            data_home = temporary / "XDG data with spaces"
            config_home = temporary / "XDG config with spaces"
            real_home = temporary / "untouched real home"
            fake_tool, tool_log = create_fake_kpackage_tool(temporary)
            component_commands = create_fake_component_commands(temporary)
            config_files = (
                "kdeglobals",
                "plasmarc",
                "ksplashrc",
                "kscreenlockerrc",
                "plasma-org.kde.plasma.desktop-appletsrc",
            )
            original_config: dict[str, bytes] = {}
            for name in config_files:
                write_text(config_home, name, f"sentinel:{name}\n")
                original_config[name] = (config_home / name).read_bytes()

            install_script = bundle / "install.sh"
            uninstall_script = bundle / "uninstall.sh"
            runtime = bundle / "lib" / "vapor_installer.py"
            self.assertTrue(install_script.is_file())
            self.assertTrue(uninstall_script.is_file())
            self.assertTrue(runtime.is_file())

            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(real_home),
                    "XDG_DATA_HOME": str(data_home),
                    "XDG_CONFIG_HOME": str(config_home),
                    "VAPOR_KPACKAGE_COMMAND": json.dumps(
                        [sys.executable, str(fake_tool)]
                    ),
                    "VAPOR_TEST_KPACKAGE_LOG": str(tool_log),
                    **component_commands,
                }
            )
            installed = run_bundle_script(bundle, "install", environment)

            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertIn("installed vapor", installed.stdout.lower())
            manifest = json.loads(
                (bundle / "manifest.json").read_text(encoding="utf-8")
            )
            for relative_path, expected_hash in manifest["files"].items():
                installed_path = data_home.joinpath(*Path(relative_path).parts)
                self.assertTrue(installed_path.is_file(), relative_path)
                import hashlib

                actual_hash = hashlib.sha256(installed_path.read_bytes()).hexdigest()
                self.assertEqual(actual_hash, expected_hash, relative_path)

            state_path = data_home / "vapor-theme" / "install-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["version"], "44.20260730.1")
            self.assertEqual(state["files"], manifest["files"])
            self.assertFalse(real_home.exists())
            for name, expected in original_config.items():
                self.assertEqual((config_home / name).read_bytes(), expected)
            operations = [
                json.loads(line)
                for line in tool_log.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [operation.count("--install") for operation in operations],
                [1, 0, 0, 1, 0],
            )
            self.assertEqual(
                operations[1:3],
                [
                    ["--type", "Plasma/LookAndFeel", "--list"],
                    [
                        "--type",
                        "Plasma/LookAndFeel",
                        "--remove",
                        "com.valve.vapor.desktop",
                    ],
                ],
            )
            discovery_log = temporary / "component-discovery.jsonl"
            self.assertEqual(
                discovery_log.read_text(encoding="utf-8").splitlines(),
                [
                    "desktop",
                    "colors",
                    "wallpaper",
                    "icon",
                    "qt",
                    "desktop",
                    "colors",
                    "wallpaper",
                    "icon",
                    "qt",
                ],
            )

    def test_repeated_install_is_idempotent_and_conflicts_are_preserved(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            bundle = build_and_extract_bundle(temporary)
            fake_tool, tool_log = create_fake_kpackage_tool(temporary)
            component_commands = create_fake_component_commands(temporary)
            data_home = temporary / "managed data"
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(temporary / "unused home"),
                    "XDG_DATA_HOME": str(data_home),
                    "XDG_CONFIG_HOME": str(temporary / "config"),
                    "VAPOR_KPACKAGE_COMMAND": json.dumps(
                        [sys.executable, str(fake_tool)]
                    ),
                    "VAPOR_TEST_KPACKAGE_LOG": str(tool_log),
                    **component_commands,
                }
            )
            first = run_bundle_script(bundle, "install", environment)
            self.assertEqual(first.returncode, 0, first.stderr)
            operations_after_first = tool_log.read_bytes()
            second = run_bundle_script(bundle, "install", environment)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("already installed", second.stdout.lower())
            self.assertEqual(tool_log.read_bytes(), operations_after_first)

            conflict_home = temporary / "conflicting data"
            conflict = (
                conflict_home
                / "icons"
                / "hicolor"
                / "scalable"
                / "places"
                / "vapor-bazzite.svg"
            )
            conflict.parent.mkdir(parents=True)
            conflict.write_text("user-owned\n", encoding="utf-8")
            conflicting_environment = environment.copy()
            conflicting_environment["XDG_DATA_HOME"] = str(conflict_home)
            rejected = run_bundle_script(
                bundle,
                "install",
                conflicting_environment,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("pre-existing path", rejected.stderr)
            self.assertEqual(conflict.read_text(encoding="utf-8"), "user-owned\n")
            self.assertFalse(
                (
                    conflict_home
                    / "plasma"
                    / "look-and-feel"
                    / "com.valve.vapor.desktop"
                ).exists()
            )
            self.assertFalse(
                (conflict_home / "vapor-theme" / "install-state.json").exists()
            )

    def test_upgrade_is_transactional_and_downgrades_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)

            def build_version(version: str, color_marker: str) -> Path:
                sources = temporary / f"sources-{version}"
                steam, bazzite, pins_path = create_source_fixture(sources)
                colors = steam / "usr/share/color-schemes/Vapor.colors"
                colors.write_text(
                    colors.read_text(encoding="utf-8")
                    + f"[Marker]\nValue={color_marker}\n",
                    encoding="utf-8",
                    newline="\n",
                )
                pins = json.loads(pins_path.read_text(encoding="utf-8"))
                pins["project_version"] = version
                relative = "usr/share/color-schemes/Vapor.colors"
                import hashlib

                pins["inputs"][f"steam:{relative}"] = hashlib.sha256(
                    colors.read_bytes()
                ).hexdigest()
                pins_path.write_text(
                    json.dumps(pins, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                archive = sources / "vapor.tar.gz"
                built = run_cli(
                    "build",
                    "--steam-source",
                    str(steam),
                    "--bazzite-source",
                    str(bazzite),
                    "--pins",
                    str(pins_path),
                    "--output",
                    str(archive),
                )
                self.assertEqual(built.returncode, 0, built.stderr)
                extracted = sources / "bundle"
                with tarfile.open(archive, "r:gz") as release:
                    release.extractall(extracted, filter="data")
                return extracted / f"vapor-{version}"

            version_one = build_version("44.20260730.1", "one")
            version_two = build_version("44.20260730.2", "two")
            version_three = build_version("44.20260730.3", "three")
            fake_tool, tool_log = create_fake_kpackage_tool(temporary)
            component_commands = create_fake_component_commands(temporary)
            data_home = temporary / "upgrade data"
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(temporary / "unused home"),
                    "XDG_DATA_HOME": str(data_home),
                    "XDG_CONFIG_HOME": str(temporary / "config"),
                    "VAPOR_KPACKAGE_COMMAND": json.dumps(
                        [sys.executable, str(fake_tool)]
                    ),
                    "VAPOR_TEST_KPACKAGE_LOG": str(tool_log),
                    **component_commands,
                }
            )

            def install(
                bundle: Path,
                *,
                env: dict[str, str] | None = None,
            ) -> subprocess.CompletedProcess[str]:
                return run_bundle_script(bundle, "install", env or environment)

            first = install(version_one)
            self.assertEqual(first.returncode, 0, first.stderr)
            upgraded = install(version_two)
            self.assertEqual(upgraded.returncode, 0, upgraded.stderr)
            state_path = data_home / "vapor-theme" / "install-state.json"
            state_after_upgrade = state_path.read_bytes()
            state = json.loads(state_after_upgrade)
            self.assertEqual(state["version"], "44.20260730.2")
            self.assertIn(
                "Value=two",
                (data_home / "color-schemes" / "Vapor.colors").read_text(
                    encoding="utf-8"
                ),
            )
            snapshot = {
                relative: data_home.joinpath(*Path(relative).parts).read_bytes()
                for relative in state["files"]
            }

            downgrade = install(version_one)
            self.assertNotEqual(downgrade.returncode, 0)
            self.assertIn("downgrade", downgrade.stderr.lower())
            self.assertEqual(state_path.read_bytes(), state_after_upgrade)

            preflight_environment = environment.copy()
            preflight_environment["VAPOR_TEST_FAIL_PREFLIGHT_VERSION"] = "44.20260730.3"
            failed_preflight = install(version_three, env=preflight_environment)
            self.assertNotEqual(failed_preflight.returncode, 0)
            self.assertIn("injected preflight failure", failed_preflight.stderr)
            self.assertEqual(state_path.read_bytes(), state_after_upgrade)
            for relative, expected in snapshot.items():
                self.assertEqual(
                    data_home.joinpath(*Path(relative).parts).read_bytes(),
                    expected,
                    relative,
                )
            self.assertFalse(any(data_home.glob(".vapor-stage-*")))
            self.assertFalse(any(data_home.glob(".vapor-backup-*")))

            discovery_environment = environment.copy()
            discovery_environment["VAPOR_TEST_FAIL_COMPONENT_DISCOVERY_VERSION"] = (
                "44.20260730.3"
            )
            failed_discovery = install(version_three, env=discovery_environment)
            self.assertNotEqual(failed_discovery.returncode, 0)
            self.assertIn(
                "injected component discovery failure",
                failed_discovery.stderr,
            )
            self.assertEqual(state_path.read_bytes(), state_after_upgrade)
            for relative, expected in snapshot.items():
                self.assertEqual(
                    data_home.joinpath(*Path(relative).parts).read_bytes(),
                    expected,
                    relative,
                )
            self.assertFalse(any(data_home.glob(".vapor-stage-*")))
            self.assertFalse(any(data_home.glob(".vapor-backup-*")))

            failing_environment = environment.copy()
            failing_environment["VAPOR_TEST_FAIL_INSTALL_VERSION"] = "44.20260730.3"
            failed_upgrade = install(version_three, env=failing_environment)
            self.assertNotEqual(failed_upgrade.returncode, 0)
            self.assertIn("injected install failure", failed_upgrade.stderr)
            self.assertEqual(state_path.read_bytes(), state_after_upgrade)
            for relative, expected in snapshot.items():
                self.assertEqual(
                    data_home.joinpath(*Path(relative).parts).read_bytes(),
                    expected,
                    relative,
                )
            self.assertFalse(
                any(data_home.glob(".vapor-stage-*")),
            )
            self.assertFalse(
                any(data_home.glob(".vapor-backup-*")),
            )

            retained_home = temporary / "retained upgrade data"
            retained_environment = environment.copy()
            retained_environment["XDG_DATA_HOME"] = str(retained_home)
            retained_first = install(version_one, env=retained_environment)
            self.assertEqual(
                retained_first.returncode,
                0,
                retained_first.stderr,
            )
            retained_state_path = retained_home / "vapor-theme" / "install-state.json"
            retained_state = json.loads(retained_state_path.read_text(encoding="utf-8"))
            retained_state["files"] = {
                relative: digest
                for relative, digest in retained_state["files"].items()
                if relative.startswith("wallpapers/Vapor/")
                or relative.endswith("vapor-bazzite.svg")
            }
            retained_state["retained"] = {
                "launcher_icon": "still selected",
                "wallpaper": "still selected",
            }
            retained_state_path.write_text(
                json.dumps(retained_state, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            (retained_home / "color-schemes" / "Vapor.colors").unlink()
            shutil.rmtree(retained_home / "plasma" / "desktoptheme" / "Vapor")
            shutil.rmtree(
                retained_home / "plasma" / "look-and-feel" / "com.valve.vapor.desktop"
            )

            retained_upgrade = install(version_two, env=retained_environment)
            self.assertEqual(
                retained_upgrade.returncode,
                0,
                retained_upgrade.stderr,
            )
            upgraded_retained_state = json.loads(
                retained_state_path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                upgraded_retained_state["version"],
                "44.20260730.2",
            )
            self.assertNotIn("retained", upgraded_retained_state)
            self.assertTrue(
                (
                    retained_home
                    / "plasma"
                    / "look-and-feel"
                    / "com.valve.vapor.desktop"
                ).is_dir()
            )

    def test_uninstall_keeps_ownership_state_when_component_removal_is_incomplete(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            bundle = build_and_extract_bundle(temporary)
            fake_kpackage, kpackage_log = create_fake_kpackage_tool(temporary)
            fake_kconfig, kconfig_log = create_fake_kconfig_tools(temporary)
            component_commands = create_fake_component_commands(temporary)
            data_home = temporary / "data"
            config_home = temporary / "config"
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(temporary / "unused home"),
                    "XDG_DATA_HOME": str(data_home),
                    "XDG_CONFIG_HOME": str(config_home),
                    "VAPOR_KPACKAGE_COMMAND": json.dumps(
                        [sys.executable, str(fake_kpackage)]
                    ),
                    "VAPOR_KREADCONFIG_COMMAND": json.dumps(
                        [sys.executable, str(fake_kconfig), "read"]
                    ),
                    "VAPOR_KWRITECONFIG_COMMAND": json.dumps(
                        [sys.executable, str(fake_kconfig), "write"]
                    ),
                    "VAPOR_APPLY_LOOKANDFEEL_COMMAND": json.dumps(
                        [sys.executable, str(fake_kconfig), "apply"]
                    ),
                    "VAPOR_TEST_KPACKAGE_LOG": str(kpackage_log),
                    "VAPOR_TEST_KCONFIG_LOG": str(kconfig_log),
                    **component_commands,
                }
            )
            installed = run_bundle_script(bundle, "install", environment)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            state_path = data_home / "vapor-theme" / "install-state.json"
            failing_environment = environment.copy()
            failing_environment["VAPOR_TEST_FAIL_KPACKAGE_REMOVE"] = (
                "com.valve.vapor.desktop"
            )
            rejected = run_bundle_script(
                bundle,
                "uninstall",
                failing_environment,
            )

            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("injected KPackage removal failure", rejected.stderr)
            self.assertTrue(
                state_path.exists(),
                "ownership state must survive an incomplete uninstall",
            )
            removed = run_bundle_script(bundle, "uninstall", environment)
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertIn("Uninstalled Vapor", removed.stdout)
            self.assertFalse(state_path.exists())

    def test_uninstall_detects_active_theme_in_kde_managed_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            bundle = build_and_extract_bundle(temporary)
            fake_kpackage, kpackage_log = create_fake_kpackage_tool(temporary)
            fake_kconfig, kconfig_log = create_fake_kconfig_tools(temporary)
            component_commands = create_fake_component_commands(temporary)
            data_home = temporary / "data"
            config_home = temporary / "config"
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(temporary / "unused home"),
                    "XDG_DATA_HOME": str(data_home),
                    "XDG_CONFIG_HOME": str(config_home),
                    "VAPOR_KPACKAGE_COMMAND": json.dumps(
                        [sys.executable, str(fake_kpackage)]
                    ),
                    "VAPOR_KREADCONFIG_COMMAND": json.dumps(
                        [sys.executable, str(fake_kconfig), "read"]
                    ),
                    "VAPOR_KWRITECONFIG_COMMAND": json.dumps(
                        [sys.executable, str(fake_kconfig), "write"]
                    ),
                    "VAPOR_APPLY_LOOKANDFEEL_COMMAND": json.dumps(
                        [sys.executable, str(fake_kconfig), "apply"]
                    ),
                    "VAPOR_TEST_KPACKAGE_LOG": str(kpackage_log),
                    "VAPOR_TEST_KCONFIG_LOG": str(kconfig_log),
                    **component_commands,
                }
            )
            installed = run_bundle_script(bundle, "install", environment)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            write_text(
                config_home / "kdedefaults",
                "kdeglobals",
                (
                    "[KDE]\n"
                    "LookAndFeelPackage=com.valve.vapor.desktop\n"
                    "[General]\n"
                    "ColorScheme=Vapor\n"
                ),
            )

            removed = run_bundle_script(bundle, "uninstall", environment)

            self.assertEqual(removed.returncode, 0, removed.stderr)
            operations = [
                json.loads(line)
                for line in kconfig_log.read_text(encoding="utf-8").splitlines()
            ]
            self.assertIn(
                [
                    "apply",
                    "--apply",
                    "org.kde.breeze.desktop",
                    "--keep-auto",
                ],
                operations,
            )
            self.assertFalse(
                (
                    data_home / "plasma" / "look-and-feel" / "com.valve.vapor.desktop"
                ).exists()
            )
            self.assertIn(
                "LookAndFeelPackage=org.kde.breeze.desktop",
                (config_home / "kdeglobals").read_text(encoding="utf-8"),
            )

    def test_uninstall_is_reference_safe_and_never_mutates_wallpapers(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            bundle = build_and_extract_bundle(temporary)
            fake_kpackage, kpackage_log = create_fake_kpackage_tool(temporary)
            fake_kconfig, kconfig_log = create_fake_kconfig_tools(temporary)
            component_commands = create_fake_component_commands(temporary)
            data_home = temporary / "data"
            config_home = temporary / "config"
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(temporary / "unused home"),
                    "XDG_DATA_HOME": str(data_home),
                    "XDG_CONFIG_HOME": str(config_home),
                    "VAPOR_KPACKAGE_COMMAND": json.dumps(
                        [sys.executable, str(fake_kpackage)]
                    ),
                    "VAPOR_KREADCONFIG_COMMAND": json.dumps(
                        [sys.executable, str(fake_kconfig), "read"]
                    ),
                    "VAPOR_KWRITECONFIG_COMMAND": json.dumps(
                        [sys.executable, str(fake_kconfig), "write"]
                    ),
                    "VAPOR_APPLY_LOOKANDFEEL_COMMAND": json.dumps(
                        [sys.executable, str(fake_kconfig), "apply"]
                    ),
                    "VAPOR_TEST_KPACKAGE_LOG": str(kpackage_log),
                    "VAPOR_TEST_KCONFIG_LOG": str(kconfig_log),
                    **component_commands,
                }
            )
            installed = run_bundle_script(bundle, "install", environment)
            self.assertEqual(installed.returncode, 0, installed.stderr)

            write_text(
                config_home,
                "kdeglobals",
                (
                    "[KDE]\n"
                    "LookAndFeelPackage=com.valve.vapor.desktop\n"
                    "DefaultLightLookAndFeel=com.valve.vapor.desktop\n"
                    "DefaultDarkLookAndFeel=com.valve.vapor.desktop\n"
                    "AutomaticLookAndFeel=true\n"
                    "[General]\n"
                    "ColorScheme=Vapor\n"
                ),
            )
            write_text(config_home, "plasmarc", "[Theme]\nname=Vapor\n")
            write_text(
                config_home,
                "ksplashrc",
                "[KSplash]\nTheme=com.valve.vapor.desktop\n",
            )
            convergence = (
                data_home
                / "wallpapers"
                / "Vapor"
                / "contents"
                / "images"
                / "3940x2160.jxl"
            )
            write_text(
                config_home,
                "kscreenlockerrc",
                (
                    "[Greeter][Wallpaper][org.kde.image][General]\n"
                    f"Image=file://{convergence}\n"
                ),
            )
            write_text(
                config_home,
                "plasma-org.kde.plasma.desktop-appletsrc",
                (
                    "[Containments][1][Wallpaper][org.kde.image][General]\n"
                    f"Image=file://{convergence}\n"
                    "[Containments][2][Applets][3][Configuration][General]\n"
                    "icon=vapor-bazzite\n"
                ),
            )
            lock_before = (config_home / "kscreenlockerrc").read_bytes()
            layout_before = (
                config_home / "plasma-org.kde.plasma.desktop-appletsrc"
            ).read_bytes()
            sibling = data_home / "wallpapers" / "UserWallpaper" / "keep.txt"
            sibling.parent.mkdir(parents=True)
            sibling.write_text("keep\n", encoding="utf-8")

            removed = run_bundle_script(bundle, "uninstall", environment)
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertIn("retained", removed.stdout.lower())
            self.assertFalse(
                (
                    data_home / "plasma" / "look-and-feel" / "com.valve.vapor.desktop"
                ).exists()
            )
            self.assertFalse((data_home / "plasma" / "desktoptheme" / "Vapor").exists())
            self.assertFalse((data_home / "color-schemes" / "Vapor.colors").exists())
            self.assertTrue((data_home / "wallpapers" / "Vapor").exists())
            self.assertTrue(
                (
                    data_home
                    / "icons"
                    / "hicolor"
                    / "scalable"
                    / "places"
                    / "vapor-bazzite.svg"
                ).exists()
            )
            self.assertEqual(
                (config_home / "kscreenlockerrc").read_bytes(),
                lock_before,
            )
            self.assertEqual(
                (config_home / "plasma-org.kde.plasma.desktop-appletsrc").read_bytes(),
                layout_before,
            )
            self.assertEqual(sibling.read_text(encoding="utf-8"), "keep\n")

            state_path = data_home / "vapor-theme" / "install-state.json"
            retained_state_bytes = state_path.read_bytes()
            retained_state = json.loads(retained_state_bytes)
            self.assertEqual(
                set(retained_state["retained"]),
                {"launcher_icon", "wallpaper"},
            )
            self.assertTrue(
                all(
                    path.startswith("wallpapers/Vapor/")
                    or path.endswith("vapor-bazzite.svg")
                    for path in retained_state["files"]
                )
            )
            kdeglobals = (config_home / "kdeglobals").read_text(encoding="utf-8")
            self.assertIn(
                "LookAndFeelPackage=org.kde.breeze.desktop",
                kdeglobals,
            )
            self.assertIn(
                "DefaultLightLookAndFeel=org.kde.breeze.desktop",
                kdeglobals,
            )
            self.assertIn(
                "DefaultDarkLookAndFeel=org.kde.breezedark.desktop",
                kdeglobals,
            )
            self.assertIn("AutomaticLookAndFeel=true", kdeglobals)
            operations_after_first = kpackage_log.read_bytes()

            repeated = run_bundle_script(bundle, "uninstall", environment)
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(kpackage_log.read_bytes(), operations_after_first)
            self.assertEqual(state_path.read_bytes(), retained_state_bytes)

            write_text(
                config_home,
                "kscreenlockerrc",
                "[Greeter][Wallpaper][org.kde.image][General]\n"
                "Image=file:///somewhere/else.jpg\n",
            )
            write_text(
                config_home,
                "plasma-org.kde.plasma.desktop-appletsrc",
                "[Containments][1]\nplugin=org.kde.plasma.folder\n",
            )
            finished = run_bundle_script(bundle, "uninstall", environment)
            self.assertEqual(finished.returncode, 0, finished.stderr)
            self.assertFalse((data_home / "wallpapers" / "Vapor").exists())
            self.assertFalse(
                (
                    data_home
                    / "icons"
                    / "hicolor"
                    / "scalable"
                    / "places"
                    / "vapor-bazzite.svg"
                ).exists()
            )
            self.assertFalse(state_path.exists())
            self.assertEqual(
                (config_home / "kscreenlockerrc").read_text(encoding="utf-8"),
                "[Greeter][Wallpaper][org.kde.image][General]\n"
                "Image=file:///somewhere/else.jpg\n",
            )


if __name__ == "__main__":
    unittest.main()
