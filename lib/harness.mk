# harness.mk — the harness Make targets, shared by every project through the
# harness/ symlink (a new target here reaches all projects with no edit).
#
# The project Makefile includes it LAST (so its own first target stays the
# default goal) and owns the toolchain targets this file calls:
#   verify   lint + type + test, the "all green" gate (required)
#   env-check  toolchain presence, used by harness/check.sh (required)
# A target is defined in exactly one of the two files: never redefine these here.
.PHONY: plan status status-export check issue issue-start issue-new

# Regenerate the derived feature_list.json from docs/plan/ (source of truth).
plan:
	python3 harness/sync_plan.py

# The whole plan at a glance: done / in progress / pending, gates, next step.
status:
	@python3 harness/plan_status.py

# Read-only static status view (issue #47): gates, attempts, timings, evidence
# links, optional token cost. Writes only to stdout or --out; never a gate.
# e.g. make status-export STATUS_ARGS="--out status.html --transcripts DIR"
status-export:
	@python3 -B harness/_status_export.py $(STATUS_ARGS)

# Single entry point for agent and hooks: toolchain + harness invariants.
# Decisions (gates, close, PR) run it only through harness/run_check.sh (issue
# #64), which sets HARNESS_RUN_CHECK; a direct `make check` says it decides nothing.
# Both halves always run (-k keeps verify going past a red step), so one run
# reports every failure; the result is red if either half is red.
# Serialized per repo (issue #52): the Stop hook and the gates must never build
# the same tree at once. A flock on harness-check.lock in the git dir (outside
# the work tree) makes a second run wait its turn, up to
# HARNESS_CHECK_LOCK_TIMEOUT seconds. The lock holder re-enters `make check`
# with HARNESS_CHECK_LOCK_HELD=1, which runs the body without locking again.
# No -o: the lock fd is inherited, so it stays held while any child of the check
# still runs, even if the flock process itself dies.
HARNESS_CHECK_LOCK_TIMEOUT ?= 1800

check:
	@if [ -z "$${HARNESS_RUN_CHECK:-}" ] && [ "$${HARNESS_CHECK_LOCK_HELD:-}" != 1 ]; then \
	  echo "make check: convenience run, it reports but decides nothing; gates, close and PRs use harness/run_check.sh" >&2; \
	fi; \
	if [ "$${HARNESS_CHECK_LOCK_HELD:-}" = 1 ]; then \
	  rc=0; \
	  $(MAKE) --no-print-directory -k verify || rc=1; \
	  ./harness/check.sh || rc=1; \
	  exit $$rc; \
	fi; \
	if ! lock="$$(git rev-parse --git-path harness-check.lock 2>/dev/null)"; then \
	  echo "make check: not a git repository; running unlocked (no gate or hook runs here)" >&2; \
	  HARNESS_CHECK_LOCK_HELD=1 $(MAKE) --no-print-directory check; exit $$?; \
	fi; \
	command -v flock >/dev/null 2>&1 || { \
	  echo "make check: flock not found; it serializes concurrent checks (install util-linux)" >&2; exit 2; }; \
	HARNESS_CHECK_LOCK_HELD=1 flock -E 75 -w "$(HARNESS_CHECK_LOCK_TIMEOUT)" "$$lock" \
	  $(MAKE) --no-print-directory check; rc=$$?; \
	if [ $$rc -eq 75 ]; then \
	  echo "make check: timed out after $(HARNESS_CHECK_LOCK_TIMEOUT)s waiting for $$lock (another check is running; HARNESS_CHECK_LOCK_TIMEOUT sets the wait)" >&2; \
	fi; \
	exit $$rc

# Issues lane (GitHub): queue an issue, claim and start it, draft a new one.
#   make issue N=14 [PRIORITY=now|next|backlog]   make issue-start N=14   make issue-new
PRIORITY ?= next

issue:
	@[ -n "$(N)" ] || { echo "make issue: N=<issue number> is required" >&2; exit 2; }
	harness/issue.sh queue $(N) $(PRIORITY)

issue-start:
	@[ -n "$(N)" ] || { echo "make issue-start: N=<issue number> is required" >&2; exit 2; }
	harness/issue.sh start $(N)

issue-new:
	harness/issue.sh new

# Doctor check of Claude Code's native Bash sandbox (I3c, WARN phase: it never
# fails). A prerequisite-only rule: env-check's recipe stays the project
# Makefile's, this only runs the check before it. The target is named through a
# variable so install.sh --migrate (_upgrade._blocks, which reads the rules
# here as harness-owned recipes) never takes env-check for a harness target.
.PHONY: sandbox-check
HARNESS_ENV_CHECK := env-check
$(HARNESS_ENV_CHECK): sandbox-check

sandbox-check:
	@python3 -B harness/_sandbox_check.py || true
