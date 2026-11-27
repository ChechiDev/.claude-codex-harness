#!/usr/bin/env bash
# Documentation gate: every 'done' leaf must have a COMPLETE reference doc at
# docs/reference/<N.M.K>-<name>.md — frontmatter (id, date, alias, tags) plus the
# five required sections, marked with stable anchors so the check is independent
# of heading wording/language:
#   <!-- section: concept -->    didactic, professor-style explanation
#   <!-- section: planning -->   how it was planned
#   <!-- section: execution -->  how it was executed
#   <!-- section: problems -->   problems found and how they were solved
#   <!-- section: files -->      files created/modified
# Used by check.sh and (via make check) the Stop hook. exit 0 = ok; 1 = missing, or a helper failed.
#   doc_gate.sh                 every 'done' leaf
#   doc_gate.sh --leaf <id>     only that leaf, by the same rule (issue #133: the close
#                               step checks its own leaf before it commits); exit 2 = usage
set -uo pipefail

ONLY=""
if [ "${1:-}" = "--leaf" ]; then
  ONLY="${2:-}"
  if ! [[ "$ONLY" =~ ^([0-9]+\.[0-9]+\.[0-9]+|ISS-[0-9]+\.[0-9]+)$ ]] || [ "$#" -ne 2 ]; then
    echo "usage: doc_gate.sh [--leaf <N.M.K|ISS-n.k>]" >&2; exit 2
  fi
elif [ "$#" -ne 0 ]; then
  echo "usage: doc_gate.sh [--leaf <N.M.K|ISS-n.k>]" >&2; exit 2
fi

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
. "$HARNESS_DIR/_colors.sh"
cd "$PROJECT_DIR" || { fail "cannot enter the project directory $PROJECT_DIR"; exit 1; }
# The done leaves, or FAIL: a helper that cannot answer must not empty the list (I10).
# No temp file: the helper's own error goes straight to stderr, above this FAIL.
if ! IDS="$(python3 "$HARNESS_DIR/_harness.py" done-ids)"; then
  fail "cannot read the done leaves (the helper's error is above)"
  exit 1
fi

ANCHORS="concept planning execution problems files"
rc=0

while read -r ID; do
  [ -z "$ID" ] && continue
  [ -z "$ONLY" ] || [ "$ID" = "$ONLY" ] || continue
  # Match docs/reference/<ID>-*.md (and tolerate a bare <ID>.md); first in name order.
  DOC=""
  for candidate in docs/reference/"${ID}"-*.md docs/reference/"${ID}".md; do
    if [ -f "$candidate" ]; then DOC="$candidate"; break; fi
  done
  if [ -z "$DOC" ]; then
    fail "leaf ${ID} is 'done' but has no docs/reference/${ID}-<name>.md"
    rc=1
    continue
  fi
  if [ ! -r "$DOC" ]; then
    fail "cannot read ${DOC}"
    rc=1
    continue
  fi
  # Frontmatter keys (within a leading --- ... --- block, checked loosely).
  for key in "id:" "date:" "alias:" "tags:"; do
    if ! grep -qE "^${key}" "$DOC"; then
      fail "${DOC}: missing frontmatter key '${key%:}'"
      rc=1
    fi
  done
  # Required section anchors.
  for a in $ANCHORS; do
    if ! grep -qF "<!-- section: ${a} -->" "$DOC"; then
      fail "${DOC}: missing section anchor '<!-- section: ${a} -->'"
      rc=1
    fi
  done
done <<< "$IDS"

if [ $rc -eq 0 ]; then
  if [ -n "$ONLY" ]; then ok "reference doc of $ONLY complete (or not required: not done)"
  else ok "reference docs complete (every 'done' documented)"; fi
fi
exit $rc
