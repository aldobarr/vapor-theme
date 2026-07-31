import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from tests.test_compiler_identity import REPO_ROOT


class GitHubIncidentTests(unittest.TestCase):
    def test_rejects_impossible_action_and_incident_combinations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            invalid_plan = temporary / "invalid.json"
            invalid_plan.write_text(
                json.dumps(
                    {
                        "action": "failure",
                        "error": "failed",
                        "incident": {
                            "action": "open-or-update",
                            "key": "vapor-updater",
                            "title": "[automation] Vapor updater failure",
                        },
                        "schema_version": 1,
                        "stable_release": "44.20260730",
                    }
                ),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["VAPOR_GH_COMMAND"] = json.dumps(
                [sys.executable, "-c", "print('[]')"]
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vapor_theme",
                    "github-incident",
                    "--plan",
                    str(invalid_plan),
                    "--repository",
                    "owner/vapor",
                    "--run-url",
                    "https://github.example/runs/123",
                ],
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("failure plan fields", result.stderr.lower())

    def test_failure_is_deduplicated_and_recovery_closes_incident(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            fake_gh = temporary / "fake_gh.py"
            log = temporary / "calls.jsonl"
            fake_gh.write_text(
                textwrap.dedent(
                    """\
                    import json
                    import os
                    import sys
                    from pathlib import Path

                    log = Path(sys.argv[1])
                    arguments = sys.argv[2:]
                    with log.open("a", encoding="utf-8") as output:
                        output.write(json.dumps(arguments) + "\\n")
                    if arguments[:2] == ["issue", "list"]:
                        print(os.environ.get("FAKE_GH_ISSUES", "[]"))
                    """
                ),
                encoding="utf-8",
                newline="\n",
            )
            failure_plan = temporary / "failure.json"
            failure_plan.write_text(
                json.dumps(
                    {
                        "action": "failure",
                        "error": "patch no longer applies",
                        "incident": {
                            "action": "open-or-update",
                            "key": "vapor-updater",
                            "title": "[automation] Vapor updater failure",
                        },
                        "schema_version": 1,
                    }
                ),
                encoding="utf-8",
            )
            command = [
                sys.executable,
                "-m",
                "vapor_theme",
                "github-incident",
                "--plan",
                str(failure_plan),
                "--repository",
                "owner/vapor",
                "--run-url",
                "https://github.example/runs/123",
            ]
            environment = os.environ.copy()
            environment["VAPOR_GH_COMMAND"] = json.dumps(
                [sys.executable, str(fake_gh), str(log)]
            )

            first = subprocess.run(
                command,
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            first_calls = [
                json.loads(line)
                for line in log.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(first_calls[0][:2], ["issue", "list"])
            self.assertEqual(first_calls[1][:2], ["issue", "create"])
            self.assertIn("[automation] Vapor updater failure", first_calls[1])
            self.assertTrue(
                any("patch no longer applies" in value for value in first_calls[1])
            )

            log.write_text("", encoding="utf-8")
            environment["FAKE_GH_ISSUES"] = json.dumps(
                [
                    {
                        "number": 17,
                        "state": "OPEN",
                        "title": "[automation] Vapor updater failure",
                    }
                ]
            )
            repeated = subprocess.run(
                command,
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            repeated_calls = [
                json.loads(line)
                for line in log.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(repeated_calls[0][:2], ["issue", "list"])
            self.assertEqual(repeated_calls[1][:3], ["issue", "comment", "17"])
            self.assertFalse(
                any(call[:2] == ["issue", "create"] for call in repeated_calls)
            )

            success_plan = temporary / "success.json"
            success_plan.write_text(
                json.dumps(
                    {
                        "action": "none",
                        "incident": "close",
                        "reason": "already-checked",
                        "schema_version": 1,
                        "stable_release": "stable-44.20260730",
                    }
                ),
                encoding="utf-8",
            )
            log.write_text("", encoding="utf-8")
            recovered = subprocess.run(
                [
                    *command[:5],
                    str(success_plan),
                    *command[6:],
                ],
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            recovery_calls = [
                json.loads(line)
                for line in log.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(recovery_calls[0][:2], ["issue", "list"])
            self.assertEqual(recovery_calls[1][:3], ["issue", "close", "17"])
            self.assertTrue(any("Recovered" in value for value in recovery_calls[1]))

    def test_ignored_check_closes_an_existing_incident(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            fake_gh = temporary / "fake_gh.py"
            log = temporary / "calls.jsonl"
            fake_gh.write_text(
                textwrap.dedent(
                    """\
                    import json
                    import sys
                    from pathlib import Path

                    log = Path(sys.argv[1])
                    arguments = sys.argv[2:]
                    with log.open("a", encoding="utf-8") as output:
                        output.write(json.dumps(arguments) + "\\n")
                    if arguments[:2] == ["issue", "list"]:
                        print(json.dumps([{
                            "number": 23,
                            "state": "OPEN",
                            "title": "[automation] Vapor updater failure",
                        }]))
                    """
                ),
                encoding="utf-8",
                newline="\n",
            )
            ignored_plan = temporary / "ignored.json"
            ignored_plan.write_text(
                json.dumps(
                    {
                        "action": "ignored",
                        "incident": "close",
                        "reason": "prerelease",
                        "schema_version": 1,
                    }
                ),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["VAPOR_GH_COMMAND"] = json.dumps(
                [sys.executable, str(fake_gh), str(log)]
            )

            recovered = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vapor_theme",
                    "github-incident",
                    "--plan",
                    str(ignored_plan),
                    "--repository",
                    "owner/vapor",
                    "--run-url",
                    "https://github.example/runs/456",
                ],
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            calls = [
                json.loads(line)
                for line in log.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(calls[0][:2], ["issue", "list"])
            self.assertEqual(calls[1][:3], ["issue", "close", "23"])


if __name__ == "__main__":
    unittest.main()
