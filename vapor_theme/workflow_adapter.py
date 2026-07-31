from __future__ import annotations

import re
import subprocess
from pathlib import Path

from vapor_theme.io_utils import read_json_object
from vapor_theme.records import (
    DottedVersion,
    parse_update_plan,
    parse_updater_state,
    parse_upstream_release,
)
from vapor_theme.updater import write_failure_plan


def _git(
    repository: Path,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as error:
        raise RuntimeError(f"could not run git: {error}") from error
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git failed"
        raise RuntimeError(detail)
    return result


def select_release_version(
    *,
    repository: Path,
    release_path: Path,
    state_path: Path,
    occupied_release_tags_path: Path,
) -> str:
    release = parse_upstream_release(
        read_json_object(release_path, label="upstream release JSON")
    )
    stable_version = DottedVersion.from_stable_tag(
        release["tag_name"],
        "upstream stable release tag",
    )
    state = parse_updater_state(
        read_json_object(state_path, label="updater state JSON")
    )
    current_version = DottedVersion.parse(
        state["version"],
        "current Vapor version",
    )
    occupied_release_tags = frozenset(
        occupied_release_tags_path.read_text(encoding="utf-8").splitlines()
    )
    candidate = stable_version.next_revision(current_version)
    while True:
        tag = f"v{candidate}"
        tag_ref = f"refs/tags/{tag}"
        present = _git(
            repository,
            "show-ref",
            "--verify",
            "--quiet",
            tag_ref,
            check=False,
        )
        if present.returncode == 1:
            if tag not in occupied_release_tags:
                return str(candidate)
            candidate = stable_version.next_revision(candidate)
            continue
        if present.returncode != 0:
            detail = present.stderr.strip() or "could not inspect release tag"
            raise RuntimeError(detail)
        candidate = stable_version.next_revision(candidate)


def guard_release_base(
    *,
    repository: Path,
    remote: str,
    branch: str,
    expected_commit: str,
) -> None:
    if re.fullmatch(r"[0-9a-f]{40}", expected_commit) is None:
        raise ValueError("expected default-branch commit must be a full SHA-1")
    _git(repository, "check-ref-format", "--branch", branch)
    branch_ref = f"refs/heads/{branch}"
    result = _git(
        repository,
        "ls-remote",
        "--exit-code",
        remote,
        branch_ref,
    )
    fields = result.stdout.split()
    if len(fields) != 2 or fields[1] != branch_ref:
        raise RuntimeError("remote default branch returned an invalid revision")
    actual_commit = fields[0]
    if actual_commit != expected_commit:
        raise RuntimeError(
            "default branch advanced during release validation "
            f"({expected_commit} -> {actual_commit}); aborting before publication"
        )


def rollback_release_tag(
    *,
    repository: Path,
    remote: str,
    branch: str,
    tag: str,
    expected_commit: str,
    expected_tag_object: str,
) -> None:
    if re.fullmatch(r"[0-9a-f]{40}", expected_commit) is None:
        raise ValueError("expected release commit must be a full SHA-1")
    if re.fullmatch(r"[0-9a-f]{40}", expected_tag_object) is None:
        raise ValueError("expected release tag object must be a full SHA-1")
    _git(repository, "check-ref-format", "--branch", branch)
    branch_ref = f"refs/heads/{branch}"
    branch_tip = _fetch_stable_remote_branch_tip(
        repository=repository,
        remote=remote,
        branch_ref=branch_ref,
    )
    if _is_ancestor(
        repository=repository,
        ancestor=expected_commit,
        descendant=branch_tip,
    ):
        raise RuntimeError(
            "remote default branch already contains the release; refusing rollback"
        )
    tag_ref = f"refs/tags/{tag}"
    _git(repository, "check-ref-format", tag_ref)
    tag_object, actual_commit = _remote_release_tag_state(
        repository=repository,
        remote=remote,
        tag_ref=tag_ref,
    )
    if tag_object is None:
        return
    if tag_object != expected_tag_object:
        return
    if actual_commit != expected_commit:
        return
    push = _git(
        repository,
        "push",
        "--atomic",
        f"--force-with-lease={branch_ref}:{branch_tip}",
        f"--force-with-lease={tag_ref}:{expected_tag_object}",
        remote,
        f"{branch_tip}:{branch_ref}",
        f":{tag_ref}",
        check=False,
    )
    if push.returncode == 0:
        return
    current_tip = _fetch_stable_remote_branch_tip(
        repository=repository,
        remote=remote,
        branch_ref=branch_ref,
    )
    if _is_ancestor(
        repository=repository,
        ancestor=expected_commit,
        descendant=current_tip,
    ):
        raise RuntimeError(
            "remote default branch already contains the release; refusing rollback"
        )
    current_tag_object, _ = _remote_release_tag_state(
        repository=repository,
        remote=remote,
        tag_ref=tag_ref,
    )
    if current_tag_object is None or current_tag_object != expected_tag_object:
        return
    detail = push.stderr.strip() or push.stdout.strip() or "git push failed"
    raise RuntimeError(detail)


def _remote_release_tag_state(
    *,
    repository: Path,
    remote: str,
    tag_ref: str,
) -> tuple[str | None, str | None]:
    peeled_ref = f"{tag_ref}^{{}}"
    result = _git(repository, "ls-remote", remote, tag_ref, peeled_ref)
    if not result.stdout.strip():
        return None, None
    refs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2 or fields[1] not in (tag_ref, peeled_ref):
            raise RuntimeError("remote release tag returned an invalid revision")
        refs[fields[1]] = fields[0]
    tag_object = refs.get(tag_ref)
    if tag_object is None:
        raise RuntimeError("remote release tag did not identify its tag object")
    return tag_object, refs.get(peeled_ref, tag_object)


def publish_release_tag(
    *,
    repository: Path,
    remote: str,
    tag: str,
) -> tuple[str, bool]:
    tag_ref = f"refs/tags/{tag}"
    _git(repository, "check-ref-format", tag_ref)
    tag_object = _git(
        repository,
        "rev-parse",
        "--verify",
        tag_ref,
    ).stdout.strip()
    result = _git(
        repository,
        "push",
        "--porcelain",
        remote,
        f"{tag_ref}:{tag_ref}",
    )
    updates = [
        line.split("\t")
        for line in result.stdout.splitlines()
        if line.startswith(("*\t", "=\t"))
    ]
    if len(updates) != 1 or len(updates[0]) != 3:
        raise RuntimeError("git did not report release-tag ownership")
    flag, ref_update, summary = updates[0]
    if ref_update != f"{tag_ref}:{tag_ref}":
        raise RuntimeError("git reported an unexpected release-tag update")
    if flag == "*" and summary == "[new tag]":
        created = True
    elif flag == "=" and summary == "[up to date]":
        created = False
    else:
        raise RuntimeError("git reported an unexpected release-tag result")
    return tag_object, created


def advance_release_branch(
    *,
    repository: Path,
    remote: str,
    branch: str,
    expected_branch_commit: str,
    tag: str,
    expected_tag_object: str,
    release_commit: str,
) -> None:
    for value, label in (
        (expected_branch_commit, "expected default-branch commit"),
        (expected_tag_object, "expected release tag object"),
        (release_commit, "release commit"),
    ):
        if re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise ValueError(f"{label} must be a full SHA-1")
    _git(repository, "check-ref-format", "--branch", branch)
    branch_ref = f"refs/heads/{branch}"
    tag_ref = f"refs/tags/{tag}"
    _git(repository, "check-ref-format", tag_ref)
    local_tag_object = _git(repository, "rev-parse", "--verify", tag_ref).stdout.strip()
    if local_tag_object != expected_tag_object:
        raise ValueError("local release tag object does not match publication")
    tagged_commit = _git(
        repository, "rev-parse", "--verify", f"{tag_ref}^{{commit}}"
    ).stdout.strip()
    if tagged_commit != release_commit:
        raise ValueError("release tag does not point to the release commit")
    resolved_release_commit = _git(
        repository, "rev-parse", "--verify", f"{release_commit}^{{commit}}"
    ).stdout.strip()
    if resolved_release_commit != release_commit:
        raise ValueError("release commit did not resolve exactly")
    push = _git(
        repository,
        "push",
        "--atomic",
        f"--force-with-lease={branch_ref}:{expected_branch_commit}",
        f"--force-with-lease={tag_ref}:{expected_tag_object}",
        remote,
        f"{release_commit}:{branch_ref}",
        f"{tag_ref}:{tag_ref}",
        check=False,
    )
    if push.returncode == 0:
        return
    try:
        verify_release_publication(
            repository=repository,
            remote=remote,
            branch=branch,
            tag=tag,
            expected_tag_object=expected_tag_object,
            release_commit=release_commit,
        )
    except (RuntimeError, ValueError) as reconciliation_error:
        detail = push.stderr.strip() or push.stdout.strip() or "git push failed"
        raise RuntimeError(
            f"{detail}; publication could not be reconciled: {reconciliation_error}"
        ) from reconciliation_error


def verify_release_publication(
    *,
    repository: Path,
    remote: str,
    branch: str,
    tag: str,
    expected_tag_object: str,
    release_commit: str,
) -> None:
    for value, label in (
        (expected_tag_object, "expected release tag object"),
        (release_commit, "release commit"),
    ):
        if re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise ValueError(f"{label} must be a full SHA-1")
    _git(repository, "check-ref-format", "--branch", branch)
    branch_ref = f"refs/heads/{branch}"
    tag_ref = f"refs/tags/{tag}"
    peeled_ref = f"{tag_ref}^{{}}"
    _git(repository, "check-ref-format", tag_ref)
    branch_tip = _fetch_stable_remote_branch_tip(
        repository=repository,
        remote=remote,
        branch_ref=branch_ref,
    )
    if not _is_ancestor(
        repository=repository,
        ancestor=release_commit,
        descendant=branch_tip,
    ):
        raise RuntimeError("remote default branch does not contain the release")
    result = _git(
        repository,
        "ls-remote",
        "--exit-code",
        remote,
        tag_ref,
        peeled_ref,
    )
    refs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2 or fields[1] not in (tag_ref, peeled_ref):
            raise RuntimeError("remote publication returned an invalid revision")
        refs[fields[1]] = fields[0]
    if refs.get(tag_ref) != expected_tag_object:
        raise RuntimeError("remote release tag object does not match the publication")
    if refs.get(peeled_ref) != release_commit:
        raise RuntimeError("remote release tag does not point to the release commit")
    if (
        _remote_ref_value(
            repository=repository,
            remote=remote,
            ref=branch_ref,
        )
        != branch_tip
    ):
        raise RuntimeError(
            "remote default branch changed during publication verification"
        )


def _remote_ref_value(
    *,
    repository: Path,
    remote: str,
    ref: str,
) -> str:
    result = _git(
        repository,
        "ls-remote",
        "--exit-code",
        remote,
        ref,
    )
    fields = result.stdout.split()
    if len(fields) != 2 or fields[1] != ref:
        raise RuntimeError(f"remote ref returned an invalid revision: {ref}")
    return fields[0]


def _fetch_stable_remote_branch_tip(
    *,
    repository: Path,
    remote: str,
    branch_ref: str,
) -> str:
    _git(repository, "fetch", "--quiet", "--no-tags", remote, branch_ref)
    fetched_tip = _git(repository, "rev-parse", "--verify", "FETCH_HEAD").stdout.strip()
    if (
        _remote_ref_value(
            repository=repository,
            remote=remote,
            ref=branch_ref,
        )
        != fetched_tip
    ):
        raise RuntimeError("remote default branch changed during verification")
    return fetched_tip


def _is_ancestor(
    *,
    repository: Path,
    ancestor: str,
    descendant: str,
) -> bool:
    ancestry = _git(
        repository,
        "merge-base",
        "--is-ancestor",
        ancestor,
        descendant,
        check=False,
    )
    if ancestry.returncode == 0:
        return True
    if ancestry.returncode == 1:
        return False
    detail = ancestry.stderr.strip() or "could not verify release ancestry"
    raise RuntimeError(detail)


def upstream_release_tag(release_path: Path) -> str:
    release = parse_upstream_release(
        read_json_object(release_path, label="upstream release JSON")
    )
    return release["tag_name"]


def decision_output_lines(
    plan_path: Path,
    updater_status: int,
) -> tuple[str, ...]:
    try:
        plan = parse_update_plan(
            read_json_object(plan_path, label="updater decision plan")
        )
    except (FileNotFoundError, ValueError):
        error = (
            "updater exited without a decision plan"
            if not plan_path.is_file()
            else "updater produced an invalid decision plan"
        )
        write_failure_plan(plan_path, RuntimeError(error))
        plan = parse_update_plan(
            read_json_object(plan_path, label="updater decision plan")
        )
        updater_status = 1
    lines = [
        f"status={updater_status}",
        f"action={plan['action']}",
    ]
    if plan["action"] == "release":
        lines.append(f"tag={plan['tag']}")
    return tuple(lines)
