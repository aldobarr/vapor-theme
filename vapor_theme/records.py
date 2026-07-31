from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, NotRequired, TypeAlias, TypedDict, cast

from vapor_theme.io_utils import JsonObject


@dataclass(frozen=True, order=True)
class DottedVersion:
    value: str = field(compare=False)
    parts: tuple[int, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if re.fullmatch(r"\d+(?:\.\d+)+", self.value) is None:
            raise ValueError(f"version must be numeric and dotted: {self.value!r}")
        object.__setattr__(
            self,
            "parts",
            tuple(int(part) for part in self.value.split(".")),
        )

    def __str__(self) -> str:
        return self.value

    @classmethod
    def parse(cls, value: object, label: str) -> DottedVersion:
        if not isinstance(value, str):
            raise ValueError(f"{label} must be numeric and dotted")
        try:
            return cls(value)
        except ValueError as error:
            raise ValueError(f"{label} must be numeric and dotted") from error

    @classmethod
    def from_stable_tag(cls, value: object, label: str) -> DottedVersion:
        if not isinstance(value, str):
            raise ValueError(f"{label} must be a stable dotted version")
        candidate = value.removeprefix("stable-")
        try:
            return cls(candidate)
        except ValueError as error:
            raise ValueError(f"{label} must be a stable dotted version") from error

    def next_revision(self, current: DottedVersion | None) -> DottedVersion:
        revision = 1
        if current is not None and current.parts[:-1] == self.parts:
            revision = current.parts[-1] + 1
        return DottedVersion(f"{self.value}.{revision}")


class BundleManifest(TypedDict):
    files: dict[str, str]
    global_theme_id: str
    schema_version: int
    version: str


class InstalledState(TypedDict):
    files: dict[str, str]
    global_theme_id: str
    retained: NotRequired[dict[str, str]]
    schema_version: int
    version: str


class BazzitePin(TypedDict):
    commit: str
    repository: str
    stable_release: str


class SteamPresetPin(TypedDict):
    commit: str
    repository: str
    tag: str


class SourcePins(TypedDict):
    bazzite: BazzitePin
    inputs: dict[str, str]
    project_version: str
    schema_version: int
    source_date_epoch: int
    steam_presets: SteamPresetPin


class UpdaterState(TypedDict):
    heartbeat_month: str
    last_checked_stable: str
    last_successful_check: NotRequired[str]
    schema_version: int
    theme_fingerprint: str
    version: str


class FailureIncident(TypedDict):
    action: Literal["open-or-update"]
    key: Literal["vapor-updater"]
    title: str


class FailurePlan(TypedDict):
    action: Literal["failure"]
    error: str
    incident: FailureIncident
    schema_version: Literal[1]


class IgnoredPlan(TypedDict):
    action: Literal["ignored"]
    incident: Literal["close"]
    reason: Literal["draft", "prerelease", "older-than-current"]
    schema_version: Literal[1]


class NoChangePlan(TypedDict):
    action: Literal["none"]
    incident: Literal["close"]
    reason: Literal["already-checked"]
    schema_version: Literal[1]
    stable_release: str


class ReleasePlan(TypedDict):
    action: Literal["release"]
    artifact: str
    artifact_sha256: str
    incident: Literal["close"]
    schema_version: Literal[1]
    stable_release: str
    tag: str
    theme_fingerprint: str
    version: str


class StateOnlyPlan(TypedDict):
    action: Literal["state-only"]
    incident: Literal["close"]
    reason: Literal["stable-without-theme-change", "monthly-heartbeat"]
    schema_version: Literal[1]
    stable_release: str


UpdatePlan: TypeAlias = (
    FailurePlan | IgnoredPlan | NoChangePlan | ReleasePlan | StateOnlyPlan
)


class UpstreamRelease(TypedDict):
    draft: bool
    prerelease: bool
    published_at: str
    tag_name: str


class ReleaseProvenance(TypedDict):
    archive_sha256: str
    tag: str


class Provenance(TypedDict):
    project_version: str
    source_pins: SourcePins
    schema_version: int
    release: NotRequired[ReleaseProvenance]


class GitHubIssue(TypedDict):
    number: int
    state: str
    title: str


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object")
    return cast(JsonObject, value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _stable_release(value: object, label: str) -> str:
    release = _string(value, label)
    DottedVersion.from_stable_tag(release, label)
    return release


def _sha256(value: object, label: str) -> str:
    digest = _string(value, label)
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _closed_incident(
    value: JsonObject,
    label: str,
) -> Literal["close"]:
    if value.get("incident") != "close":
        raise ValueError(f"{label} incident must be close")
    return "close"


def _string_map(value: object, label: str) -> dict[str, str]:
    mapping = _object(value, label)
    if not mapping or not all(isinstance(item, str) for item in mapping.values()):
        raise ValueError(f"{label} must be a nonempty string map")
    return cast(dict[str, str], mapping)


def _require_exact_fields(
    value: JsonObject,
    expected: set[str],
    label: str,
) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} fields are invalid")


def parse_bundle_manifest(
    value: JsonObject,
    *,
    expected_global_theme_id: str,
) -> BundleManifest:
    if value.get("schema_version") != 1:
        raise ValueError("unsupported bundle manifest schema")
    global_theme_id = _string(
        value.get("global_theme_id"),
        "bundle manifest global_theme_id",
    )
    if global_theme_id != expected_global_theme_id:
        raise ValueError("bundle Global Theme ID is invalid")
    version = str(
        DottedVersion.parse(
            value.get("version"),
            "bundle manifest version",
        )
    )
    return {
        "files": _string_map(value.get("files"), "bundle manifest files"),
        "global_theme_id": global_theme_id,
        "schema_version": 1,
        "version": version,
    }


def parse_installed_state(
    value: JsonObject,
    *,
    expected_global_theme_id: str,
) -> InstalledState:
    manifest = parse_bundle_manifest(
        value,
        expected_global_theme_id=expected_global_theme_id,
    )
    state: InstalledState = {
        "files": manifest["files"],
        "global_theme_id": manifest["global_theme_id"],
        "schema_version": manifest["schema_version"],
        "version": manifest["version"],
    }
    retained = value.get("retained")
    if retained is not None:
        state["retained"] = _string_map(retained, "installed state retained")
    return state


def _git_commit(value: object, label: str) -> str:
    commit = _string(value, label).lower()
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError(f"{label} must be a full 40-character Git hash")
    return commit


def _bazzite_pin(value: object) -> BazzitePin:
    source = _object(value, "bazzite")
    stable_release = _stable_release(
        source.get("stable_release"),
        "bazzite stable_release",
    ).removeprefix("stable-")
    return {
        "commit": _git_commit(source.get("commit"), "bazzite commit"),
        "repository": _string(
            source.get("repository"),
            "bazzite repository",
        ),
        "stable_release": stable_release,
    }


def _steam_preset_pin(value: object) -> SteamPresetPin:
    source = _object(value, "steam_presets")
    return {
        "commit": _git_commit(
            source.get("commit"),
            "steam_presets commit",
        ),
        "repository": _string(
            source.get("repository"),
            "steam_presets repository",
        ),
        "tag": _string(source.get("tag"), "steam_presets tag"),
    }


def parse_source_pins(value: JsonObject) -> SourcePins:
    if value.get("schema_version") != 1:
        raise ValueError("pins must be a schema version 1 JSON object")
    bazzite = _bazzite_pin(value.get("bazzite"))
    steam_presets = _steam_preset_pin(value.get("steam_presets"))
    parsed_project_version = DottedVersion.parse(
        value.get("project_version"),
        "project_version",
    )
    parsed_stable_release = DottedVersion(bazzite["stable_release"])
    if (
        parsed_project_version.parts[:-1] != parsed_stable_release.parts
        or len(parsed_project_version.parts) != len(parsed_stable_release.parts) + 1
        or parsed_project_version.parts[-1] < 1
    ):
        raise ValueError(
            "project_version must be derived from bazzite stable_release "
            "with a positive build revision"
        )
    project_version = str(parsed_project_version)
    source_date_epoch = value.get("source_date_epoch")
    if (
        not isinstance(source_date_epoch, int)
        or isinstance(source_date_epoch, bool)
        or source_date_epoch < 0
    ):
        raise ValueError("source_date_epoch must be a nonnegative integer")
    inputs = _string_map(value.get("inputs"), "pins inputs")
    for input_name, digest in inputs.items():
        if ":" not in input_name:
            raise ValueError(f"invalid source pin hash: {input_name}")
        inputs[input_name] = _sha256(digest, f"source pin hash {input_name}")
    return {
        "bazzite": bazzite,
        "inputs": inputs,
        "project_version": project_version,
        "schema_version": 1,
        "source_date_epoch": source_date_epoch,
        "steam_presets": steam_presets,
    }


def parse_updater_state(value: JsonObject) -> UpdaterState:
    if value.get("schema_version") != 1:
        raise ValueError("unsupported updater state schema")
    last_checked_stable = _stable_release(
        value.get("last_checked_stable"),
        "updater state last_checked_stable",
    )
    state: UpdaterState = {
        "heartbeat_month": _string(
            value.get("heartbeat_month"),
            "updater state heartbeat_month",
        ),
        "last_checked_stable": last_checked_stable,
        "schema_version": 1,
        "theme_fingerprint": _string(
            value.get("theme_fingerprint"),
            "updater state theme_fingerprint",
        ),
        "version": str(
            DottedVersion.parse(
                value.get("version"),
                "updater state version",
            )
        ),
    }
    if "last_successful_check" in value:
        state["last_successful_check"] = _string(
            value["last_successful_check"],
            "updater state last_successful_check",
        )
    return state


def parse_update_plan(value: JsonObject) -> UpdatePlan:
    if value.get("schema_version") != 1:
        raise ValueError("update plan must be a schema version 1 JSON object")
    action = _string(value.get("action"), "update plan action")
    if action == "failure":
        _require_exact_fields(
            value,
            {"action", "error", "incident", "schema_version"},
            "failure plan",
        )
        incident = _object(value.get("incident"), "failure plan incident")
        _require_exact_fields(
            incident,
            {"action", "key", "title"},
            "failure plan incident",
        )
        if (
            incident.get("action") != "open-or-update"
            or incident.get("key") != "vapor-updater"
        ):
            raise ValueError("failure plan incident is invalid")
        return {
            "action": "failure",
            "error": _string(value.get("error"), "failure plan error"),
            "incident": {
                "action": "open-or-update",
                "key": "vapor-updater",
                "title": _string(
                    incident.get("title"),
                    "failure plan incident title",
                ),
            },
            "schema_version": 1,
        }
    if action == "ignored":
        _require_exact_fields(
            value,
            {"action", "incident", "reason", "schema_version"},
            "ignored plan",
        )
        closed_incident = _closed_incident(value, "ignored plan")
        reason = value.get("reason")
        if reason not in {"draft", "prerelease", "older-than-current"}:
            raise ValueError("ignored plan reason is invalid")
        ignored: IgnoredPlan = {
            "action": "ignored",
            "incident": closed_incident,
            "reason": reason,
            "schema_version": 1,
        }
        return ignored
    if action == "none":
        _require_exact_fields(
            value,
            {
                "action",
                "incident",
                "reason",
                "schema_version",
                "stable_release",
            },
            "no-change plan",
        )
        closed_incident = _closed_incident(value, "no-change plan")
        if value.get("reason") != "already-checked":
            raise ValueError("no-change plan fields are invalid")
        stable_release = _stable_release(
            value.get("stable_release"),
            "no-change plan stable_release",
        )
        return {
            "action": "none",
            "incident": closed_incident,
            "reason": "already-checked",
            "schema_version": 1,
            "stable_release": stable_release,
        }
    if action == "release":
        _require_exact_fields(
            value,
            {
                "action",
                "artifact",
                "artifact_sha256",
                "incident",
                "schema_version",
                "stable_release",
                "tag",
                "theme_fingerprint",
                "version",
            },
            "release plan",
        )
        closed_incident = _closed_incident(value, "release plan")
        version = str(
            DottedVersion.parse(
                value.get("version"),
                "release plan version",
            )
        )
        tag = _string(value.get("tag"), "release plan tag")
        if tag != f"v{version}":
            raise ValueError("release plan tag does not match its version")
        checksum = _sha256(
            value.get("artifact_sha256"),
            "release plan artifact_sha256",
        )
        stable_release = _stable_release(
            value.get("stable_release"),
            "release plan stable_release",
        )
        return {
            "action": "release",
            "artifact": _string(
                value.get("artifact"),
                "release plan artifact",
            ),
            "artifact_sha256": checksum,
            "incident": closed_incident,
            "schema_version": 1,
            "stable_release": stable_release,
            "tag": tag,
            "theme_fingerprint": _string(
                value.get("theme_fingerprint"),
                "release plan theme_fingerprint",
            ),
            "version": version,
        }
    if action == "state-only":
        _require_exact_fields(
            value,
            {
                "action",
                "incident",
                "reason",
                "schema_version",
                "stable_release",
            },
            "state-only plan",
        )
        closed_incident = _closed_incident(value, "state-only plan")
        reason = value.get("reason")
        if reason not in {
            "stable-without-theme-change",
            "monthly-heartbeat",
        }:
            raise ValueError("state-only plan reason is invalid")
        stable_release = _stable_release(
            value.get("stable_release"),
            "state-only plan stable_release",
        )
        state_only: StateOnlyPlan = {
            "action": "state-only",
            "incident": closed_incident,
            "reason": reason,
            "schema_version": 1,
            "stable_release": stable_release,
        }
        return state_only
    raise ValueError(f"unsupported update plan action: {action}")


def parse_upstream_release(value: JsonObject) -> UpstreamRelease:
    draft = value.get("draft")
    prerelease = value.get("prerelease")
    if not isinstance(draft, bool) or not isinstance(prerelease, bool):
        raise ValueError("upstream release flags must be booleans")
    return {
        "draft": draft,
        "prerelease": prerelease,
        "published_at": _string(
            value.get("published_at"),
            "upstream release published_at",
        ),
        "tag_name": _string(value.get("tag_name"), "upstream release tag_name"),
    }


def parse_provenance(value: JsonObject) -> Provenance:
    if value.get("schema_version") != 1:
        raise ValueError("provenance must use schema version 1")
    provenance: Provenance = {
        "project_version": _string(
            value.get("project_version"),
            "provenance project_version",
        ),
        "schema_version": 1,
        "source_pins": parse_source_pins(
            _object(value.get("source_pins"), "provenance source_pins")
        ),
    }
    if "release" in value:
        release = _object(value["release"], "provenance release")
        provenance["release"] = {
            "archive_sha256": _sha256(
                release.get("archive_sha256"),
                "provenance release archive_sha256",
            ),
            "tag": _string(release.get("tag"), "provenance release tag"),
        }
    return provenance
