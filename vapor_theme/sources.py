from __future__ import annotations

from pathlib import Path

from vapor_theme.io_utils import read_json_object, run_checked, staged_directory
from vapor_theme.records import parse_source_pins


def _git(arguments: list[str], *, failure: str) -> str:
    result = run_checked(["git"], *arguments, failure=failure)
    return result.stdout.strip()


def _checkout(repository: str, commit: str, destination: Path) -> None:
    destination.mkdir()
    _git(["init", "-q", str(destination)], failure="could not initialize source")
    _git(
        ["-C", str(destination), "remote", "add", "origin", repository],
        failure="could not configure source repository",
    )
    _git(
        [
            "-c",
            "protocol.file.allow=always",
            "-C",
            str(destination),
            "fetch",
            "--depth=1",
            "origin",
            commit,
        ],
        failure=f"could not fetch pinned commit {commit}",
    )
    _git(
        [
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.eol=lf",
            "-C",
            str(destination),
            "checkout",
            "-q",
            "--detach",
            "FETCH_HEAD",
        ],
        failure=f"could not check out pinned commit {commit}",
    )
    resolved = _git(
        ["-C", str(destination), "rev-parse", "HEAD"],
        failure="could not verify checked-out source",
    ).lower()
    if resolved != commit:
        raise RuntimeError(
            f"pinned commit mismatch: expected {commit}, resolved {resolved}"
        )


def fetch_sources(*, pins_path: Path, output: Path) -> None:
    pins = parse_source_pins(read_json_object(pins_path, label="pins JSON"))
    steam_repository = pins["steam_presets"]["repository"]
    steam_commit = pins["steam_presets"]["commit"]
    bazzite_repository = pins["bazzite"]["repository"]
    bazzite_commit = pins["bazzite"]["commit"]
    with staged_directory(
        output,
        conflict="refusing to replace source directory",
    ) as temporary:
        _checkout(steam_repository, steam_commit, temporary / "steam")
        _checkout(bazzite_repository, bazzite_commit, temporary / "bazzite")
