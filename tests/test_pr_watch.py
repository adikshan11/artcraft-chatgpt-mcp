# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import pr_watch  # noqa: E402


def row(**changes):
    base = {
        "pr": "Storytold/x#1", "title": "A fix", "head": "abc12345", "risk": "Minimal", "docstrings": 100.0,
        "threads": 0, "conflicts": False, "failing": [], "changes_requested_by": [], "last_person": None,
    }
    return {**base, **changes}


class WatchTests(unittest.TestCase):
    def test_a_clean_pull_request_needs_nothing(self):
        self.assertEqual(pr_watch.needs_attention(row()), [])

    def test_each_problem_is_named(self):
        reasons = pr_watch.needs_attention(row(
            risk="Moderate", docstrings=80.0, threads=2, conflicts=True, failing=["gate"], changes_requested_by=["reviewer"],
        ))
        self.assertEqual(len(reasons), 6)
        self.assertIn("CodeRabbit rates it Moderate", reasons)
        self.assertIn("changes requested by reviewer", reasons)

    def test_an_unrated_pull_request_is_not_called_a_problem(self):
        self.assertEqual(pr_watch.needs_attention(row(risk="unrated", docstrings=None)), [])

    def test_the_same_state_gives_the_same_fingerprint_and_a_change_does_not(self):
        same = pr_watch.fingerprint([row()], [])
        self.assertEqual(same, pr_watch.fingerprint([row()], []))
        self.assertNotEqual(same, pr_watch.fingerprint([row(head="def67890")], []))
        self.assertNotEqual(same, pr_watch.fingerprint([row(last_person=("someone", "COMMENTED", "2026-11-14T00:00:00Z"))], []))
        self.assertNotEqual(same, pr_watch.fingerprint([row()], ["Storytold/x#2"]))

    def test_the_saved_state_survives_the_round_trip_through_an_issue_body(self):
        body = f'text\n\n{pr_watch.MARKER}{{"prs": ["a#1"], "hash": "ff"}} -->'
        self.assertEqual(pr_watch.saved_state(body), '{"prs": ["a#1"], "hash": "ff"}')
        self.assertEqual(pr_watch.saved_state("nothing here"), "")

    def test_the_comment_names_the_person_and_what_is_finished(self):
        text = pr_watch.render([row(threads=1)], ["Storytold/y#9"], "someone")
        self.assertTrue(text.startswith("@someone "))
        self.assertIn("Storytold/y#9", text)
        self.assertIn("1 open review thread(s)", text)

    def test_bots_are_not_people(self):
        for login in ("coderabbitai[bot]", "sonarqubecloud", "github-actions[bot]", "autocraft-bot[bot]"):
            self.assertTrue(pr_watch.BOTS.search(login), login)
        self.assertFalse(pr_watch.BOTS.search("austin62jake"))


if __name__ == "__main__":
    unittest.main()
