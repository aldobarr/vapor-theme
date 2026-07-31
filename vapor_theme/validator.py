from __future__ import annotations

import tarfile
import tempfile
from pathlib import Path

from vapor_theme.bundle_contract import (
    GLOBAL_THEME_ID,
    collect_regular_file_paths,
    require_regular_file,
    safe_relative_path,
    validate_manifested_payload,
)
from vapor_theme.io_utils import read_json_object
from vapor_theme.records import (
    BundleManifest,
    parse_bundle_manifest,
    parse_provenance,
)


def _extract_archive(archive_path: Path, destination: Path) -> Path:
    seen: set[str] = set()
    roots: set[str] = set()
    with tarfile.open(archive_path, "r:*") as archive:
        for member in archive:
            member_path = safe_relative_path(member.name, label="archive entry")
            canonical_name = member_path.as_posix()
            if canonical_name in seen:
                raise ValueError(f"duplicate archive entry: {canonical_name}")
            seen.add(canonical_name)
            roots.add(member_path.parts[0])
            if not (member.isdir() or member.isfile()):
                raise ValueError(
                    f"archive contains a link or special entry: {canonical_name}"
                )
            target = destination.joinpath(*member_path.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"cannot read archive entry: {canonical_name}")
            with target.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
    if len(roots) != 1:
        raise ValueError("bundle archive must contain exactly one top-level directory")
    return destination / next(iter(roots))


def _validate_manifest(bundle_root: Path) -> tuple[BundleManifest, Path]:
    manifest = parse_bundle_manifest(
        read_json_object(
            require_regular_file(
                bundle_root,
                "manifest.json",
                missing="required bundle file is missing",
            )
        ),
        expected_global_theme_id=GLOBAL_THEME_ID,
    )
    payload = bundle_root / "payload"
    validate_manifested_payload(payload, manifest)
    return manifest, payload


def _validate_bundle_root(bundle_root: Path) -> str:
    collect_regular_file_paths(
        bundle_root,
        subject="bundle",
    )
    manifest, payload = _validate_manifest(bundle_root)
    version = manifest["version"]
    if bundle_root.name != f"vapor-{version}":
        raise ValueError("bundle directory name does not match its version")

    provenance = parse_provenance(
        read_json_object(
            require_regular_file(
                bundle_root,
                "provenance.json",
                missing="required bundle file is missing",
            )
        )
    )
    if provenance["project_version"] != version:
        raise ValueError("provenance version does not match manifest")
    if provenance["source_pins"]["project_version"] != version:
        raise ValueError("provenance source pins do not match manifest")
    for relative_path in (
        "THIRD_PARTY_NOTICES.md",
        "LICENSES/AGPL-3.0-only.txt",
        "LICENSES/Apache-2.0.txt",
        "LICENSES/GPL-2.0.txt",
        "install.sh",
        "uninstall.sh",
        "lib/vapor_installer.py",
        "lib/vapor_theme/__init__.py",
        "lib/vapor_theme/bundle_contract.py",
        "lib/vapor_theme/io_utils.py",
        "lib/vapor_theme/records.py",
    ):
        require_regular_file(
            bundle_root,
            relative_path,
            missing="required bundle file is missing",
        )

    return version


def validate_bundle(bundle_path: Path) -> str:
    if bundle_path.is_dir():
        return _validate_bundle_root(bundle_path.resolve())
    if not bundle_path.is_file():
        raise FileNotFoundError(f"bundle does not exist: {bundle_path}")
    with tempfile.TemporaryDirectory(prefix="vapor-validate-") as temporary:
        bundle_root = _extract_archive(bundle_path, Path(temporary))
        return _validate_bundle_root(bundle_root)
