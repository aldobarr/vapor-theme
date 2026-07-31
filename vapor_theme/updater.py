from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from vapor_theme.compiler import build_release
from vapor_theme.io_utils import (
    read_json_object,
    sha256_file,
    temporary_file,
    vacant_temporary_path,
    write_bytes_atomic,
    write_json_atomic,
)
from vapor_theme.records import (
    DottedVersion,
    IgnoredPlan,
    SourcePins,
    UpdatePlan,
    UpdaterState,
    parse_source_pins,
    parse_updater_state,
    parse_upstream_release,
)
from vapor_theme.source_contract import (
    collect_source_inputs,
    steam_preset_tag,
    theme_fingerprint,
)
from vapor_theme.validator import validate_bundle


def _parse_timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(UTC)


def _checked_month(checked_at: str) -> str:
    parsed = _parse_timestamp(checked_at, "checked-at")
    return f"{parsed.year:04d}-{parsed.month:02d}"


def write_failure_plan(plan_path: Path, error: BaseException) -> None:
    write_json_atomic(
        plan_path,
        {
            "action": "failure",
            "error": str(error),
            "incident": {
                "action": "open-or-update",
                "key": "vapor-updater",
                "title": "[automation] Vapor updater failure",
            },
            "schema_version": 1,
        },
    )


def _ignored_plan(
    plan_path: Path,
    reason: Literal["draft", "prerelease", "older-than-current"],
) -> IgnoredPlan:
    plan: IgnoredPlan = {
        "action": "ignored",
        "incident": "close",
        "reason": reason,
        "schema_version": 1,
    }
    write_json_atomic(plan_path, plan)
    return plan


def run_update(
    *,
    release_path: Path,
    bazzite_source: Path,
    steam_source: Path,
    bazzite_commit: str,
    steam_commit: str,
    pins_path: Path,
    state_path: Path,
    artifact_path: Path,
    plan_path: Path,
    checked_at: str,
    candidate_version: str | None = None,
) -> UpdatePlan:
    release = parse_upstream_release(
        read_json_object(release_path, label="upstream release JSON")
    )
    if release["draft"]:
        return _ignored_plan(plan_path, "draft")
    if release["prerelease"]:
        return _ignored_plan(plan_path, "prerelease")

    tag_name = release["tag_name"]
    upstream_version = DottedVersion.from_stable_tag(
        tag_name,
        "upstream stable release tag",
    )
    state = parse_updater_state(
        read_json_object(state_path, label="updater state JSON")
    )
    current_pins = parse_source_pins(read_json_object(pins_path, label="pins JSON"))
    if upstream_version < DottedVersion.from_stable_tag(
        state["last_checked_stable"],
        "current stable release",
    ):
        return _ignored_plan(plan_path, "older-than-current")

    plan: UpdatePlan
    candidate_inputs = collect_source_inputs(
        steam_source=steam_source,
        bazzite_source=bazzite_source,
    )
    candidate_steam_tag = steam_preset_tag(bazzite_source)
    candidate_fingerprint = theme_fingerprint(
        candidate_inputs,
        bazzite_source=bazzite_source,
        steam_commit=steam_commit,
    )
    if candidate_fingerprint != state.get("theme_fingerprint"):
        next_version = upstream_version.next_revision(
            DottedVersion.parse(state["version"], "current Vapor version")
        )
        selected_version = next_version
        if candidate_version is not None:
            selected_version = DottedVersion.parse(
                candidate_version,
                "selected Vapor version",
            )
            if (
                selected_version.parts[:-1] != upstream_version.parts
                or selected_version < next_version
            ):
                raise ValueError(
                    "selected Vapor version must be an available revision of the "
                    "current stable release"
                )
        version = str(selected_version)
        published_at = _parse_timestamp(
            release["published_at"],
            "published_at",
        )
        candidate_pins: SourcePins = {
            "bazzite": {
                "commit": bazzite_commit,
                "repository": current_pins["bazzite"]["repository"],
                "stable_release": str(upstream_version),
            },
            "inputs": candidate_inputs,
            "project_version": version,
            "schema_version": 1,
            "source_date_epoch": int(published_at.timestamp()),
            "steam_presets": {
                "commit": steam_commit,
                "repository": current_pins["steam_presets"]["repository"],
                "tag": candidate_steam_tag,
            },
        }
        updated_state: UpdaterState = state.copy()
        updated_state.update(
            {
                "heartbeat_month": _checked_month(checked_at),
                "last_checked_stable": tag_name,
                "last_successful_check": checked_at,
                "theme_fingerprint": candidate_fingerprint,
                "version": version,
            }
        )

        if artifact_path.exists():
            raise RuntimeError(
                f"refusing to overwrite an existing artifact: {artifact_path}"
            )
        with (
            vacant_temporary_path(
                artifact_path.parent,
                prefix=".vapor-artifact-",
                suffix=".tar.gz",
            ) as temporary_artifact,
            temporary_file(
                pins_path.parent,
                prefix=".candidate-pins-",
                suffix=".json",
            ) as temporary_pins,
        ):
            write_json_atomic(temporary_pins, candidate_pins)
            old_pins = pins_path.read_bytes()
            old_state = state_path.read_bytes()
            try:
                build_release(
                    steam_source=steam_source,
                    bazzite_source=bazzite_source,
                    pins_path=temporary_pins,
                    output_path=temporary_artifact,
                )
                validated_version = validate_bundle(temporary_artifact)
                if validated_version != version:
                    raise RuntimeError(
                        "validated artifact version does not match update plan"
                    )
                artifact_hash = sha256_file(temporary_artifact)
                plan = {
                    "action": "release",
                    "artifact": artifact_path.name,
                    "artifact_sha256": artifact_hash,
                    "incident": "close",
                    "schema_version": 1,
                    "stable_release": tag_name,
                    "tag": f"v{version}",
                    "theme_fingerprint": candidate_fingerprint,
                    "version": version,
                }
                os.replace(temporary_artifact, artifact_path)
                write_json_atomic(pins_path, candidate_pins)
                write_json_atomic(state_path, updated_state)
                write_json_atomic(plan_path, plan)
                return plan
            except BaseException:
                artifact_path.unlink(missing_ok=True)
                write_bytes_atomic(pins_path, old_pins)
                write_bytes_atomic(state_path, old_state)
                raise

    month = _checked_month(checked_at)
    stable_changed = state.get("last_checked_stable") != tag_name
    heartbeat_due = state.get("heartbeat_month") != month
    if stable_changed or heartbeat_due:
        state["last_checked_stable"] = tag_name
        state["heartbeat_month"] = month
        state["last_successful_check"] = checked_at
        write_json_atomic(state_path, state)
        plan = {
            "action": "state-only",
            "incident": "close",
            "reason": (
                "stable-without-theme-change" if stable_changed else "monthly-heartbeat"
            ),
            "schema_version": 1,
            "stable_release": tag_name,
        }
    else:
        plan = {
            "action": "none",
            "incident": "close",
            "reason": "already-checked",
            "schema_version": 1,
            "stable_release": tag_name,
        }
    write_json_atomic(plan_path, plan)
    return plan
