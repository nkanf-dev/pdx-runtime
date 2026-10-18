"""Targeted commit-policy contracts. No SDK, native, game or SCM mutation."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
from io import StringIO
import json
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("commit_messages", ROOT / "tools/check_commit_messages.py")
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)
BASE = "2" * 40
HEAD = "3" * 40
ZERO = "0" * 40


def result(stdout="", returncode=0, stderr=""):
    return CompletedProcess([], returncode, stdout, stderr)


class MessageTests(unittest.TestCase):
    def test_valid_conventional_subjects(self):
        subjects = [
            "feat: expose an observation", "fix(v3): preserve a partial result",
            "docs: document the hook", "ci: validate commit messages",
            "chore: update tooling", "custom-type(SDK/IO): describe a change",
            "FIX(SDK): Preserve the native result", "feat!: change the entry point",
            "refactor(runtime)!: rename a method", "docs: 中文提交说明",
            "docs: " + "a" * 200,
        ]
        for subject in subjects:
            with self.subTest(subject=subject):
                self.assertIsNone(policy.validate_message(subject))

    def test_original_breaking_footer_does_not_need_bang(self):
        for footer in ("BREAKING CHANGE: use the new entry point",
                       "BREAKING-CHANGE: use the new entry point"):
            with self.subTest(footer=footer):
                self.assertIsNone(policy.validate_message("feat(runtime): change entry point\n\n" + footer))

    def test_body_and_multiple_footers(self):
        self.assertIsNone(policy.validate_message(
            "fix(v3): preserve the result\n\nExplain the change.\n\n"
            "BREAKING CHANGE: this changes the return shape\n"
            "Refs: #123\nReviewed-by: Example\n"))

    def test_invalid_subjects(self):
        subjects = [
            "", "\nfix: misplaced subject", "Expose native results",
            "Add bounded observations", "fix:missing space", "fix:", "fix: ",
            "fix: \t", "fix(): empty scope", "fix( ): blank scope",
            "fix(scope: missing parenthesis", "fix((scope)): nested scope",
            "fix(scope)! : misplaced bang", "fix!!: repeated bang",
            ": missing type", "fix scope: invalid type", " fix: leading space",
        ]
        for subject in subjects:
            with self.subTest(subject=subject):
                self.assertIsNotNone(policy.validate_message(subject))

    def test_body_needs_blank_separator(self):
        self.assertIn("blank line", policy.validate_message("fix: change\nBody without separator"))

    def test_breaking_footer_needs_description(self):
        for footer in ("BREAKING CHANGE:", "BREAKING CHANGE: ", "BREAKING-CHANGE:no space"):
            with self.subTest(footer=footer):
                self.assertIsNotNone(policy.validate_message("fix: change\n\n" + footer))

    def test_temporary_subjects_are_rejected_even_in_a_merge(self):
        for prefix in ("fixup! ", "squash! ", "amend! "):
            for merge in (False, True):
                with self.subTest(prefix=prefix, merge=merge):
                    self.assertIn("temporary", policy.validate_message(prefix + "fix: change", merge=merge))

    def test_generated_merge_requires_actual_merge_context(self):
        for subject in ("Merge branch 'topic'", "Merge pull request #12 from user/topic"):
            with self.subTest(subject=subject):
                self.assertIsNone(policy.validate_message(subject, merge=True))
                self.assertIsNotNone(policy.validate_message(subject, merge=False))
        self.assertIsNotNone(policy.validate_message("Arbitrary merge description", merge=True))
        self.assertIsNotNone(policy.validate_message("Merge ", merge=True))
        self.assertIsNone(policy.validate_message("chore: merge topic", merge=True))

    def test_standard_git_revert_and_conventional_revert(self):
        self.assertIsNone(policy.validate_message('Revert "old subject"\n\nThis reverts commit ' + HEAD + '.\n'))
        self.assertIsNone(policy.validate_message(
            'Revert "merge subject"\n\nThis reverts commit ' + HEAD + ', reversing\n'
            'changes made to ' + BASE + '.\n'))
        self.assertIsNone(policy.validate_message("revert: remove the old change"))
        self.assertIsNone(policy.validate_message('Revert "old subject"\n\nThis reverts commit ' + "a" * 64 + '.'))

    def test_generated_revert_requires_exact_full_target_line(self):
        bodies = ["", "This reverts commit abc123.", "not This reverts commit " + HEAD + ".",
                  "This reverts commit " + HEAD + ". extra", "This reverts commit " + "a" * 41 + "."]
        for body in bodies:
            with self.subTest(body=body):
                self.assertIsNotNone(policy.validate_message('Revert "subject"\n\n' + body))


class RangeTests(unittest.TestCase):
    def test_pr_uses_source_and_target_shas_not_synthetic_merge(self):
        event = {"pull_request": {"base": {"sha": BASE}, "head": {"sha": HEAD}}}
        self.assertEqual(policy.github_range("pull_request", event, "9" * 40), (BASE, HEAD))

    def test_push_uses_before_after(self):
        self.assertEqual(policy.github_range("push", {"before": BASE, "after": HEAD}, None), (BASE, HEAD))

    def test_new_ref_has_no_excluded_base(self):
        self.assertEqual(policy.github_range("push", {"before": ZERO, "after": HEAD}, None), (None, HEAD))

    def test_ref_deletion_has_no_head(self):
        self.assertEqual(policy.github_range("push", {"before": BASE, "after": ZERO}, None), (None, None))

    def test_manual_run_uses_selected_sha(self):
        self.assertEqual(policy.github_range("workflow_dispatch", {}, HEAD), (None, HEAD))

    def test_incomplete_or_unknown_events_fail(self):
        for name, event, sha in [("pull_request_target", {}, HEAD), ("workflow_dispatch", {}, None),
                                 ("push", {"before": ZERO, "after": ""}, None),
                                 ("push", {"before": BASE, "after": "main"}, None)]:
            with self.subTest(name=name, event=event):
                with self.assertRaises(policy.PolicyError):
                    policy.github_range(name, event, sha)
        with self.assertRaises(KeyError):
            policy.github_range("pull_request", {}, HEAD)

    def test_no_base_checks_all_reachable_commits_including_root(self):
        with patch.object(policy, "resolve_commit", return_value=HEAD) as resolve, \
             patch.object(policy, "git", return_value=result(HEAD + "\n")) as mocked:
            self.assertEqual(policy.select_commits(ROOT, None, HEAD), [HEAD])
        resolve.assert_called_once_with(ROOT, HEAD)
        mocked.assert_called_once_with(ROOT, "rev-list", "--reverse", "--topo-order", HEAD, "--")

    def test_available_base_is_excluded_without_ancestry_requirement(self):
        with patch.object(policy, "resolve_commit", side_effect=lambda repo, rev: rev) as resolve, \
             patch.object(policy, "git", return_value=result(HEAD + "\n")) as mocked:
            self.assertEqual(policy.select_commits(ROOT, BASE, HEAD), [HEAD])
        self.assertEqual([call.args[1] for call in resolve.call_args_list], [HEAD, BASE])
        mocked.assert_called_once_with(ROOT, "rev-list", "--reverse", "--topo-order", HEAD,
                                       "--not", BASE, "--")

    def test_push_missing_before_checks_reachable_after_with_explicit_notice(self):
        def resolve(repo, revision):
            if revision == BASE:
                raise policy.PolicyError("missing before object")
            return revision
        with patch.object(policy, "resolve_commit", side_effect=resolve), \
             patch.object(policy, "git", return_value=result(HEAD + "\n")) as mocked, \
             redirect_stderr(StringIO()) as err:
            self.assertEqual(policy.select_commits(ROOT, BASE, HEAD, allow_missing_base=True), [HEAD])
        mocked.assert_called_once_with(ROOT, "rev-list", "--reverse", "--topo-order", HEAD, "--")
        self.assertIn("checking all commits reachable", err.getvalue())
        self.assertIn(BASE, err.getvalue())

    def test_missing_explicit_base_fails_without_rev_list(self):
        def resolve(repo, revision):
            if revision == BASE:
                raise policy.PolicyError("missing base object")
            return revision
        with patch.object(policy, "resolve_commit", side_effect=resolve), \
             patch.object(policy, "git") as mocked:
            with self.assertRaisesRegex(policy.PolicyError, "missing base"):
                policy.select_commits(ROOT, BASE, HEAD)
        mocked.assert_not_called()

    def test_missing_head_still_fails_for_push(self):
        with patch.object(policy, "resolve_commit", side_effect=policy.PolicyError("missing head")), \
             patch.object(policy, "git") as mocked:
            with self.assertRaisesRegex(policy.PolicyError, "missing head"):
                policy.select_commits(ROOT, BASE, HEAD, allow_missing_base=True)
        mocked.assert_not_called()

    def test_revision_resolution_uses_end_of_options(self):
        with patch.object(policy, "git", return_value=result(HEAD + "\n")) as mocked:
            self.assertEqual(policy.resolve_commit(ROOT, "--malicious-option"), HEAD)
        mocked.assert_called_once_with(ROOT, "rev-parse", "--verify", "--end-of-options",
                                       "--malicious-option^{commit}")

    def test_range_failure_and_success_use_same_message_validator(self):
        outputs = [result(BASE + "\0fix: valid\n"),
                   result(BASE + "\0Expose a nonconforming result\n"),
                   result(BASE + " " + HEAD + "\0Merge branch 'topic'\n")]
        with patch.object(policy, "select_commits", return_value=["a", "b", "c"]), \
             patch.object(policy, "git", side_effect=outputs), \
             redirect_stdout(StringIO()) as out, redirect_stderr(StringIO()) as err:
            self.assertEqual(policy.check_range(ROOT, BASE, HEAD), 1)
        self.assertIn("3 commit(s); 1 invalid", out.getvalue())
        self.assertIn("FAIL b", err.getvalue())

    def test_parentless_commit_has_no_message_exemption(self):
        for message, expected in (("chore: initial commit", 0), ("Initial import", 1)):
            with self.subTest(message=message), \
                 patch.object(policy, "select_commits", return_value=[HEAD]), \
                 patch.object(policy, "git", return_value=result("\0" + message + "\n")), \
                 redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(policy.check_range(ROOT, None, HEAD), expected)

    def test_known_empty_range_succeeds(self):
        with patch.object(policy, "select_commits", return_value=[]), \
             patch.object(policy, "git") as mocked, redirect_stdout(StringIO()) as out:
            self.assertEqual(policy.check_range(ROOT, BASE, HEAD), 0)
        mocked.assert_not_called()
        self.assertIn("0 commit(s)", out.getvalue())


class HookAndEntryTests(unittest.TestCase):
    def test_push_entry_allows_missing_before_but_pr_requires_its_base(self):
        events = (("push", {"before": BASE, "after": HEAD}, True),
                  ("pull_request", {"pull_request": {"base": {"sha": BASE},
                                                     "head": {"sha": HEAD}}}, False))
        with tempfile.TemporaryDirectory() as directory:
            event_path = Path(directory) / "event.json"
            for name, event, allow_missing in events:
                with self.subTest(event=name):
                    event_path.write_text(json.dumps(event), encoding="utf-8")
                    with patch.object(policy, "check_range", return_value=0) as mocked:
                        self.assertEqual(policy.main(["--event-file", str(event_path),
                                                      "--event-name", name]), 0)
                    mocked.assert_called_once_with(ROOT, BASE, HEAD, allow_missing_base=allow_missing)

    def test_local_no_base_checks_head_reachable_history(self):
        with patch.object(policy, "check_range", return_value=0) as mocked:
            self.assertEqual(policy.main([]), 0)
        mocked.assert_called_once_with(ROOT, None, "HEAD")

    def test_missing_event_objects_fail_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "push.json"
            event.write_text("{}", encoding="utf-8")
            with patch.object(policy, "check_range") as mocked, redirect_stderr(StringIO()) as err:
                self.assertEqual(policy.main(["--event-file", str(event), "--event-name", "push"]), 2)
            mocked.assert_not_called()
            self.assertIn("Commit check could not run", err.getvalue())

    def test_unreadable_push_history_is_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "push.json"
            event.write_text(json.dumps({"before": BASE, "after": HEAD}), encoding="utf-8")
            with patch.object(policy, "check_range", side_effect=policy.PolicyError("unreadable head")), \
                 redirect_stderr(StringIO()) as err:
                self.assertEqual(policy.main(["--event-file", str(event), "--event-name", "push"]), 2)
            self.assertIn("unreadable head", err.getvalue())

    def test_entry_modes_cannot_silently_ignore_a_range(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            policy.main(["--message-file", "message", "--base", BASE])


if __name__ == "__main__":
    unittest.main()
