# harness.mk — the harness Make targets, shared by every project through the
# harness/ symlink (a new target here reaches all projects with no edit).
#
# The project Makefile includes it LAST (so its own first target stays the
# default goal) and owns the toolchain targets this file calls:
#   verify   lint + type + test, the "all green" gate (required)
#   env-check  toolchain presence, used by harness/check.sh (required)
# A target is defined in exactly one of the two files: never redefine these here.
.PHONY: plan status check issue issue-start issue-new

# Regenerate the derived feature_list.json from docs/plan/ (source of truth).
plan:
	python3 harness/sync_plan.py

# The whole plan at a glance: done / in progress / pending, gates, next step.
status:
	@python3 harness/plan_status.py

# Single entry point for agent and hooks: toolchain + harness invariants.
# Both halves always run (-k keeps verify going past a red step), so one run
# reports every failure; the result is red if either half is red.
check:
	@rc=0; \
	$(MAKE) --no-print-directory -k verify || rc=1; \
	./harness/check.sh || rc=1; \
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
