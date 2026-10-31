#!/usr/bin/env bash
# The issues lane on GitHub (see _issue.py): queue | start | release | resume | new | publish.
#   harness/issue.sh queue 14 [now|next|backlog]   (make issue N=14 PRIORITY=…)
#   harness/issue.sh start 14                      (make issue-start N=14)
#   harness/issue.sh release 14
#   harness/issue.sh resume [14]
#   harness/issue.sh new                           (make issue-new)
#   harness/issue.sh publish                       (after DRAFT_APPROVED and the user's OK)
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_issue.py" "$@"
