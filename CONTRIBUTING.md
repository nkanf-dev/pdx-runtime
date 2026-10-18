# Contributing

Use the build and test commands in [DEVELOPMENT.md](DEVELOPMENT.md). Keep each
change focused. Run the tests for the paths you change. Commit-message checks use
Python 3.11+ and Git only. They do not load the SDK, native DLL or a game.

## Commit messages

New commits follow [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/):

```text
type(optional scope)(optional !): nonempty description

Optional body.

Optional footers.
```

The scope and `!` are optional. Put `!` immediately before `:`. Put a space after
`:`. Separate a body or footers from the subject with a blank line. Write a clear
description of the change.

Examples:

```text
feat(runtime): expose a new observation
fix(v3): preserve a partial native result
docs: explain the offline checks
ci: check commit messages
refactor(SDK)!: rename an entry point
```

A breaking change can use `!`, a `BREAKING CHANGE:` footer, or both. The original
footer form works without `!`:

```text
feat(runtime): replace the session entry point

BREAKING CHANGE: callers must use the new entry point.
```

`BREAKING-CHANGE:` is also accepted. Give either breaking-change footer a
nonempty description. The reserved breaking-change tokens are uppercase.

Common project types are `feat`, `fix`, `docs`, `test`, `build`, `ci`, `refactor`,
`perf`, `style`, `chore` and `revert`. These are recommendations, not an allowlist.
Conventional Commits permits other types. This project adds no type allowlist,
scope allowlist, letter-case rule or subject-length limit. Choose a short noun
for a type and, if useful, a scope such as `runtime`, `v3`, `ci` or `regression`.
The validator checks message syntax, not the truth or completeness of a change's
semantic-versioning description.

### Merge, revert and temporary messages

- A real merge commit may keep its ordinary Git/GitHub-generated `Merge ...`
  subject. The hook requires a pending merge (`MERGE_HEAD`); CI requires at least
  two parents. A single-parent commit named `Merge ...` is not exempt. A custom
  merge subject must follow the normal format.
- An ordinary Git-generated `Revert "..."` subject is accepted with its standard
  `This reverts commit <full hash>.` body. Git's standard merge-revert body is
  accepted too. Otherwise use `revert: describe what is reverted`.
- `fixup!`, `squash!` and `amend!` subjects are rejected locally and in CI. Use a
  final Conventional Commit subject for new work. Existing published commits and
  release tags must not be rewritten to satisfy this check.
- A squash merge creates a new single-parent commit. Its final message must also
  follow this policy. CI checks the source commits of a pull request, not only its
  title. If GitHub derives a squash message from a PR title, make that title
  conventional too. A future merge message is not present in the PR range; the
  subsequent push check validates that new commit.

### Enable the local hook

First check whether this clone already uses other hooks:

```powershell
git config --show-origin --get core.hooksPath
```

If a hook directory is already configured, integrate the call to
`tools/check_commit_messages.py --message-file "$1"` into that hook instead of
silently replacing it. Otherwise run this once from the repository root:

```powershell
git config --local core.hooksPath .githooks
```

This changes only this clone. It does not change global Git configuration.
The hook uses `.venv/Scripts/python.exe` on Windows or `.venv/bin/python` on POSIX.
It falls back to `python3` or `python` on `PATH`; that interpreter must be 3.11+.
The tracked hook must have Git executable mode `100755` on POSIX.
`.githooks/.gitattributes` keeps its shell script in LF format on every checkout.
Git's comment cleanup is used for the local message file. No file is rewritten.
The hook is opt-in per clone; CI still checks new commits if the hook is absent.

Run the same validator by hand:

```powershell
.venv/Scripts/python.exe tools/check_commit_messages.py --message-file .git/COMMIT_EDITMSG
.venv/Scripts/python.exe tools/check_commit_messages.py --base origin/main --head HEAD
.venv/Scripts/python.exe -m unittest discover -s tests -p test_commit_messages.py -v
```

The message-file command reads the specified file and the current merge state;
it does not create a commit. A range check with no `--base` checks all commits reachable from the selected head. Exit codes are `0` for success, `1` for invalid
messages, and `2` when the check cannot read its required input or history.

### CI ranges

`.github/workflows/commit-messages.yml` runs the same validator:

| Event | Included commits |
| --- | --- |
| Pull request | Commits reachable from the source head, but not from the target base SHA. |
| Push with an available `before` commit | Commits reachable from `after`, but not from `before`. |
| Push whose `before` commit is absent from the checkout | All commits reachable from `after`. This also covers a parentless history reset. |
| New branch/tag or manual workflow run | All commits reachable from the selected SHA. |
| Deleted branch/tag | No new commits; the job is skipped. |

There are no historical exemptions or pinned migration baseline. An explicit
`--base` must exist. Missing heads, unreadable history and invalid inputs fail the
check. An event with a missing previous commit checks the full reachable history
instead of treating an unknown range as success.

Repository administrators can require the **Conventional Commits** status check
in branch protection. Adding the workflow does not itself change GitHub branch
protection settings.
