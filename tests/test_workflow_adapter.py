import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_bundle_validation import run_cli
from tests.test_compiler_identity import create_source_fixture


def _git(
    repository: Path,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        text=True,
        capture_output=True,
        check=check,
    )


def _commit(repository: Path, message: str) -> str:
    _git(repository, "add", ".")
    _git(
        repository,
        "-c",
        "user.name=Vapor Tests",
        "-c",
        "user.email=vapor-tests@example.invalid",
        "commit",
        "-q",
        "-m",
        message,
    )
    return _git(repository, "rev-parse", "HEAD").stdout.strip()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


class WorkflowAdapterTests(unittest.TestCase):
    def test_cli_atomically_rejects_a_replaced_release_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            remote = temporary / "remote.git"
            publisher = temporary / "publisher"
            competitor = temporary / "competitor"
            subprocess.run(
                ["git", "init", "--bare", "-q", str(remote)],
                text=True,
                capture_output=True,
                check=True,
            )
            publisher.mkdir()
            _git(publisher, "init", "-q")
            tracked = publisher / "tracked.txt"
            tracked.write_text("baseline\n", encoding="utf-8", newline="\n")
            baseline = _commit(publisher, "baseline")
            _git(publisher, "branch", "-M", "master")
            _git(publisher, "remote", "add", "origin", str(remote))
            _git(publisher, "push", "-q", "-u", "origin", "master")
            tracked.write_text("release\n", encoding="utf-8", newline="\n")
            release_commit = _commit(publisher, "release")
            _git(
                publisher,
                "-c",
                "user.name=Vapor Tests",
                "-c",
                "user.email=vapor-tests@example.invalid",
                "tag",
                "-a",
                "v44.20260721.1",
                "-m",
                "release",
            )
            tag_object = _git(publisher, "rev-parse", "v44.20260721.1").stdout.strip()
            _git(publisher, "push", "-q", "origin", "v44.20260721.1")

            subprocess.run(
                ["git", "clone", "-q", str(remote), str(competitor)],
                text=True,
                capture_output=True,
                check=True,
            )
            _git(
                competitor,
                "-c",
                "user.name=Vapor Tests",
                "-c",
                "user.email=vapor-tests@example.invalid",
                "tag",
                "-f",
                "-a",
                "v44.20260721.1",
                baseline,
                "-m",
                "replacement",
            )
            replacement_tag_object = _git(
                competitor, "rev-parse", "v44.20260721.1"
            ).stdout.strip()
            _git(
                competitor,
                "push",
                "-q",
                "--force",
                "origin",
                "v44.20260721.1",
            )

            result = run_cli(
                "advance-release-branch",
                "--repository",
                str(publisher),
                "--remote",
                "origin",
                "--branch",
                "master",
                "--expected-branch-commit",
                baseline,
                "--tag",
                "v44.20260721.1",
                "--expected-tag-object",
                tag_object,
                "--release-commit",
                release_commit,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("stale info", result.stderr)
            remote_refs = {
                fields[1]: fields[0]
                for line in _git(
                    publisher,
                    "ls-remote",
                    "origin",
                    "refs/heads/master",
                    "refs/tags/v44.20260721.1",
                ).stdout.splitlines()
                if len(fields := line.split()) == 2
            }
            self.assertEqual(remote_refs["refs/heads/master"], baseline)
            self.assertEqual(
                remote_refs["refs/tags/v44.20260721.1"], replacement_tag_object
            )

    def test_cli_reads_the_stable_tag_from_one_release_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            release = Path(temporary_directory) / "release.json"
            release.write_text(
                json.dumps(
                    {
                        "draft": False,
                        "prerelease": False,
                        "published_at": "2026-07-30T12:00:00Z",
                        "tag_name": "stable-44.20260730",
                    }
                ),
                encoding="utf-8",
            )

            result = run_cli(
                "upstream-release-tag",
                "--release-json",
                str(release),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "stable-44.20260730\n")

    def test_cli_resolves_the_steam_preset_tag_from_bazzite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            _, bazzite, _ = create_source_fixture(Path(temporary_directory))

            result = run_cli(
                "steam-preset-tag",
                "--bazzite-source",
                str(bazzite),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "0.30\n")

    def test_cli_validates_and_emits_release_decision_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = Path(temporary_directory) / "plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "action": "release",
                        "artifact": "Vapor.tar.gz",
                        "artifact_sha256": "a" * 64,
                        "incident": "close",
                        "schema_version": 1,
                        "stable_release": "stable-44.20260730",
                        "tag": "v44.20260730.1",
                        "theme_fingerprint": "fingerprint",
                        "version": "44.20260730.1",
                    }
                ),
                encoding="utf-8",
            )

            result = run_cli(
                "decision-outputs",
                "--plan",
                str(plan),
                "--updater-status",
                "0",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    "status=0",
                    "action=release",
                    "tag=v44.20260730.1",
                ],
            )

    def test_cli_converts_an_invalid_decision_into_a_failure_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = Path(temporary_directory) / "plan.json"
            plan.write_text('{"action":"invented"}\n', encoding="utf-8")

            result = run_cli(
                "decision-outputs",
                "--plan",
                str(plan),
                "--updater-status",
                "0",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                ["status=1", "action=failure"],
            )
            failure = json.loads(plan.read_text(encoding="utf-8"))
            self.assertEqual(failure["action"], "failure")
            self.assertIn("invalid decision plan", failure["error"])

    def test_cli_allocates_the_next_revision_after_a_preserved_release_loses_its_lease(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            remote = temporary / "remote.git"
            publisher = temporary / "publisher"
            competitor = temporary / "competitor"
            recovery = temporary / "recovery"
            subprocess.run(
                ["git", "init", "--bare", "-q", str(remote)],
                text=True,
                capture_output=True,
                check=True,
            )
            publisher.mkdir()
            _git(publisher, "init", "-q")
            tracked = publisher / "tracked.txt"
            tracked.write_text("baseline\n", encoding="utf-8", newline="\n")
            baseline = _commit(publisher, "baseline")
            _git(publisher, "branch", "-M", "master")
            _git(publisher, "remote", "add", "origin", str(remote))
            _git(publisher, "push", "-q", "-u", "origin", "master")

            tracked.write_text("release one\n", encoding="utf-8", newline="\n")
            first_release = _commit(publisher, "preserved release")
            _git(
                publisher,
                "-c",
                "user.name=Vapor Tests",
                "-c",
                "user.email=vapor-tests@example.invalid",
                "tag",
                "-a",
                "v44.20260721.1",
                "-m",
                "preserved release",
            )
            first_tag_object = _git(
                publisher, "rev-parse", "v44.20260721.1"
            ).stdout.strip()
            _git(publisher, "push", "-q", "origin", "v44.20260721.1")

            subprocess.run(
                ["git", "clone", "-q", str(remote), str(competitor)],
                text=True,
                capture_output=True,
                check=True,
            )
            competing_file = competitor / "competing.txt"
            competing_file.write_text("keep\n", encoding="utf-8", newline="\n")
            competing_commit = _commit(competitor, "concurrent default-branch work")
            _git(competitor, "push", "-q", "origin", "master")

            lost_lease = run_cli(
                "advance-release-branch",
                "--repository",
                str(publisher),
                "--remote",
                "origin",
                "--branch",
                "master",
                "--expected-branch-commit",
                baseline,
                "--tag",
                "v44.20260721.1",
                "--expected-tag-object",
                first_tag_object,
                "--release-commit",
                first_release,
            )
            self.assertNotEqual(lost_lease.returncode, 0)
            self.assertIn("stale info", lost_lease.stderr)

            subprocess.run(
                ["git", "clone", "-q", str(remote), str(recovery)],
                text=True,
                capture_output=True,
                check=True,
            )
            upstream_release = temporary / "upstream-release.json"
            state = temporary / "state.json"
            project_release_tags = temporary / "project-release-tags.txt"
            project_release_tags.write_text("", encoding="utf-8")
            _write_json(
                upstream_release,
                {
                    "draft": False,
                    "prerelease": False,
                    "published_at": "2026-07-21T00:00:00Z",
                    "tag_name": "stable-44.20260721",
                },
            )
            _write_json(
                state,
                {
                    "heartbeat_month": "2026-07",
                    "last_checked_stable": "stable-44.20260701",
                    "last_successful_check": "2026-07-01T00:00:00Z",
                    "schema_version": 1,
                    "theme_fingerprint": "old-fingerprint",
                    "version": "44.20260701.1",
                },
            )
            selected = run_cli(
                "select-release-version",
                "--repository",
                str(recovery),
                "--release-json",
                str(upstream_release),
                "--state",
                str(state),
                "--occupied-release-tags",
                str(project_release_tags),
            )
            self.assertEqual(selected.returncode, 0, selected.stderr)
            self.assertEqual(selected.stdout, "44.20260721.2\n")

            recovered_file = recovery / "recovered.txt"
            recovered_file.write_text("release two\n", encoding="utf-8", newline="\n")
            recovered_release = _commit(recovery, "unattended recovery release")
            _git(
                recovery,
                "-c",
                "user.name=Vapor Tests",
                "-c",
                "user.email=vapor-tests@example.invalid",
                "tag",
                "-a",
                "v44.20260721.2",
                "-m",
                "unattended recovery release",
            )
            publish = run_cli(
                "publish-release-tag",
                "--repository",
                str(recovery),
                "--remote",
                "origin",
                "--tag",
                "v44.20260721.2",
            )
            self.assertEqual(publish.returncode, 0, publish.stderr)
            self.assertIn("tag_created=true", publish.stdout)
            second_tag_object = _git(
                recovery, "rev-parse", "v44.20260721.2"
            ).stdout.strip()
            advance = run_cli(
                "advance-release-branch",
                "--repository",
                str(recovery),
                "--remote",
                "origin",
                "--branch",
                "master",
                "--expected-branch-commit",
                competing_commit,
                "--tag",
                "v44.20260721.2",
                "--expected-tag-object",
                second_tag_object,
                "--release-commit",
                recovered_release,
            )
            self.assertEqual(advance.returncode, 0, advance.stderr)
            self.assertEqual(
                _git(
                    recovery,
                    "ls-remote",
                    "origin",
                    "refs/tags/v44.20260721.1",
                ).stdout.split()[0],
                first_tag_object,
            )
            self.assertEqual(
                _git(
                    recovery,
                    "ls-remote",
                    "origin",
                    "refs/heads/master",
                ).stdout.split()[0],
                recovered_release,
            )

    def test_cli_skips_a_same_baseline_release_with_mismatched_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            remote = temporary / "remote.git"
            repository = temporary / "repository"
            subprocess.run(
                ["git", "init", "--bare", "-q", str(remote)],
                text=True,
                capture_output=True,
                check=True,
            )
            repository.mkdir()
            _git(repository, "init", "-q")
            pins = repository / "upstream" / "pins.json"
            state = repository / "upstream" / "state.json"
            upstream_release = temporary / "upstream-release.json"
            project_release_tags = temporary / "project-release-tags.txt"
            project_release_tags.write_text("", encoding="utf-8")
            _write_json(pins, {"project_version": "44.20260701.1"})
            _write_json(
                state,
                {
                    "heartbeat_month": "2026-07",
                    "last_checked_stable": "stable-44.20260701",
                    "last_successful_check": "2026-07-01T00:00:00Z",
                    "schema_version": 1,
                    "theme_fingerprint": "old-fingerprint",
                    "version": "44.20260701.1",
                },
            )
            baseline = _commit(repository, "baseline")
            _git(repository, "branch", "-M", "master")
            _git(repository, "remote", "add", "origin", str(remote))
            _git(repository, "push", "-q", "-u", "origin", "master")

            _write_json(
                pins,
                {
                    "project_version": "44.20260721.1",
                    "source_identity": "foreign-release",
                },
            )
            _write_json(
                state,
                {
                    "heartbeat_month": "2026-07",
                    "last_checked_stable": "stable-44.20260721",
                    "last_successful_check": "2026-07-21T00:00:00Z",
                    "schema_version": 1,
                    "theme_fingerprint": "foreign-fingerprint",
                    "version": "44.20260721.1",
                },
            )
            _commit(repository, "foreign release on the same baseline")
            _git(
                repository,
                "-c",
                "user.name=Another Publisher",
                "-c",
                "user.email=publisher@example.invalid",
                "tag",
                "-a",
                "v44.20260721.1",
                "-m",
                "foreign release",
            )
            occupied_tag = _git(
                repository, "rev-parse", "v44.20260721.1"
            ).stdout.strip()
            _git(repository, "push", "-q", "origin", "v44.20260721.1")
            _git(repository, "reset", "--hard", baseline)
            _write_json(
                upstream_release,
                {
                    "draft": False,
                    "prerelease": False,
                    "published_at": "2026-07-21T00:00:00Z",
                    "tag_name": "stable-44.20260721",
                },
            )

            selected = run_cli(
                "select-release-version",
                "--repository",
                str(repository),
                "--release-json",
                str(upstream_release),
                "--state",
                str(state),
                "--occupied-release-tags",
                str(project_release_tags),
            )

            self.assertEqual(selected.returncode, 0, selected.stderr)
            self.assertEqual(selected.stdout, "44.20260721.2\n")
            self.assertEqual(
                _git(repository, "rev-parse", "v44.20260721.1").stdout.strip(),
                occupied_tag,
            )
            self.assertEqual(
                _git(repository, "rev-parse", "HEAD").stdout.strip(), baseline
            )
            recovered = repository / "recovered.txt"
            recovered.write_text("release two\n", encoding="utf-8", newline="\n")
            recovered_release = _commit(repository, "unattended recovery release")
            _git(
                repository,
                "-c",
                "user.name=Vapor Tests",
                "-c",
                "user.email=vapor-tests@example.invalid",
                "tag",
                "-a",
                "v44.20260721.2",
                "-m",
                "unattended recovery release",
            )
            second_tag_object = _git(
                repository, "rev-parse", "v44.20260721.2"
            ).stdout.strip()
            publish = run_cli(
                "publish-release-tag",
                "--repository",
                str(repository),
                "--remote",
                "origin",
                "--tag",
                "v44.20260721.2",
            )
            self.assertEqual(publish.returncode, 0, publish.stderr)
            advance = run_cli(
                "advance-release-branch",
                "--repository",
                str(repository),
                "--remote",
                "origin",
                "--branch",
                "master",
                "--expected-branch-commit",
                baseline,
                "--tag",
                "v44.20260721.2",
                "--expected-tag-object",
                second_tag_object,
                "--release-commit",
                recovered_release,
            )
            self.assertEqual(advance.returncode, 0, advance.stderr)
            self.assertEqual(
                _git(
                    repository,
                    "ls-remote",
                    "origin",
                    "refs/tags/v44.20260721.1",
                ).stdout.split()[0],
                occupied_tag,
            )
            self.assertEqual(
                _git(
                    repository,
                    "ls-remote",
                    "origin",
                    "refs/heads/master",
                ).stdout.split()[0],
                recovered_release,
            )

    def test_cli_rejects_publication_after_the_default_branch_advances(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            remote = temporary / "remote.git"
            repository = temporary / "repository"
            subprocess.run(
                ["git", "init", "--bare", "-q", str(remote)],
                text=True,
                capture_output=True,
                check=True,
            )
            repository.mkdir()
            _git(repository, "init", "-q")
            tracked = repository / "tracked.txt"
            tracked.write_text("baseline\n", encoding="utf-8", newline="\n")
            baseline = _commit(repository, "baseline")
            _git(repository, "branch", "-M", "master")
            _git(repository, "remote", "add", "origin", str(remote))
            _git(repository, "push", "-q", "-u", "origin", "master")

            accepted = run_cli(
                "guard-release-base",
                "--repository",
                str(repository),
                "--remote",
                "origin",
                "--branch",
                "master",
                "--expected-commit",
                baseline,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)

            tracked.write_text("advanced\n", encoding="utf-8", newline="\n")
            _commit(repository, "advance default branch")
            _git(repository, "push", "-q", "origin", "master")

            rejected = run_cli(
                "guard-release-base",
                "--repository",
                str(repository),
                "--remote",
                "origin",
                "--branch",
                "master",
                "--expected-commit",
                baseline,
            )

            self.assertEqual(rejected.returncode, 1)
            self.assertIn("default branch advanced", rejected.stderr)

    def test_cli_skips_a_stranded_github_release_without_a_git_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            remote = temporary / "remote.git"
            repository = temporary / "repository"
            subprocess.run(
                ["git", "init", "--bare", "-q", str(remote)],
                text=True,
                capture_output=True,
                check=True,
            )
            repository.mkdir()
            _git(repository, "init", "-q")
            tracked = repository / "tracked.txt"
            tracked.write_text("baseline\n", encoding="utf-8", newline="\n")
            baseline = _commit(repository, "baseline")
            _git(repository, "branch", "-M", "master")
            _git(repository, "remote", "add", "origin", str(remote))
            _git(repository, "push", "-q", "-u", "origin", "master")

            upstream_release = temporary / "upstream-release.json"
            state = temporary / "state.json"
            project_release_tags = temporary / "project-release-tags.txt"
            _write_json(
                upstream_release,
                {
                    "draft": False,
                    "prerelease": False,
                    "published_at": "2026-07-21T00:00:00Z",
                    "tag_name": "stable-44.20260721",
                },
            )
            _write_json(
                state,
                {
                    "heartbeat_month": "2026-07",
                    "last_checked_stable": "stable-44.20260701",
                    "last_successful_check": "2026-07-01T00:00:00Z",
                    "schema_version": 1,
                    "theme_fingerprint": "old-fingerprint",
                    "version": "44.20260701.1",
                },
            )
            project_release_tags.write_text(
                "v44.20260721.1\n",
                encoding="utf-8",
                newline="\n",
            )
            stranded_release_snapshot = project_release_tags.read_bytes()

            selected = run_cli(
                "select-release-version",
                "--repository",
                str(repository),
                "--release-json",
                str(upstream_release),
                "--state",
                str(state),
                "--occupied-release-tags",
                str(project_release_tags),
            )
            self.assertEqual(selected.returncode, 0, selected.stderr)
            self.assertEqual(selected.stdout, "44.20260721.2\n")

            tracked.write_text("release two\n", encoding="utf-8", newline="\n")
            recovered_release = _commit(repository, "unattended recovery release")
            _git(
                repository,
                "-c",
                "user.name=Vapor Tests",
                "-c",
                "user.email=vapor-tests@example.invalid",
                "tag",
                "-a",
                "v44.20260721.2",
                "-m",
                "unattended recovery release",
            )
            second_tag_object = _git(
                repository, "rev-parse", "v44.20260721.2"
            ).stdout.strip()
            publish = run_cli(
                "publish-release-tag",
                "--repository",
                str(repository),
                "--remote",
                "origin",
                "--tag",
                "v44.20260721.2",
            )
            self.assertEqual(publish.returncode, 0, publish.stderr)
            advance = run_cli(
                "advance-release-branch",
                "--repository",
                str(repository),
                "--remote",
                "origin",
                "--branch",
                "master",
                "--expected-branch-commit",
                baseline,
                "--tag",
                "v44.20260721.2",
                "--expected-tag-object",
                second_tag_object,
                "--release-commit",
                recovered_release,
            )
            self.assertEqual(advance.returncode, 0, advance.stderr)
            self.assertEqual(
                project_release_tags.read_bytes(), stranded_release_snapshot
            )
            self.assertEqual(
                _git(
                    repository,
                    "ls-remote",
                    "origin",
                    "refs/tags/v44.20260721.1",
                ).stdout,
                "",
            )
            self.assertEqual(
                _git(
                    repository,
                    "ls-remote",
                    "origin",
                    "refs/heads/master",
                ).stdout.split()[0],
                recovered_release,
            )

    def test_cli_rolls_back_a_tag_when_the_final_branch_lease_loses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            remote = temporary / "remote.git"
            publisher = temporary / "publisher"
            competitor = temporary / "competitor"
            subprocess.run(
                ["git", "init", "--bare", "-q", str(remote)],
                text=True,
                capture_output=True,
                check=True,
            )
            publisher.mkdir()
            _git(publisher, "init", "-q")
            tracked = publisher / "tracked.txt"
            tracked.write_text("baseline\n", encoding="utf-8", newline="\n")
            baseline = _commit(publisher, "baseline")
            _git(publisher, "branch", "-M", "master")
            _git(publisher, "remote", "add", "origin", str(remote))
            _git(publisher, "push", "-q", "-u", "origin", "master")

            tracked.write_text("release\n", encoding="utf-8", newline="\n")
            release_commit = _commit(publisher, "release")
            _git(
                publisher,
                "-c",
                "user.name=Vapor Tests",
                "-c",
                "user.email=vapor-tests@example.invalid",
                "tag",
                "-a",
                "v44.20260721.1",
                "-m",
                "release",
            )
            tag_object = _git(publisher, "rev-parse", "v44.20260721.1").stdout.strip()
            _git(publisher, "push", "-q", "origin", "v44.20260721.1")

            subprocess.run(
                ["git", "clone", "-q", str(remote), str(competitor)],
                text=True,
                capture_output=True,
                check=True,
            )
            competing_file = competitor / "competing.txt"
            competing_file.write_text("keep\n", encoding="utf-8", newline="\n")
            competing_commit = _commit(competitor, "concurrent push")
            _git(competitor, "push", "-q", "origin", "master")

            final_push = run_cli(
                "advance-release-branch",
                "--repository",
                str(publisher),
                "--remote",
                "origin",
                "--branch",
                "master",
                "--expected-branch-commit",
                baseline,
                "--tag",
                "v44.20260721.1",
                "--expected-tag-object",
                tag_object,
                "--release-commit",
                release_commit,
            )
            self.assertNotEqual(final_push.returncode, 0)
            self.assertIn("stale info", final_push.stderr)

            rollback = run_cli(
                "rollback-release-tag",
                "--repository",
                str(publisher),
                "--remote",
                "origin",
                "--branch",
                "master",
                "--tag",
                "v44.20260721.1",
                "--expected-commit",
                release_commit,
                "--expected-tag-object",
                tag_object,
            )

            self.assertEqual(rollback.returncode, 0, rollback.stderr)
            self.assertEqual(
                _git(
                    publisher,
                    "ls-remote",
                    "origin",
                    "refs/tags/v44.20260721.1",
                ).stdout,
                "",
            )
            self.assertEqual(
                _git(
                    publisher,
                    "ls-remote",
                    "origin",
                    "refs/heads/master",
                ).stdout.split()[0],
                competing_commit,
            )

            competing_file.write_text("keep\nrelease\n", encoding="utf-8", newline="\n")
            replacement_release = _commit(competitor, "retry release")
            _git(
                competitor,
                "-c",
                "user.name=Vapor Tests",
                "-c",
                "user.email=vapor-tests@example.invalid",
                "tag",
                "-f",
                "-a",
                "v44.20260721.1",
                "-m",
                "retry release",
            )
            replacement_tag_object = _git(
                competitor, "rev-parse", "v44.20260721.1"
            ).stdout.strip()
            _git(competitor, "push", "-q", "origin", "v44.20260721.1")
            retry = run_cli(
                "advance-release-branch",
                "--repository",
                str(competitor),
                "--remote",
                "origin",
                "--branch",
                "master",
                "--expected-branch-commit",
                competing_commit,
                "--tag",
                "v44.20260721.1",
                "--expected-tag-object",
                replacement_tag_object,
                "--release-commit",
                replacement_release,
            )
            self.assertEqual(retry.returncode, 0, retry.stderr)
            descendant_file = competitor / "descendant.txt"
            descendant_file.write_text("preserve\n", encoding="utf-8", newline="\n")
            descendant_commit = _commit(competitor, "post-release descendant")
            _git(competitor, "push", "-q", "origin", "master")
            ambiguous_success = run_cli(
                "advance-release-branch",
                "--repository",
                str(competitor),
                "--remote",
                "origin",
                "--branch",
                "master",
                "--expected-branch-commit",
                competing_commit,
                "--tag",
                "v44.20260721.1",
                "--expected-tag-object",
                replacement_tag_object,
                "--release-commit",
                replacement_release,
            )
            self.assertEqual(ambiguous_success.returncode, 0, ambiguous_success.stderr)
            reconciled = run_cli(
                "verify-release-publication",
                "--repository",
                str(competitor),
                "--remote",
                "origin",
                "--branch",
                "master",
                "--tag",
                "v44.20260721.1",
                "--expected-tag-object",
                replacement_tag_object,
                "--release-commit",
                replacement_release,
            )
            self.assertEqual(reconciled.returncode, 0, reconciled.stderr)
            refused_cleanup = run_cli(
                "rollback-release-tag",
                "--repository",
                str(competitor),
                "--remote",
                "origin",
                "--branch",
                "master",
                "--tag",
                "v44.20260721.1",
                "--expected-commit",
                replacement_release,
                "--expected-tag-object",
                replacement_tag_object,
            )
            self.assertNotEqual(refused_cleanup.returncode, 0)
            self.assertIn("already contains the release", refused_cleanup.stderr)
            self.assertEqual(
                _git(
                    competitor,
                    "ls-remote",
                    "origin",
                    "refs/heads/master",
                ).stdout.split()[0],
                descendant_commit,
            )

    def test_cli_refuses_to_roll_back_a_replaced_annotated_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            remote = temporary / "remote.git"
            repository = temporary / "repository"
            subprocess.run(
                ["git", "init", "--bare", "-q", str(remote)],
                text=True,
                capture_output=True,
                check=True,
            )
            repository.mkdir()
            _git(repository, "init", "-q")
            tracked = repository / "tracked.txt"
            tracked.write_text("baseline\n", encoding="utf-8", newline="\n")
            _commit(repository, "baseline")
            _git(repository, "branch", "-M", "master")
            _git(repository, "remote", "add", "origin", str(remote))
            _git(repository, "push", "-q", "-u", "origin", "master")
            tracked.write_text("release\n", encoding="utf-8", newline="\n")
            release_commit = _commit(repository, "release")
            _git(
                repository,
                "-c",
                "user.name=Vapor Tests",
                "-c",
                "user.email=vapor-tests@example.invalid",
                "tag",
                "-a",
                "v44.20260721.1",
                "-m",
                "original publication",
            )
            original_tag_object = _git(
                repository, "rev-parse", "v44.20260721.1"
            ).stdout.strip()
            _git(repository, "push", "-q", "origin", "v44.20260721.1")

            _git(
                repository,
                "-c",
                "user.name=Another Publisher",
                "-c",
                "user.email=publisher@example.invalid",
                "tag",
                "-f",
                "-a",
                "v44.20260721.1",
                "-m",
                "replacement publication",
            )
            replacement_tag_object = _git(
                repository, "rev-parse", "v44.20260721.1"
            ).stdout.strip()
            self.assertNotEqual(replacement_tag_object, original_tag_object)
            _git(
                repository,
                "push",
                "-q",
                "--force",
                "origin",
                "v44.20260721.1",
            )

            rollback = run_cli(
                "rollback-release-tag",
                "--repository",
                str(repository),
                "--remote",
                "origin",
                "--branch",
                "master",
                "--tag",
                "v44.20260721.1",
                "--expected-commit",
                release_commit,
                "--expected-tag-object",
                original_tag_object,
            )

            self.assertEqual(rollback.returncode, 0, rollback.stderr)
            self.assertEqual(
                _git(
                    repository,
                    "ls-remote",
                    "origin",
                    "refs/tags/v44.20260721.1",
                ).stdout.split()[0],
                replacement_tag_object,
            )

    def test_cli_reports_whether_this_run_created_the_remote_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            remote = temporary / "remote.git"
            repository = temporary / "repository"
            subprocess.run(
                ["git", "init", "--bare", "-q", str(remote)],
                text=True,
                capture_output=True,
                check=True,
            )
            repository.mkdir()
            _git(repository, "init", "-q")
            tracked = repository / "tracked.txt"
            tracked.write_text("release\n", encoding="utf-8", newline="\n")
            _commit(repository, "release")
            _git(repository, "remote", "add", "origin", str(remote))
            _git(
                repository,
                "-c",
                "user.name=Vapor Tests",
                "-c",
                "user.email=vapor-tests@example.invalid",
                "tag",
                "-a",
                "v44.20260721.1",
                "-m",
                "release",
            )
            tag_object = _git(repository, "rev-parse", "v44.20260721.1").stdout.strip()

            created = run_cli(
                "publish-release-tag",
                "--repository",
                str(repository),
                "--remote",
                "origin",
                "--tag",
                "v44.20260721.1",
            )
            existing = run_cli(
                "publish-release-tag",
                "--repository",
                str(repository),
                "--remote",
                "origin",
                "--tag",
                "v44.20260721.1",
            )

            self.assertEqual(created.returncode, 0, created.stderr)
            self.assertEqual(
                created.stdout.splitlines(),
                [f"tag_object={tag_object}", "tag_created=true"],
            )
            self.assertEqual(existing.returncode, 0, existing.stderr)
            self.assertEqual(
                existing.stdout.splitlines(),
                [f"tag_object={tag_object}", "tag_created=false"],
            )


if __name__ == "__main__":
    unittest.main()
