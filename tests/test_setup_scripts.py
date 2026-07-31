import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_installer import build_and_extract_bundle


class SetupScriptTests(unittest.TestCase):
    def test_setup_scripts_ignore_missing_plasma_objects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            bundle = build_and_extract_bundle(temporary)
            scripts = (
                bundle
                / "payload"
                / "plasma"
                / "look-and-feel"
                / "com.valve.vapor.desktop"
                / "contents"
                / "plasmoidsetupscripts"
            )
            harness = temporary / "missing-object-harness.js"
            harness.write_text(
                (
                    'const fs = require("fs");\n'
                    'const vm = require("vm");\n'
                    f"const root = {json.dumps(str(scripts))};\n"
                    "for (const name of [\n"
                    '  "org.kde.plasma.folder.js",\n'
                    '  "org.kde.plasma.kickoff.js",\n'
                    '  "org.kde.plasma.systemtray.js"\n'
                    "]) {\n"
                    "  vm.runInNewContext(fs.readFileSync(root + '/' + name, 'utf8'), {\n"
                    "    encodeURI,\n"
                    "    userDataPath() { throw new Error('must not resolve paths'); }\n"
                    "  }, { filename: name });\n"
                    "}\n"
                ),
                encoding="utf-8",
                newline="\n",
            )

            result = subprocess.run(
                ["node", str(harness)],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_new_plasma_objects_receive_portable_vapor_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            bundle = build_and_extract_bundle(temporary)
            scripts = (
                bundle
                / "payload"
                / "plasma"
                / "look-and-feel"
                / "com.valve.vapor.desktop"
                / "contents"
                / "plasmoidsetupscripts"
            )
            harness = temporary / "harness.js"
            harness.write_text(
                (
                    'const fs = require("fs");\n'
                    'const vm = require("vm");\n'
                    "const outputs = {};\n"
                    "for (const [name, path] of Object.entries({\n"
                    f"  folder: {json.dumps(str(scripts / 'org.kde.plasma.folder.js'))},\n"
                    f"  kickoff: {json.dumps(str(scripts / 'org.kde.plasma.kickoff.js'))},\n"
                    f"  tray: {json.dumps(str(scripts / 'org.kde.plasma.systemtray.js'))}\n"
                    "})) {\n"
                    "  const writes = [];\n"
                    "  const applet = {\n"
                    "    currentConfigGroup: [],\n"
                    "    wallpaperPlugin: null,\n"
                    "    reloads: 0,\n"
                    "    writeConfig(key, value) {\n"
                    "      writes.push({ key, value, group: this.currentConfigGroup });\n"
                    "    },\n"
                    "    reloadConfig() { this.reloads += 1; }\n"
                    "  };\n"
                    "  vm.runInNewContext(fs.readFileSync(path, 'utf8'), {\n"
                    "    applet,\n"
                    "    containment: {},\n"
                    "    encodeURI,\n"
                    "    userDataPath(type, relative) {\n"
                    "      if (type !== 'data') throw new Error('wrong XDG type');\n"
                    "      return '/tmp/XDG data/' + relative;\n"
                    "    }\n"
                    "  }, { filename: path });\n"
                    "  outputs[name] = {\n"
                    "    writes,\n"
                    "    reloads: applet.reloads,\n"
                    "    wallpaperPlugin: applet.wallpaperPlugin\n"
                    "  };\n"
                    "}\n"
                    "process.stdout.write(JSON.stringify(outputs));\n"
                ),
                encoding="utf-8",
                newline="\n",
            )
            result = subprocess.run(
                ["node", str(harness)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs = json.loads(result.stdout)

            self.assertEqual(outputs["folder"]["wallpaperPlugin"], "org.kde.image")
            self.assertEqual(outputs["folder"]["reloads"], 1)
            self.assertEqual(
                outputs["folder"]["writes"],
                [
                    {
                        "group": ["Wallpaper", "org.kde.image", "General"],
                        "key": "Image",
                        "value": (
                            "file:///tmp/XDG%20data/wallpapers/Vapor/"
                            "contents/images/3940x2160.jxl"
                        ),
                    }
                ],
            )
            self.assertEqual(
                outputs["kickoff"]["writes"],
                [
                    {
                        "group": ["General"],
                        "key": "icon",
                        "value": "vapor-bazzite",
                    }
                ],
            )
            self.assertEqual(outputs["kickoff"]["reloads"], 1)
            self.assertEqual(
                outputs["tray"]["writes"],
                [
                    {
                        "group": ["General"],
                        "key": "scaleIconsToFit",
                        "value": True,
                    }
                ],
            )
            self.assertEqual(outputs["tray"]["reloads"], 1)


if __name__ == "__main__":
    unittest.main()
