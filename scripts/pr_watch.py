# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""Watches the Craft pull requests and says what needs a person.

Runs from GitHub Actions, so it keeps going when nobody's computer is on. It reads public pull
requests with the workflow's own token. When anything that needs attention changes since the
last run, it comments on one tracking issue, which emails the person it mentions. Nothing is
written to the pull requests themselves.

    python scripts/pr_watch.py OWNER_LOGIN --repo adikshan11/bridgecraft --issue-title "Craft PR watch"
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys

RISK = re.compile(r"Merge Risk:\*\* \*\*[^\w]*(\w+)\*\*")
DOCSTRINGS = re.compile(r"Docstring coverage is ([0-9.]+)%")
MARKER = "<!-- pr-watch:"
BOTS = re.compile(r"coderabbit|sonar|github-actions|\[bot\]|bot$", re.I)


def gh(*args, check=True):
    """Run the GitHub CLI and return its text output."""
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {result.stderr.strip()[:200]}")
    return result.stdout


def api(path):
    """GET a GitHub REST path, following pages, and return the combined list or object."""
    out = gh("api", "--paginate", "--slurp", path)
    pages = json.loads(out or "[]")
    if pages and all(isinstance(p, list) for p in pages):
        return [row for page in pages for row in page]
    return pages[0] if pages else {}


def open_threads(owner, name, number):
    """The number of review threads on a pull request that nobody has resolved."""
    query = (
        "query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n)"
        "{reviewThreads(first:100){nodes{isResolved}}}}}"
    )
    out = gh("api", "graphql", "-f", f"query={query}", "-F", f"o={owner}", "-F", f"r={name}", "-F", f"n={number}")
    nodes = json.loads(out)["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    return sum(1 for node in nodes if not node["isResolved"])


def summarize(owner, name, number, me):
    """What the person needs to know about one open pull request, as a small dictionary."""
    pr = json.loads(gh("api", f"repos/{owner}/{name}/pulls/{number}"))
    comments = api(f"repos/{owner}/{name}/issues/{number}/comments")
    reviews = api(f"repos/{owner}/{name}/pulls/{number}/reviews")
    bot_text = "\n".join(c["body"] for c in comments if "coderabbit" in c["user"]["login"].lower())
    risk = RISK.findall(bot_text)
    docs = DOCSTRINGS.findall(bot_text)
    people = [
        (item["user"]["login"], item.get("state", "COMMENTED"), item.get("submitted_at") or item["created_at"])
        for item in [*comments, *reviews]
        if item["user"]["login"] != me and not BOTS.search(item["user"]["login"])
    ]
    people.sort(key=lambda row: row[2])
    changes = {u for u, s, _ in people if s == "CHANGES_REQUESTED"} - {u for u, s, _ in people if s == "APPROVED"}
    checks = json.loads(gh("api", f"repos/{owner}/{name}/commits/{pr['head']['sha']}/check-runs") or "{}").get("check_runs", [])
    return {
        "pr": f"{owner}/{name}#{number}",
        "title": pr["title"],
        "head": pr["head"]["sha"][:8],
        "risk": risk[-1] if risk else "unrated",
        "docstrings": float(docs[-1]) if docs else None,
        "threads": open_threads(owner, name, number),
        "conflicts": pr.get("mergeable_state") == "dirty",
        "failing": sorted({c["name"] for c in checks if c.get("conclusion") == "failure"}),
        "changes_requested_by": sorted(changes),
        "last_person": people[-1] if people else None,
    }


def needs_attention(row):
    """The reasons this pull request needs a person, as short phrases."""
    reasons = []
    if row["risk"] not in ("Minimal", "unrated"):
        reasons.append(f"CodeRabbit rates it {row['risk']}")
    if row["docstrings"] is not None and row["docstrings"] < 100:
        reasons.append(f"docstring coverage {row['docstrings']:g}%")
    if row["threads"]:
        reasons.append(f"{row['threads']} open review thread(s)")
    if row["conflicts"]:
        reasons.append("conflicts with the base branch")
    if row["failing"]:
        reasons.append("failing: " + ", ".join(row["failing"]))
    if row["changes_requested_by"]:
        reasons.append("changes requested by " + ", ".join(row["changes_requested_by"]))
    return reasons


def fingerprint(rows, finished):
    """A short hash of everything worth telling the person, so an unchanged state stays quiet."""
    state = [
        (row["pr"], row["head"], row["risk"], row["threads"], row["last_person"], needs_attention(row))
        for row in rows
    ]
    return hashlib.sha256(json.dumps([state, sorted(finished)], default=list).encode()).hexdigest()[:16]


def render(rows, finished, me):
    """The comment text: what changed hands, what needs attention, and what is clean."""
    lines = [f"@{me} the Craft pull requests changed since the last check."]
    if finished:
        lines += ["", "**No longer open:** " + ", ".join(sorted(finished))]
    waiting = [(row, needs_attention(row)) for row in rows]
    lines += ["", "| Pull request | Head | Needs attention |", "|---|---|---|"]
    for row, reasons in waiting:
        who = f" (last from {row['last_person'][0]})" if row["last_person"] else ""
        lines.append(f"| {row['pr']}: {row['title'][:60]}{who} | `{row['head']}` | {'; '.join(reasons) or 'nothing'} |")
    return "\n".join(lines)


def saved_state(body):
    """The saved state hidden at the end of the tracking issue, or an empty string."""
    found = re.search(re.escape(MARKER) + r"(\{.*?\}) -->", body or "", re.S)
    return found.group(1) if found else ""


def main(argv=None):
    """Compare the open pull requests with the last run and comment on the tracking issue."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("me", help="the GitHub login whose pull requests are watched")
    parser.add_argument("--repo", required=True, help="OWNER/NAME of the repository holding the tracking issue")
    parser.add_argument("--issue-title", default="Craft PR watch")
    parser.add_argument("--org", default="Storytold")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    found = json.loads(gh("search", "prs", "--author", args.me, "--owner", args.org, "--state", "open",
                          "--limit", "100", "--json", "repository,number"))
    rows = []
    for item in found:
        owner, name = item["repository"]["nameWithOwner"].split("/")
        rows.append(summarize(owner, name, item["number"], args.me))
    rows.sort(key=lambda row: row["pr"])

    issues = json.loads(gh("issue", "list", "--repo", args.repo, "--state", "open", "--search",
                           f'"{args.issue_title}" in:title', "--json", "number,title,body"))
    issue = next((i for i in issues if i["title"] == args.issue_title), None)
    saved = saved_state(issue["body"]) if issue else ""
    previous = json.loads(saved) if saved else {"prs": [], "hash": ""}
    finished = sorted(set(previous["prs"]) - {row["pr"] for row in rows})
    digest = fingerprint(rows, finished)
    body = f"Tracks the Craft pull requests of @{args.me}. The workflow updates it; do not edit.\n\n{render(rows, [], args.me)}\n\n{MARKER}{json.dumps({'prs': [r['pr'] for r in rows], 'hash': digest})} -->"
    if args.dry_run:
        print(render(rows, finished, args.me))
        return 0
    if issue is None:
        gh("issue", "create", "--repo", args.repo, "--title", args.issue_title, "--body", body)
        return 0
    if previous["hash"] == digest:
        print("unchanged")
        return 0
    gh("issue", "edit", str(issue["number"]), "--repo", args.repo, "--body", body)
    gh("issue", "comment", str(issue["number"]), "--repo", args.repo, "--body", render(rows, finished, args.me))
    return 0


if __name__ == "__main__":
    sys.exit(main())
