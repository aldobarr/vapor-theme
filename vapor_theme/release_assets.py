from __future__ import annotations

import json
import re
import shutil
import tarfile
from pathlib import Path

from vapor_theme.io_utils import (
    ensure_json_object,
    read_json_object,
    sha256_file,
    staged_directory,
    write_json_file,
)
from vapor_theme.records import (
    Provenance,
    ReleasePlan,
    parse_provenance,
    parse_update_plan,
)
from vapor_theme.validator import validate_bundle


def release_owner_marker(tag_object: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", tag_object) is None:
        raise ValueError("release tag object must be a full SHA-1")
    return f"<!-- vapor-theme-automation:v1 tag-object={tag_object} -->"


def plan_existing_release_upload(
    *,
    release_path: Path,
    tag: str,
    tag_object: str,
    assets: list[Path],
) -> tuple[int, tuple[Path, ...]]:
    release = read_json_object(release_path, label="GitHub release JSON")
    release_id = release.get("id")
    if (
        isinstance(release_id, bool)
        or not isinstance(release_id, int)
        or release_id < 1
    ):
        raise ValueError("GitHub release has an invalid database ID")
    if release.get("tag_name") != tag:
        raise ValueError("GitHub release tag does not match the requested release")
    published_at = release.get("published_at")
    if (
        release.get("draft") is not False
        or release.get("prerelease") is not False
        or not isinstance(published_at, str)
        or not published_at.strip()
    ):
        raise ValueError("GitHub release must be a published stable release")
    body = release.get("body")
    if body is None:
        body = ""
    if not isinstance(body, str):
        raise ValueError("GitHub release body must be text or null")

    expected: dict[str, Path] = {}
    for asset in assets:
        if not asset.is_file():
            raise FileNotFoundError(f"release asset is not a file: {asset}")
        if re.fullmatch(r"[A-Za-z0-9._-]+", asset.name) is None:
            raise ValueError(f"release asset name is not upload-safe: {asset.name}")
        if asset.name in expected:
            raise ValueError(f"duplicate expected release asset: {asset.name}")
        expected[asset.name] = asset
    if not expected:
        raise ValueError("at least one release asset is required")

    raw_assets = release.get("assets")
    if not isinstance(raw_assets, list):
        raise ValueError("GitHub release assets must be an array")
    matched: set[str] = set()
    for index, raw_asset in enumerate(raw_assets):
        release_asset = ensure_json_object(
            raw_asset, label=f"GitHub release asset {index}"
        )
        name = release_asset.get("name")
        if not isinstance(name, str):
            raise ValueError(f"GitHub release asset {index} has an invalid name")
        if name not in expected:
            continue
        if name in matched:
            raise ValueError(f"GitHub release contains duplicate asset: {name}")
        if release_asset.get("state") != "uploaded":
            raise ValueError(f"GitHub release asset is not uploaded: {name}")
        expected_digest = f"sha256:{sha256_file(expected[name])}"
        if release_asset.get("digest") != expected_digest:
            raise ValueError(f"GitHub release asset has different content: {name}")
        matched.add(name)

    missing = tuple(path for name, path in expected.items() if name not in matched)
    if missing and release_owner_marker(tag_object) not in body:
        raise ValueError(
            "incomplete GitHub release is not owned by Vapor automation; "
            "refusing to modify it"
        )
    return release_id, missing


def _embedded_provenance(bundle: Path, version: str) -> Provenance:
    member_name = f"vapor-{version}/provenance.json"
    try:
        with tarfile.open(bundle, "r:gz") as archive:
            member = archive.getmember(member_name)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("bundle provenance is not a regular file")
            value: object = json.loads(source.read().decode("utf-8"))
    except (
        KeyError,
        tarfile.TarError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        raise ValueError(f"could not read bundle provenance: {error}") from error
    return parse_provenance(ensure_json_object(value, label="bundle provenance"))


def read_release_plan(plan_path: Path) -> ReleasePlan:
    plan = parse_update_plan(read_json_object(plan_path, label="release plan JSON"))
    if plan["action"] != "release":
        raise ValueError("release plan must describe a release")
    return plan


def prepare_release_assets(
    *,
    bundle: Path,
    plan_path: Path,
    output: Path,
) -> None:
    plan = read_release_plan(plan_path)
    version = plan["version"]
    tag = plan["tag"]
    expected_hash = plan["artifact_sha256"]
    validated_version = validate_bundle(bundle)
    if validated_version != version:
        raise ValueError("release plan version does not match the bundle")
    actual_hash = sha256_file(bundle)
    if actual_hash != expected_hash:
        raise ValueError("artifact checksum does not match the release plan")
    provenance = _embedded_provenance(bundle, version)
    if provenance.get("project_version") != version:
        raise ValueError("bundle provenance version does not match the release")
    provenance["release"] = {
        "archive_sha256": actual_hash,
        "tag": tag,
    }

    archive_name = f"Vapor-{tag}.tar.gz"
    with staged_directory(
        output,
        conflict="refusing to replace release assets",
    ) as temporary:
        shutil.copyfile(bundle, temporary / archive_name)
        (temporary / "SHA256SUMS").write_text(
            f"{actual_hash}  {archive_name}\n",
            encoding="utf-8",
            newline="\n",
        )
        write_json_file(
            temporary / f"Vapor-{tag}.provenance.json",
            provenance,
        )
