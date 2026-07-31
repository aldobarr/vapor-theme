from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import cast

JsonObject = dict[str, object]


def command_from_environment(name: str, default: list[str]) -> list[str]:
    configured = os.environ.get(name)
    if configured is None:
        return default
    try:
        value: object = json.loads(configured)
    except json.JSONDecodeError as error:
        raise ValueError(f"{name} must be a JSON command array") from error
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(part, str) and part for part in value)
    ):
        raise ValueError(f"{name} must be a nonempty JSON string array")
    return cast(list[str], value)


def ensure_json_object(value: object, *, label: str) -> JsonObject:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} must contain a JSON object")
    return cast(JsonObject, value)


def read_json_object(path: Path, *, label: str = "JSON file") -> JsonObject:
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid {label} {path}: {error}") from error
    return ensure_json_object(value, label=f"{label} {path}")


def canonical_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def write_json_file(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        canonical_json(value),
        encoding="utf-8",
        newline="\n",
    )


def write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temporary_name)
        raise


def write_json_atomic(path: Path, value: object) -> None:
    content = canonical_json(value).encode("utf-8")
    write_bytes_atomic(path, content)


def run_checked(
    command: list[str],
    *arguments: str,
    failure: str,
    environment: dict[str, str] | None = None,
    working_directory: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            [*command, *arguments],
            text=True,
            capture_output=True,
            check=False,
            cwd=working_directory,
            env=environment,
        )
    except FileNotFoundError as error:
        raise RuntimeError(f"required command is unavailable: {command[0]}") from error
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{failure}: {detail}")
    return result


@contextmanager
def staged_directory(
    output: Path,
    *,
    conflict: str,
) -> Iterator[Path]:
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"{conflict}: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        yield temporary
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"{conflict}: {output}")
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


@contextmanager
def temporary_file(
    parent: Path,
    *,
    prefix: str,
    suffix: str,
) -> Iterator[Path]:
    parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=prefix,
        suffix=suffix,
        dir=parent,
    )
    os.close(descriptor)
    path = Path(temporary_name)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


@contextmanager
def vacant_temporary_path(
    parent: Path,
    *,
    prefix: str,
    suffix: str,
) -> Iterator[Path]:
    with temporary_file(parent, prefix=prefix, suffix=suffix) as path:
        path.unlink()
        yield path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
