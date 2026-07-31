from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

from vapor_theme.io_utils import (
    command_from_environment,
    read_json_object,
    run_checked,
)
from vapor_theme.records import GitHubIssue, parse_update_plan


def _run_gh(command: list[str], arguments: list[str]) -> str:
    rendered = shlex.join(arguments)
    result = run_checked(
        command,
        *arguments,
        failure=f"gh {rendered} failed",
    )
    return result.stdout


def _matching_issues(
    command: list[str],
    repository: str,
    title: str,
) -> list[GitHubIssue]:
    output = _run_gh(
        command,
        [
            "issue",
            "list",
            "--repo",
            repository,
            "--state",
            "all",
            "--search",
            f'"{title}" in:title',
            "--json",
            "number,state,title",
            "--limit",
            "100",
        ],
    )
    try:
        issues: object = json.loads(output)
    except json.JSONDecodeError as error:
        raise RuntimeError("gh issue list returned invalid JSON") from error
    if not isinstance(issues, list):
        raise RuntimeError("gh issue list did not return a JSON array")
    matching: list[GitHubIssue] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        issue_title = issue.get("title")
        number = issue.get("number")
        state = issue.get("state")
        if (
            issue_title == title
            and isinstance(number, int)
            and not isinstance(number, bool)
            and isinstance(state, str)
        ):
            matching.append(
                {
                    "number": number,
                    "state": state,
                    "title": title,
                }
            )
    return matching


def update_incident(
    *,
    plan_path: Path,
    repository: str,
    run_url: str,
) -> None:
    if re.fullmatch(r"[^/\s]+/[^/\s]+", repository) is None:
        raise ValueError("repository must have owner/name form")
    plan = parse_update_plan(read_json_object(plan_path, label="update plan JSON"))
    incident = plan.get("incident")
    if not isinstance(incident, (dict, str)):
        return
    command = command_from_environment("VAPOR_GH_COMMAND", ["gh"])
    title = (
        incident.get("title")
        if isinstance(incident, dict)
        else "[automation] Vapor updater failure"
    )
    if not isinstance(title, str) or not title:
        raise ValueError("incident title must be a nonempty string")
    issues = _matching_issues(command, repository, title)
    issue = next(
        (candidate for candidate in issues if candidate.get("state") == "OPEN"),
        issues[0] if issues else None,
    )

    if isinstance(incident, dict) and incident.get("action") == "open-or-update":
        error = plan.get("error")
        body = "\n".join(
            [
                "<!-- vapor-updater -->",
                "The automated Vapor upstream update failed.",
                "",
                f"Error: `{error}`",
                "",
                f"Workflow run: {run_url}",
            ]
        )
        if issue is None:
            _run_gh(
                command,
                [
                    "issue",
                    "create",
                    "--repo",
                    repository,
                    "--title",
                    title,
                    "--body",
                    body,
                ],
            )
            return
        number = str(issue["number"])
        if issue.get("state") != "OPEN":
            _run_gh(
                command,
                ["issue", "reopen", number, "--repo", repository],
            )
        _run_gh(
            command,
            [
                "issue",
                "comment",
                number,
                "--repo",
                repository,
                "--body",
                body,
            ],
        )
        return

    if incident == "close" and issue is not None and issue.get("state") == "OPEN":
        _run_gh(
            command,
            [
                "issue",
                "close",
                str(issue["number"]),
                "--repo",
                repository,
                "--comment",
                f"Recovered successfully. Workflow run: {run_url}",
            ],
        )
