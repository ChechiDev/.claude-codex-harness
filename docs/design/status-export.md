# Status export (issue #47)

`make status-export` writes a static, read-only view of the harness status:
for every subtask, its status (blocked included), the plan and code gate verdicts
with links to their evidence in `progress/`, the code-gate attempts and their
timings, and an optional token cost.

```
python3 harness/_status_export.py [--format html|json] [--out FILE] [--transcripts DIR]
make status-export                      # HTML to stdout
make status-export STATUS_ARGS="--format json --out status.json"
```

## Sources (read only)

| Data | Source |
|---|---|
| subtasks, status | `feature_list.json` (`make plan`) |
| plan / code gate | `progress/plan_review_<id>.md`, `progress/review_<id>.md` |
| attempts, timings | `.git/harness-gate-runs/code.<tag>_<id>.json` and `code.approved_<id>.json` (issue #39) |
| cost (optional) | local JSONL transcripts given with `--transcripts` |

Timings are the first and last attempt `created_at`. A malformed gate-run record
or undecodable gate evidence file is listed as a warning on its subtask, never
fatal. With `--out`, evidence links are relative to the page's folder.

## Guarantees

- **Read only.** It writes only to stdout or `--out`. It never writes harness
  state, is not part of `make check`, the hooks or any gate, and decides nothing.
- **Never over harness state.** `--out` is refused (exit 2) when it is a symlink,
  a hard link, inside the toolkit (also via the `harness/` symlink), inside the git dir or common dir (linked worktrees too), or resolves to `feature_list.json`, `harness.toml`, or anything under
  `progress/`, `docs/`, `harness/` or `.git/`.
- **Static view.** The HTML has no scripts, forms, buttons or event handlers, and
  a Content-Security-Policy of `default-src 'none'`. Every imported text (titles,
  log tails, paths) is HTML-escaped.

## Cost

The transcript format is undocumented and may change, so cost is best effort:
lines that are JSON objects with a string `gitBranch` and integer
`message.usage.input_tokens` / `output_tokens` are summed per branch, and each
subtask takes the total of its task (or issue) branch. Anything else is skipped.
A subtask with no matching usage shows `"unknown"`. Tokens are reported, not money.
Cost is per branch, so subtasks of the same task share the figure.
