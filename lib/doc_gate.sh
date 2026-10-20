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
# Used by check.sh and (via make check) the Stop hook. exit 0 = ok; 1 = missing.
set -uo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$PROJECT_DIR" || exit 0

ANCHORS="concept planning execution problems files"
rc=0

while read -r ID; do
  [ -z "$ID" ] && continue
  # Match docs/reference/<ID>-*.md (and tolerate a bare <ID>.md).
  DOC="$(ls docs/reference/"${ID}"-*.md docs/reference/"${ID}".md 2>/dev/null | head -1)"
  if [ -z "$DOC" ] || [ ! -f "$DOC" ]; then
    echo "[FAIL]  leaf ${ID} is 'done' but has no docs/reference/${ID}-<name>.md"
    rc=1
    continue
  fi
  # Frontmatter keys (within a leading --- ... --- block, checked loosely).
  for key in "id:" "date:" "alias:" "tags:"; do
    if ! grep -qE "^${key}" "$DOC"; then
      echo "[FAIL]  ${DOC}: missing frontmatter key '${key%:}'"
      rc=1
    fi
  done
  # Required section anchors.
  for a in $ANCHORS; do
    if ! grep -qF "<!-- section: ${a} -->" "$DOC"; then
      echo "[FAIL]  ${DOC}: missing section anchor '<!-- section: ${a} -->'"
      rc=1
    fi
  done
done < <(python3 "$HARNESS_DIR/_harness.py" done-ids 2>/dev/null)

[ $rc -eq 0 ] && echo "[OK]    reference docs complete (every 'done' documented)"
exit $rc
