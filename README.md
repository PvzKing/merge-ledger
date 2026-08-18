## Merge Ledger

**Review tools tell you what's wrong with a pull request. Merge Ledger tells you what happened after it merged.**

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)
![Tests](https://img.shields.io/badge/tests-46%20passing-brightgreen)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

> **Early release.** Thresholds are seeded from published industry baselines, not
> calibrated against production repositories yet. Reports from real repos are the
> most useful thing you can contribute.

Writing code got cheap. Verifying it didn't. Merge Ledger reads your git history
and answers the question no review tool asks: **which code didn't survive contact
with reality, and how long did it wait at the door?**

| Metric | What it means |
|---|---|
| **Rewritten early** | Share of new lines already rewritten within 14 days. Measures code that shipped before it was understood. |
| **Copy vs. move** | Lines that duplicate an existing block, against lines relocated to consolidate. Above 1× means the team is copying faster than it is tidying. |
| **Cloned code** | Share of the current codebase sitting inside a block that appears more than once. |
| **Error masking** | Constructs that swallow failures silently, per 1000 lines. |

Plus two things that make those numbers mean something:

- **Direction** — every scan is recorded, so the next report says *"11%, up from 9.8% a month ago"* instead of just *"11%"*.
- **Review queue** — how many PRs are waiting, how long until a human first
  touches them, and whether agent-authored PRs wait longer. Optional, via GitHub.

No service, no account, no telemetry. Just `git` and Python 3.10+.

---

## Install

```bash
git clone https://github.com/PvzKing/merge-ledger.git
cd merge-ledger
pip install -e .
```

Or run it without installing:

```bash
python3 -m mergeledger /path/to/repo
```

## Use

```bash
# last 90 days
mergeledger .

# longer window, also emit JSON
mergeledger ~/work/backend --days 180 --json result.json

# large repo: skip the whole-tree duplication scan
mergeledger . --no-head-scan

# fully offline
mergeledger . --no-github
```

The output is a **single self-contained HTML file** — no CDN, no external fonts,
no network requests at all. Attach it to an email, keep it as a CI artifact, or
open it on someone else's laptop on a plane.

Terminal summary:

```
backend · main · 90 days · 412 commits

  !  rewritten early        11.3%   warning
  !! copy vs. move           2.40×  attention
  ·  cloned code             6.8%   ok
  !  error masking      3.10/kloc   warning
  !  review queue           34 PRs  warning
  !  time to merge        2.5 days  warning
  !  time to first review 19.4 hrs  warning

  vs. 30 days ago:
     Rewritten early            9.8 -> 11.3%  (+15%) worse
     Time to first review      12.1 -> 19.4h  (+60%) worse
```

> **Shallow clones won't work.** `git blame` needs full history to date a line.
> Run `git fetch --unshallow` first.

---

## Direction

Every scan appends to `.merge-ledger/history.json` inside the repository. The
file is small, plain JSON, and **should be committed** so the whole team reads
the same baseline.

Two guards keep the trend honest:

- Comparisons only happen between scans with the **same branch, window, and churn
  threshold**. Otherwise "improvement" can appear purely because someone changed
  a flag.
- Changes under **5% relative** are marked *unchanged*, not up or down. Without
  this, every scan looks like it moved when it's just noise.

Disable with `--no-history`, relocate with `--history <path>`.

## Review queue

Git history knows what happened to code once it landed. It has no idea how long
that code waited at the door — and by the 2026 numbers, that's where the jam is.
This section pulls it from GitHub.

```bash
export GITHUB_TOKEN=ghp_xxx      # or pass --token
mergeledger .
```

| Without a token | With a token |
|---|---|
| Open PR count, how many are stale (>14 days), the oldest | Everything on the left |
| Time from open to merge | **Time until the first reviewer touches it** |
| Acceptance rate | How many PRs merged with zero reviews |
| Agent-PR vs. human-PR wait comparison | |

First-reviewer timing needs one API call per PR, so it only runs with a token —
unauthenticated GitHub caps you at 60 calls per hour.

**This section can never fail the scan.** No network, wrong token, private repo,
non-GitHub remote — the report still ships, with the reason stated plainly.
Disable with `--no-github`.

## CI gate

Every threshold is opt-in. With no flags, the tool only reports.

```bash
mergeledger . --max-churn 15 --max-duplication 20 --max-stale-prs 10 --quiet
```

Exits 1 when breached. A ready-made GitHub Actions workflow is in
`examples/merge-ledger.yml`.

> **Don't gate in week one.** Run in report-only mode for a month to learn your
> team's own baseline, then set thresholds slightly above it. Numbers borrowed
> from an industry average are almost always wrong for a specific repository.

---

## How the numbers are computed

**Rewritten early (churn).** For every deleted or modified line, the tool asks
`git blame` how old that line was. Under the threshold (14 days by default), it
counts. The denominator is all new lines in the window. Churn is charged to the
line's **original author**, not whoever removed it — if an agent's code is
rewritten by a human three days later, that's the agent's churn.

**Copy-paste.** Within a single commit, a block of five or more lines appearing
more than once among the added lines counts as a copy. The first occurrence is
treated as the original.

**Moves.** Lines that disappear from one file and reappear in another within the
same commit. This is consolidation — the healthy signal.

**Cloned code.** Unlike the three above, this scans the *current* state: what
share of lines sit inside a five-line block that repeats across the repository.
The headline figure **excludes test files** — repetition in tests is usually
deliberate and harmless. On `expressjs/express`, including tests reports 23.4%;
excluding them, 1.4%. The combined figure is still shown for reference.

**Error masking.** Detection is context-aware. A bare `pass` doesn't count —
only a `pass` directly following a catch block. Static-analysis suppressions
(`# noqa`, `@ts-ignore`, `// nolint`) go in a separate bucket, because muting a
warning is a different act from swallowing a failure.

**Excluded from everything.** Third-party code, build output, lockfiles,
migrations, and machine-generated files (see `filters.py`). Test files are
counted, but their share is reported separately.

**Time window.** `git log --since` filters on commit date, while line age comes
from author date. In patch-based workflows these can differ by months, so the
tool casts a wider net and filters on author date itself.

## Marking AI-written code

The AI-vs-human comparison only works if there's a trail. The tool recognises
`Co-Authored-By` trailers from Claude, Copilot, Cursor, Devin, Aider, Codex, and
Gemini, plus author names carrying bot markers.

For your own convention:

```bash
mergeledger . --ai-pattern 'assisted-by:\s*agent' --ai-pattern '\[ai\]'
```

With no markers at all, the report still runs — the numbers just cover all code
without distinction, and the report says so explicitly instead of quietly
showing zero.

---

## What this tool can't do

The most important section here.

**It reads history, not intent.** High churn can mean rushed work — or a team
experimenting healthily in a space whose shape isn't clear yet. A prototype
rewritten five times in a week is a sign of a *working* process. These numbers
are conversation material, not a verdict.

**There are deliberately no per-person metrics.** Every aggregation stops at the
area and file level. The file table shows the *number* of authors, never their
names. This is a product decision, not a technical limit: rankable developer
metrics get gamed, and rightly so.

**Duplicate detection is lexical.** Textually identical blocks are caught; the
same logic written differently is not.

**Blame can't cross undetected renames.** If a file is moved and heavily edited
at once, part of its history breaks and churn reads lower than reality.

**Agent-PR markers are a guess.** Detection reads branch names, labels, titles,
and author names. A team that marks nothing gets zero — which is honest: not
"there's no agent code," but "there's no trail."

**Default thresholds are a starting point, not truth.** They all live in
`scoring.py`, in one place, deliberately easy to change.

---

## Layout

```
mergeledger/
  gitio.py      git command wrappers and output parsing
  filters.py    which files count, and who wrote a commit
  metrics.py    churn, duplication, moves, masking
  scoring.py    per-area risk score — every threshold lives here
  history.py    scan history and comparison between runs
  github.py     review queue — optional, fails quietly
  report.py     self-contained HTML report and JSON output
  cli.py        command line interface
tests/
  test_metrics.py   git computations against synthetic repos
  test_flow.py      review queue and history, no network
```

Git calls run in parallel per batch of commits. On a four-core machine, a
1500-commit repository finishes in tens of seconds. Tune with `--workers`; on a
single core it falls back to serial, since splitting work there only adds
overhead.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

46 tests, none touching the network. The git tests build small repositories
whose contents are designed so the answer can be worked out by hand — 10 churned
lines, 8 moved, 6 copied — then assert the tool reports exactly that. The review
queue tests use synthetic PR data with fixed timings, including the failure
paths: rate limiting, private repos, and no connection.

## Contributing

Bug reports from real repositories are the most valuable thing right now,
especially where the numbers look obviously wrong for your codebase — that's how
the thresholds get calibrated.

New behaviour needs a test. The suite is fast and offline; please keep it that
way.

## License

MIT.
