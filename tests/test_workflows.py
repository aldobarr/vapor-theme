import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _read_workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _assert_actions_are_immutable(
    test_case: unittest.TestCase,
    workflow: str,
) -> None:
    references = re.findall(r"(?m)^\s*uses:\s+([^@\s]+)@([^\s#]+)", workflow)
    test_case.assertTrue(references)
    for action, revision in references:
        test_case.assertRegex(
            revision,
            r"\A[0-9a-f]{40}\Z",
            f"{action} must be pinned to a full commit SHA",
        )


class WorkflowContractTests(unittest.TestCase):
    def test_ci_uses_free_runners_and_real_fedora_plasma(self) -> None:
        workflow = _read_workflow("test.yml")
        dependencies = (
            REPO_ROOT
            / "tests"
            / "integration"
            / "install_fedora_plasma_dependencies.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("push:", workflow)
        self.assertIn("pull_request:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("contents: read", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertIn("runs-on: ubuntu-latest", workflow)
        self.assertIn("container: fedora:44", workflow)
        self.assertIn(
            "bash tests/integration/install_fedora_plasma_dependencies.sh",
            workflow,
        )
        for package in (
            "cmake",
            "extra-cmake-modules",
            "gcc-c++",
            "kf6-kiconthemes",
            "kf6-kcoreaddons-devel",
            "kf6-kpackage-devel",
            "kwin-wayland",
            "libcap",
            "python3-pyqt6",
            "kactivitymanagerd",
            "qt6-qttools",
        ):
            self.assertIn(package, dependencies)
        self.assertNotIn("xrandr", dependencies)
        self.assertIn("python -m unittest discover -v", workflow)
        self.assertIn("python -m compileall", workflow)
        self.assertIn("python -m mypy", workflow)
        self.assertIn("python -m ruff check", workflow)
        self.assertIn("python -m ruff format --check", workflow)
        self.assertIn("actionlint", workflow)
        self.assertIn(
            "actionlint/cmd/actionlint@914e7df21a07ef503a81201c76d2b11c789d3fca",
            workflow,
        )
        self.assertIn("shellcheck", workflow)
        self.assertIn(
            "python -m tests.integration.plasma_runtime_check",
            workflow,
        )
        _assert_actions_are_immutable(self, workflow)

    def test_updater_is_set_and_forget_without_a_pull_request(self) -> None:
        workflow = _read_workflow("update.yml")

        self.assertIn("schedule:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        for release_recipe_path in (
            "tests/integration/**",
            "vapor_theme/bundle_contract.py",
            "vapor_theme/compiler.py",
            "vapor_theme/source_contract.py",
        ):
            self.assertIn(f'- "{release_recipe_path}"', workflow)
        self.assertIn("group: vapor-upstream-update", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        for permission in (
            "contents: write",
            "issues: write",
            "id-token: write",
            "attestations: write",
        ):
            self.assertIn(permission, workflow)
        self.assertNotIn("pull_request:", workflow)
        self.assertNotIn(" gh pr ", workflow)
        self.assertIn(
            "repos/ublue-os/bazzite/releases/latest",
            workflow,
        )
        self.assertIn("python -m vapor_theme upstream-release-tag", workflow)
        self.assertIn("python -m vapor_theme steam-preset-tag", workflow)
        self.assertIn("python -m vapor_theme select-release-version", workflow)
        self.assertIn(
            'gh api --paginate "repos/${{ github.repository }}/releases"', workflow
        )
        self.assertIn('--occupied-release-tags "$project_release_tags"', workflow)
        self.assertIn("python -m vapor_theme update", workflow)
        self.assertIn('--candidate-version "$candidate_version"', workflow)
        self.assertIn("python -m vapor_theme validate", workflow)
        self.assertIn("python -m vapor_theme decision-outputs", workflow)
        self.assertIn("action == 'state-only'", workflow)
        self.assertIn("upstream/state.json", workflow)
        self.assertIn("SHA256SUMS", workflow)
        self.assertIn(".provenance.json", workflow)
        self.assertIn("actions/attest@", workflow)
        self.assertIn("subject-path:", workflow)
        self.assertNotIn("gh release upload", workflow)
        self.assertNotIn("--clobber", workflow)
        self.assertIn(
            'gh api "repos/${{ github.repository }}/releases/tags/${tag}"', workflow
        )
        self.assertIn("git push origin", workflow)
        self.assertIn(
            "DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}",
            workflow,
        )
        self.assertIn(
            "ref: ${{ github.event.repository.default_branch }}",
            workflow,
        )
        self.assertEqual(workflow.count("refs/heads/${DEFAULT_BRANCH}"), 1)
        self.assertNotIn("GITHUB_REF_NAME", workflow)
        self.assertIn("write_failure_plan", workflow)
        self.assertNotIn("%define\\s+packagever", workflow)
        self.assertNotIn("python -m vapor_theme resume-release", workflow)
        self.assertIn("python -m vapor_theme guard-release-base", workflow)
        self.assertIn("python -m vapor_theme rollback-release-tag", workflow)
        self.assertIn("python -m vapor_theme publish-release-tag", workflow)
        self.assertIn("python -m vapor_theme advance-release-branch", workflow)
        self.assertIn("python -m vapor_theme release-owner-marker", workflow)
        self.assertIn("python -m vapor_theme plan-release-upload", workflow)
        self.assertIn("id: publication", workflow)
        publication_step = workflow.split("id: publication", 1)[1].split(
            "- name: Advance the default branch", 1
        )[0]
        self.assertNotIn("steps.publication.outputs", publication_step)
        self.assertIn('echo "release_id=$release_id" >> "$GITHUB_OUTPUT"', workflow)
        self.assertIn('echo "release_created=true" >> "$GITHUB_OUTPUT"', workflow)
        self.assertIn('echo "release_created=false" >> "$GITHUB_OUTPUT"', workflow)
        self.assertIn("id: baseline", workflow)
        self.assertIn(
            'echo "commit=$(git rev-parse HEAD)" >> "$GITHUB_OUTPUT"',
            workflow,
        )
        self.assertIn(
            '--expected-commit "${{ steps.baseline.outputs.commit }}"',
            workflow,
        )
        self.assertNotIn("--expected-base", workflow)
        self.assertIn(
            '--expected-branch-commit "${{ steps.baseline.outputs.commit }}"', workflow
        )
        self.assertIn(
            '--expected-tag-object "${{ steps.publication.outputs.tag_object }}"',
            workflow,
        )
        self.assertIn("python -m vapor_theme github-incident", workflow)
        self.assertNotIn("gh release delete", workflow)
        self.assertIn(
            'gh api --method DELETE "repos/${{ github.repository }}/releases/${release_id}"',
            workflow,
        )
        self.assertIn(
            'gh api --method POST "repos/${{ github.repository }}/releases"', workflow
        )
        self.assertIn(
            '"https://uploads.github.com/repos/${{ github.repository }}/releases/${release_id}/assets?name=${asset_name}"',
            workflow,
        )
        self.assertIn("while IFS= read -r asset; do", workflow)
        self.assertIn('[[ "$asset" == asset=* ]] || continue', workflow)
        self.assertNotIn(
            'release_id="$(gh release view "$tag" --json databaseId',
            workflow,
        )
        self.assertIn(
            '--expected-tag-object "${{ steps.publication.outputs.tag_object }}"',
            workflow,
        )
        self.assertIn(
            "steps.publication.outputs.tag_created == 'true'",
            workflow,
        )
        self.assertIn(
            "steps.publication.outputs.release_created == 'true'",
            workflow,
        )
        self.assertNotIn("python -m vapor_theme verify-release-state", workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertNotIn('git rev-parse --verify --quiet "${tag}^{commit}"', workflow)
        self.assertNotIn(
            'assert state["version"] == plan["version"]',
            workflow,
        )
        self.assertEqual(
            workflow.count('git config user.name "github-actions[bot]"'), 1
        )
        self.assertEqual(
            workflow.count(
                "git config user.email "
                '"41898282+github-actions[bot]@users.noreply.github.com"'
            ),
            1,
        )
        self.assertIn("docker run --rm", workflow)
        self.assertIn("fedora:44", workflow)
        self.assertIn(
            "python -m tests.integration.plasma_runtime_check dist/Vapor.tar.gz",
            workflow,
        )
        self.assertIn(
            "bash tests/integration/install_fedora_plasma_dependencies.sh",
            workflow,
        )

        validate_at = workflow.index("python -m vapor_theme validate")
        plasma_runtime_at = workflow.index(
            "python -m tests.integration.plasma_runtime_check dist/Vapor.tar.gz"
        )
        commit_at = workflow.index("git commit")
        tag_at = workflow.index("git tag")
        guard_at = workflow.index("python -m vapor_theme guard-release-base")
        remote_tag_at = workflow.index("python -m vapor_theme publish-release-tag")
        release_at = workflow.index("gh api --method POST")
        branch_push_at = workflow.index("python -m vapor_theme advance-release-branch")
        rollback_at = workflow.index("python -m vapor_theme rollback-release-tag")
        release_delete_at = workflow.index("gh api --method DELETE")
        failure_plan_at = workflow.index("write_failure_plan")
        self.assertLess(validate_at, plasma_runtime_at)
        self.assertLess(plasma_runtime_at, commit_at)
        self.assertLess(commit_at, tag_at)
        self.assertLess(tag_at, guard_at)
        self.assertLess(guard_at, remote_tag_at)
        self.assertLess(remote_tag_at, release_at)
        self.assertLess(tag_at, release_at)
        self.assertLess(release_at, branch_push_at)
        self.assertLess(branch_push_at, rollback_at)
        self.assertLess(rollback_at, release_delete_at)
        self.assertLess(release_delete_at, failure_plan_at)
        rollback_step = workflow.split(
            "- name: Roll back a publication that lost its branch lease", 1
        )[1].split("- name: Record a workflow-adapter failure", 1)[0]
        self.assertIn("rollback_status=0", rollback_step)
        self.assertIn("release_cleanup_safe=false", rollback_step)
        self.assertIn("if python -m vapor_theme rollback-release-tag", rollback_step)
        self.assertIn("release_cleanup_safe=true", rollback_step)
        self.assertIn(
            '"${{ steps.publication.outputs.release_created }}" == "true" '
            '&& "$release_cleanup_safe" == "true"',
            rollback_step,
        )
        self.assertIn("if ! gh api --method DELETE", rollback_step)
        self.assertIn('exit "$rollback_status"', rollback_step)
        source_resolution = workflow.split(
            "- name: Resolve the latest stable source snapshots",
            1,
        )[1].split("- name: Evaluate updater state machine", 1)[0]
        self.assertEqual(source_resolution.count('>> "$GITHUB_OUTPUT"'), 1)
        self.assertIn('} >> "$GITHUB_OUTPUT"', source_resolution)
        _assert_actions_are_immutable(self, workflow)

    def test_updater_bootstraps_on_the_default_branch(self) -> None:
        workflow = _read_workflow("update.yml")

        self.assertRegex(workflow, r"(?m)^  push:\s*$")
        self.assertIn('      - ".github/workflows/update.yml"', workflow)
        self.assertIn('      - "upstream/pins.json"', workflow)
        self.assertIn('      - "upstream/state.json"', workflow)
        self.assertIn(
            "if: github.event_name != 'push' || "
            "github.ref_name == github.event.repository.default_branch",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
