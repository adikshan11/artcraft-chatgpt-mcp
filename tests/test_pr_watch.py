# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import pr_watch  # noqa: E402


def row(**changes):
    base = {
        "pr": "Storytold/x#1", "title": "A fix", "head": "abc12345", "risk": "Minimal", "docstrings": 100.0,
        "threads": 0, "conflicts": False, "failing": [], "waiting_for_approval": [], "changes_requested_by": [],
        "last_person": None,
    }
    return {**base, **changes}


class WatchTests(unittest.TestCase):
    def test_a_clean_pull_request_needs_nothing(self):
        self.assertEqual(pr_watch.needs_attention(row()), [])

    def test_each_problem_is_named(self):
        reasons = pr_watch.needs_attention(row(
            risk="Moderate", docstrings=80.0, threads=2, conflicts=True, failing=["gate"], changes_requested_by=["reviewer"],
            waiting_for_approval=["CI"],
        ))
        self.assertEqual(len(reasons), 7)
        self.assertIn("CI waiting for a maintainer to approve it", reasons)
        self.assertIn("CodeRabbit rates it Moderate", reasons)
        self.assertIn("changes requested by reviewer", reasons)

    def test_an_unrated_pull_request_is_not_called_a_problem(self):
        self.assertEqual(pr_watch.needs_attention(row(risk="unrated", docstrings=None)), [])

    def test_the_same_state_gives_the_same_fingerprint_and_a_change_does_not(self):
        same = pr_watch.fingerprint([row()])
        self.assertEqual(same, pr_watch.fingerprint([row()]))
        self.assertNotEqual(same, pr_watch.fingerprint([row(head="def67890")]))
        self.assertNotEqual(same, pr_watch.fingerprint([row(last_person=("someone", "COMMENTED", "2026-11-14T00:00:00Z"))]))
        # A pull request that closes drops out of the rows, which is the change.
        self.assertNotEqual(pr_watch.fingerprint([row(), row(pr="Storytold/x#2")]), same)

    def test_the_saved_state_survives_the_round_trip_through_an_issue_body(self):
        body = f'text\n\n{pr_watch.MARKER}{{"prs": ["a#1"], "hash": "ff"}} -->'
        self.assertEqual(pr_watch.saved_state(body), '{"prs": ["a#1"], "hash": "ff"}')
        self.assertEqual(pr_watch.saved_state("nothing here"), "")

    def test_the_comment_names_the_person_and_what_is_finished(self):
        text = pr_watch.render([row(threads=1)], ["Storytold/y#9"], "someone")
        self.assertTrue(text.startswith("@someone "))
        self.assertIn("Storytold/y#9", text)
        self.assertIn("1 open review thread(s)", text)

    def test_a_later_request_for_changes_beats_an_earlier_approval_and_a_dismissal_clears_it(self):
        def review(login, state, at):
            return {"user": {"login": login}, "state": state, "submitted_at": at}

        decisions = pr_watch.current_decisions([
            review("a", "CHANGES_REQUESTED", "2026-11-14T03"), review("a", "APPROVED", "2026-11-14T01"),
            review("b", "APPROVED", "2026-11-14T01"), review("b", "COMMENTED", "2026-11-14T02"),
            review("c", "CHANGES_REQUESTED", "2026-11-14T01"), review("c", "DISMISSED", "2026-11-14T02"),
        ])
        self.assertEqual(decisions, {"a": "CHANGES_REQUESTED", "b": "APPROVED"})

    def test_timed_out_and_startup_failures_count_as_failing(self):
        for conclusion in ("failure", "timed_out", "startup_failure"):
            self.assertIn(conclusion, pr_watch.FAILED)
        self.assertNotIn("action_required", pr_watch.FAILED)

    def test_review_threads_are_counted_over_every_page(self):
        pages = [
            {"data": {"repository": {"pullRequest": {"reviewThreads": {
                "pageInfo": {"hasNextPage": True, "endCursor": "c1"}, "nodes": [{"isResolved": True}] * 100}}}}},
            {"data": {"repository": {"pullRequest": {"reviewThreads": {
                "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [{"isResolved": False}]}}}}},
        ]
        calls = []

        def fake_gh(*args, **kwargs):
            calls.append(args)
            return json.dumps(pages[len(calls) - 1])

        with mock.patch.object(pr_watch, "gh", fake_gh):
            self.assertEqual(pr_watch.open_threads("o", "r", 1), 1)
        self.assertIn("after=c1", calls[1])

    def test_check_runs_are_combined_across_pages(self):
        out = json.dumps([{"check_runs": [{"name": "a"}]}, {"check_runs": [{"name": "b"}]}])
        with mock.patch.object(pr_watch, "gh", lambda *a, **k: out):
            self.assertEqual([r["name"] for r in pr_watch.check_runs("o", "r", "sha")], ["a", "b"])

    def test_the_comment_is_posted_before_the_saved_state_changes(self):
        order = []

        def fake_gh(*args, **kwargs):
            if args[:2] == ("search", "prs"):
                return "[]"
            if args[:2] == ("issue", "list"):
                return json.dumps([{"number": 5, "title": "Craft PR watch", "body": '<!-- pr-watch:{"prs": ["o/r#1"], "hash": "old"} -->'}])
            order.append(args[:2])
            return ""

        with mock.patch.object(pr_watch, "gh", fake_gh):
            pr_watch.main(["me", "--repo", "o/r"])
        self.assertEqual(order, [("issue", "comment"), ("issue", "edit")])

    def test_a_failed_comment_leaves_the_saved_state_alone_so_the_next_run_tries_again(self):
        def fake_gh(*args, **kwargs):
            if args[:2] == ("search", "prs"):
                return "[]"
            if args[:2] == ("issue", "list"):
                return json.dumps([{"number": 5, "title": "Craft PR watch", "body": '<!-- pr-watch:{"prs": ["o/r#1"], "hash": "old"} -->'}])
            if args[:2] == ("issue", "comment"):
                raise RuntimeError("down")
            raise AssertionError("the state must not be written")

        with mock.patch.object(pr_watch, "gh", fake_gh), self.assertRaises(RuntimeError):
            pr_watch.main(["me", "--repo", "o/r"])

    def test_bots_are_not_people(self):
        for login in ("coderabbitai[bot]", "sonarqubecloud", "github-actions[bot]", "autocraft-bot[bot]"):
            self.assertTrue(pr_watch.BOTS.search(login), login)
        self.assertFalse(pr_watch.BOTS.search("austin62jake"))


if __name__ == "__main__":
    unittest.main()
