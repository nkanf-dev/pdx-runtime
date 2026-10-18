"""Check new commit messages with one policy for the local hook and CI.

This tool reads Git metadata only. It does not rewrite messages or history.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
HEADER = re.compile(
    r"(?P<type>[^\s():!]+)(?:\((?P<scope>[^()\r\n]+)\))?"
    r"!?: (?P<description>.*\S.*)"
)
REVERT_TARGET = re.compile(
    r"^This reverts commit [0-9a-f]{40}(?:[0-9a-f]{24})?"
    r"(?:\.|, reversing\nchanges made to [0-9a-f]{40}(?:[0-9a-f]{24})?\.)$",
    re.MULTILINE,
)


class PolicyError(ValueError):
    """An explicit range or required Git object could not be read."""


def validate_message(message: str, *, merge: bool = False) -> str | None:
    """Return a reason on failure; do not constrain type lists, case or length."""
    lines = message.splitlines()
    if not lines or not lines[0].strip():
        return "the first line must contain a Conventional Commit subject"
    subject = lines[0]
    if subject.startswith(("fixup! ", "squash! ", "amend! ")):
        return "temporary fixup!/squash!/amend! subjects cannot enter shared history"
    if len(lines) > 1 and lines[1].strip():
        return "separate the subject from the body or footers with a blank line"
    # Only a real/pending merge may use a Git/GitHub-generated merge subject.
    if merge and subject.startswith("Merge ") and subject[6:].strip():
        return None
    if re.fullmatch(r'Revert ".+"', subject):
        if REVERT_TARGET.search("\n".join(lines[2:])):
            return None
        return 'a generated Revert "..." message needs its "This reverts commit <hash>." body'
    match = HEADER.fullmatch(subject)
    if match is None or (match["scope"] is not None and not match["scope"].strip()):
        return "expected type(optional scope)(optional !): nonempty description"
    # BREAKING CHANGE is valid without !, as in the original 1.0 specification.
    for line in lines[2:]:
        for token in ("BREAKING CHANGE:", "BREAKING-CHANGE:"):
            if line.startswith(token):
                if not line.startswith(token + " ") or not line[len(token):].strip():
                    return f"{token} requires a space and a nonempty description"
    return None


def git(repo: Path, *args: str, input: str | None = None, check: bool = True):
    result = subprocess.run(
        ["git", "-C", str(repo), *args], input=input, capture_output=True,
        text=True, encoding="utf-8", errors="replace", check=False,
    )
    if check and result.returncode:
        raise PolicyError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def resolve_commit(repo: Path, revision: str) -> str:
    return git(repo, "rev-parse", "--verify", "--end-of-options",
               revision + "^{commit}").stdout.strip()


def select_commits(repo: Path, base: str | None, head: str, *,
                   allow_missing_base: bool = False) -> list[str]:
    """Select reachable head commits, excluding a supplied base when available.

    A push may replace history whose old tip is absent from a fresh checkout.
    In that case check every commit reachable from the new tip. Explicit local
    and pull-request bases remain required; no history or commit is exempted.
    """
    head_id = resolve_commit(repo, head)
    excluded = []
    if base:
        try:
            excluded = ["--not", resolve_commit(repo, base)]
        except PolicyError:
            if not allow_missing_base:
                raise
            print(f"Push base {base} is unavailable; checking all commits reachable "
                  f"from {head_id}.", file=sys.stderr)
    return git(repo, "rev-list", "--reverse", "--topo-order", head_id,
               *excluded, "--").stdout.splitlines()


def github_range(event_name: str, event: dict, sha: str | None) -> tuple[str | None, str | None]:
    """Return (excluded base, included head); None head means a deleted ref."""
    def oid(value):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
            raise PolicyError("GitHub range endpoints must be full commit IDs")
        return value

    if event_name == "pull_request":
        pull = event["pull_request"]
        return oid(pull["base"]["sha"]), oid(pull["head"]["sha"])
    if event_name == "push":
        before, after = oid(event["before"]), oid(event["after"])
        if not after.strip("0"):
            return None, None
        return (before if before.strip("0") else None), after
    if event_name == "workflow_dispatch" and sha:
        return None, oid(sha)
    raise PolicyError(f"unsupported or incomplete GitHub event: {event_name}")


def check_range(repo: Path, base: str | None, head: str, *,
                allow_missing_base: bool = False) -> int:
    commits = select_commits(repo, base, head, allow_missing_base=allow_missing_base)
    failures = 0
    for oid in commits:
        parents, message = git(repo, "show", "-s", "--format=%P%x00%B", oid, "--").stdout.split("\0", 1)
        reason = validate_message(message, merge=len(parents.split()) > 1)
        if reason:
            failures += 1
            subject = message.splitlines()[0] if message.splitlines() else "<empty>"
            print(f"FAIL {oid}: {subject}\n  {reason}", file=sys.stderr)
    print(f"Checked {len(commits)} commit(s); {failures} invalid.")
    return 1 if failures else 0


def check_file(repo: Path, path: Path) -> int:
    # Git's own cleanup honors core.commentChar/core.commentString and templates.
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    message = git(repo, "stripspace", "--strip-comments", input=raw).stdout
    merge_path = Path(git(repo, "rev-parse", "--git-path", "MERGE_HEAD").stdout.strip())
    if not merge_path.is_absolute():
        merge_path = repo / merge_path
    reason = validate_message(message, merge=merge_path.is_file())
    if reason:
        print(f"Invalid commit message: {reason}\n"
              "Example: fix(runtime): preserve the native result\n"
              "See CONTRIBUTING.md#commit-messages", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--message-file", type=Path, help="commit-msg hook input")
    mode.add_argument("--event-file", type=Path, help="GitHub event JSON")
    parser.add_argument("--event-name", choices=("pull_request", "push", "workflow_dispatch"))
    parser.add_argument("--sha", help="GitHub SHA for workflow_dispatch")
    parser.add_argument("--base", help="exclude commits already reachable from this base")
    parser.add_argument("--head", help="included head (default: HEAD)")
    args = parser.parse_args(argv)
    if (args.message_file or args.event_file) and (args.base or args.head):
        parser.error("--base/--head cannot be combined with a message or event file")
    if bool(args.event_file) != bool(args.event_name) or (args.sha and not args.event_file):
        parser.error("use --event-file with --event-name; --sha is only for event mode")
    try:
        if args.message_file:
            return check_file(ROOT, args.message_file)
        if args.event_file:
            event = json.loads(args.event_file.read_text(encoding="utf-8-sig"))
            base, head = github_range(args.event_name, event, args.sha)
            if head is None:
                print("Deleted ref: no new commits to check.")
                return 0
            return check_range(ROOT, base, head, allow_missing_base=args.event_name == "push")
        return check_range(ROOT, args.base, args.head or "HEAD")
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Commit check could not run: {error}\n"
              "Check the selected head/base objects and fetch the required history.",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
