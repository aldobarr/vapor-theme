from __future__ import annotations

import argparse
import sys
from pathlib import Path

from vapor_theme.compiler import build_release
from vapor_theme.github_incident import update_incident
from vapor_theme.release_assets import (
    plan_existing_release_upload,
    prepare_release_assets,
    release_owner_marker,
)
from vapor_theme.source_contract import steam_preset_tag
from vapor_theme.sources import fetch_sources
from vapor_theme.updater import run_update, write_failure_plan
from vapor_theme.validator import validate_bundle
from vapor_theme.workflow_adapter import (
    advance_release_branch,
    decision_output_lines,
    guard_release_base,
    publish_release_tag,
    rollback_release_tag,
    select_release_version,
    upstream_release_tag,
    verify_release_publication,
)


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vapor-theme")
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="build a portable Vapor release")
    build.add_argument("--steam-source", type=Path, required=True)
    build.add_argument("--bazzite-source", type=Path, required=True)
    build.add_argument("--pins", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)

    validate = commands.add_parser("validate", help="validate a Vapor bundle")
    validate.add_argument("--bundle", type=Path, required=True)

    fetch = commands.add_parser(
        "fetch-sources",
        help="materialize immutable source snapshots from pins",
    )
    fetch.add_argument("--pins", type=Path, required=True)
    fetch.add_argument("--output", type=Path, required=True)

    resolve_steam = commands.add_parser(
        "steam-preset-tag",
        help="read the pinned Steam preset tag from Bazzite's desktop recipe",
    )
    resolve_steam.add_argument("--bazzite-source", type=Path, required=True)

    release_tag = commands.add_parser(
        "upstream-release-tag",
        help="validate a Bazzite release snapshot and print its tag",
    )
    release_tag.add_argument("--release-json", type=Path, required=True)

    decision = commands.add_parser(
        "decision-outputs",
        help="validate an updater plan and emit GitHub output fields",
    )
    decision.add_argument("--plan", type=Path, required=True)
    decision.add_argument("--updater-status", type=int, required=True)

    select_version = commands.add_parser(
        "select-release-version",
        help="select the next release revision not occupied by an existing tag",
    )
    select_version.add_argument("--repository", type=Path, required=True)
    select_version.add_argument("--release-json", type=Path, required=True)
    select_version.add_argument("--state", type=Path, required=True)
    select_version.add_argument(
        "--occupied-release-tags",
        type=Path,
        required=True,
    )

    guard = commands.add_parser(
        "guard-release-base",
        help="abort publication if the remote default branch advanced",
    )
    guard.add_argument("--repository", type=Path, required=True)
    guard.add_argument("--remote", required=True)
    guard.add_argument("--branch", required=True)
    guard.add_argument("--expected-commit", required=True)

    rollback = commands.add_parser(
        "rollback-release-tag",
        help="remove only the failed publication's matching remote tag",
    )
    rollback.add_argument("--repository", type=Path, required=True)
    rollback.add_argument("--remote", required=True)
    rollback.add_argument("--branch", required=True)
    rollback.add_argument("--tag", required=True)
    rollback.add_argument("--expected-commit", required=True)
    rollback.add_argument("--expected-tag-object", required=True)

    publish_tag = commands.add_parser(
        "publish-release-tag",
        help="push a release tag and report whether this run created it",
    )
    publish_tag.add_argument("--repository", type=Path, required=True)
    publish_tag.add_argument("--remote", required=True)
    publish_tag.add_argument("--tag", required=True)

    owner_marker = commands.add_parser(
        "release-owner-marker",
        help="emit the ownership marker for an automation-created release",
    )
    owner_marker.add_argument("--tag-object", required=True)

    upload_plan = commands.add_parser(
        "plan-release-upload",
        help="verify an existing release and list only its missing owned assets",
    )
    upload_plan.add_argument("--release-json", type=Path, required=True)
    upload_plan.add_argument("--tag", required=True)
    upload_plan.add_argument("--tag-object", required=True)
    upload_plan.add_argument("--asset", type=Path, action="append", required=True)

    advance_branch = commands.add_parser(
        "advance-release-branch",
        help="atomically advance a release branch while leasing its published tag",
    )
    advance_branch.add_argument("--repository", type=Path, required=True)
    advance_branch.add_argument("--remote", required=True)
    advance_branch.add_argument("--branch", required=True)
    advance_branch.add_argument("--expected-branch-commit", required=True)
    advance_branch.add_argument("--tag", required=True)
    advance_branch.add_argument("--expected-tag-object", required=True)
    advance_branch.add_argument("--release-commit", required=True)

    verify_publication = commands.add_parser(
        "verify-release-publication",
        help="reconcile an ambiguous push with the exact remote release refs",
    )
    verify_publication.add_argument("--repository", type=Path, required=True)
    verify_publication.add_argument("--remote", required=True)
    verify_publication.add_argument("--branch", required=True)
    verify_publication.add_argument("--tag", required=True)
    verify_publication.add_argument("--expected-tag-object", required=True)
    verify_publication.add_argument("--release-commit", required=True)

    release = commands.add_parser(
        "prepare-release",
        help="prepare checksummed, versioned release assets",
    )
    release.add_argument("--bundle", type=Path, required=True)
    release.add_argument("--plan", type=Path, required=True)
    release.add_argument("--output", type=Path, required=True)

    incident = commands.add_parser(
        "github-incident",
        help="open, update, or close the deduplicated updater incident",
    )
    incident.add_argument("--plan", type=Path, required=True)
    incident.add_argument("--repository", required=True)
    incident.add_argument("--run-url", required=True)

    update = commands.add_parser("update", help="evaluate a Bazzite release")
    update.add_argument("--release-json", type=Path, required=True)
    update.add_argument("--bazzite-source", type=Path, required=True)
    update.add_argument("--steam-source", type=Path, required=True)
    update.add_argument("--bazzite-commit", required=True)
    update.add_argument("--steam-commit", required=True)
    update.add_argument("--pins", type=Path, required=True)
    update.add_argument("--state", type=Path, required=True)
    update.add_argument("--artifact", type=Path, required=True)
    update.add_argument("--plan", type=Path, required=True)
    update.add_argument("--checked-at", required=True)
    update.add_argument("--candidate-version")

    return parser


def _run(arguments: argparse.Namespace) -> int:
    if arguments.command == "build":
        build_release(
            steam_source=arguments.steam_source,
            bazzite_source=arguments.bazzite_source,
            pins_path=arguments.pins,
            output_path=arguments.output,
        )
        return 0
    if arguments.command == "validate":
        version = validate_bundle(arguments.bundle)
        print(f"Valid Vapor bundle {version}")
        return 0
    if arguments.command == "fetch-sources":
        fetch_sources(
            pins_path=arguments.pins,
            output=arguments.output,
        )
        return 0
    if arguments.command == "steam-preset-tag":
        print(steam_preset_tag(arguments.bazzite_source))
        return 0
    if arguments.command == "upstream-release-tag":
        print(upstream_release_tag(arguments.release_json))
        return 0
    if arguments.command == "decision-outputs":
        for line in decision_output_lines(
            arguments.plan,
            arguments.updater_status,
        ):
            print(line)
        return 0
    if arguments.command == "select-release-version":
        print(
            select_release_version(
                repository=arguments.repository,
                release_path=arguments.release_json,
                state_path=arguments.state,
                occupied_release_tags_path=arguments.occupied_release_tags,
            )
        )
        return 0
    if arguments.command == "rollback-release-tag":
        rollback_release_tag(
            repository=arguments.repository,
            remote=arguments.remote,
            branch=arguments.branch,
            tag=arguments.tag,
            expected_commit=arguments.expected_commit,
            expected_tag_object=arguments.expected_tag_object,
        )
        return 0
    if arguments.command == "publish-release-tag":
        tag_object, created = publish_release_tag(
            repository=arguments.repository,
            remote=arguments.remote,
            tag=arguments.tag,
        )
        print(f"tag_object={tag_object}")
        print(f"tag_created={str(created).lower()}")
        return 0
    if arguments.command == "release-owner-marker":
        print(release_owner_marker(arguments.tag_object))
        return 0
    if arguments.command == "plan-release-upload":
        release_id, missing = plan_existing_release_upload(
            release_path=arguments.release_json,
            tag=arguments.tag,
            tag_object=arguments.tag_object,
            assets=arguments.asset,
        )
        print(f"release_id={release_id}")
        for asset in missing:
            print(f"asset={asset}")
        return 0
    if arguments.command == "advance-release-branch":
        advance_release_branch(
            repository=arguments.repository,
            remote=arguments.remote,
            branch=arguments.branch,
            expected_branch_commit=arguments.expected_branch_commit,
            tag=arguments.tag,
            expected_tag_object=arguments.expected_tag_object,
            release_commit=arguments.release_commit,
        )
        return 0
    if arguments.command == "verify-release-publication":
        verify_release_publication(
            repository=arguments.repository,
            remote=arguments.remote,
            branch=arguments.branch,
            tag=arguments.tag,
            expected_tag_object=arguments.expected_tag_object,
            release_commit=arguments.release_commit,
        )
        return 0
    if arguments.command == "guard-release-base":
        guard_release_base(
            repository=arguments.repository,
            remote=arguments.remote,
            branch=arguments.branch,
            expected_commit=arguments.expected_commit,
        )
        return 0
    if arguments.command == "prepare-release":
        prepare_release_assets(
            bundle=arguments.bundle,
            plan_path=arguments.plan,
            output=arguments.output,
        )
        return 0
    if arguments.command == "github-incident":
        update_incident(
            plan_path=arguments.plan,
            repository=arguments.repository,
            run_url=arguments.run_url,
        )
        return 0
    if arguments.command == "update":
        try:
            run_update(
                release_path=arguments.release_json,
                bazzite_source=arguments.bazzite_source,
                steam_source=arguments.steam_source,
                bazzite_commit=arguments.bazzite_commit,
                steam_commit=arguments.steam_commit,
                pins_path=arguments.pins,
                state_path=arguments.state,
                artifact_path=arguments.artifact,
                plan_path=arguments.plan,
                checked_at=arguments.checked_at,
                candidate_version=arguments.candidate_version,
            )
        except (FileNotFoundError, RuntimeError, ValueError) as error:
            write_failure_plan(arguments.plan, error)
            raise
        return 0
    raise AssertionError(f"unhandled command: {arguments.command}")


def main() -> int:
    arguments = create_parser().parse_args()
    try:
        return _run(arguments)
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
