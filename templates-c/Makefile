# Makefile — C variant. Single source of truth for the commands.
# Keep the harness contract targets: lint / type / test / verify / check / plan
# (+ env-check, used by harness/check.sh). Customize freely below that.
.PHONY: lint type test verify check plan env-check clean

CC      ?= gcc
CSTD    := -std=c11
WARN    := -Wall -Wextra -Werror -pedantic
CFLAGS  := $(CSTD) $(WARN) -Iinclude $(EXTRA_CFLAGS)

SRC   := $(shell find src -name '*.c' 2>/dev/null)
OBJ   := $(SRC:.c=.o)
HDR   := $(shell find include src -name '*.h' 2>/dev/null)
TESTS := $(shell find tests -name 'test_*.c' 2>/dev/null)

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

# Lint / static analysis — optional tools, run only when installed (degrade
# gracefully so the gate still works on a bare gcc+make box).
lint:
	@if command -v clang-format >/dev/null 2>&1; then \
	  echo "clang-format --dry-run -Werror"; clang-format --dry-run -Werror $(SRC) $(HDR) $(TESTS); \
	else echo "[skip]  clang-format not installed"; fi
	@if command -v cppcheck >/dev/null 2>&1; then \
	  echo "cppcheck"; cppcheck --enable=warning,style --error-exitcode=1 --quiet -Iinclude src; \
	else echo "[skip]  cppcheck not installed"; fi
	@if command -v clang-tidy >/dev/null 2>&1 && [ -n "$(SRC)" ]; then \
	  echo "clang-tidy"; clang-tidy $(SRC) -- $(CFLAGS); \
	else echo "[skip]  clang-tidy not installed (SOLID/clean-code enforced by review)"; fi

# C has no separate type checker: a strict warning-clean compile IS the gate.
type: $(OBJ)
	@echo "[ok]    compiles clean ($(WARN))"

test: $(OBJ)
	@set -e; \
	if [ -z "$(TESTS)" ]; then echo "[ok]    no tests yet"; else \
	  for t in $(TESTS); do \
	    bin=$${t%.c}.bin; \
	    echo "  cc  $$t"; $(CC) $(CFLAGS) $$t $(OBJ) -o $$bin; \
	    echo "  run $$bin"; ./$$bin; \
	  done; \
	  echo "[ok]    tests passed"; \
	fi

verify: lint type test

plan:
	python3 harness/sync_plan.py

check: verify
	./harness/check.sh

env-check:
	@command -v $(CC) >/dev/null 2>&1 || { echo "[FAIL]  $(CC) not found"; exit 1; }
	@command -v make  >/dev/null 2>&1 || { echo "[FAIL]  make not found"; exit 1; }
	@echo "[OK]    C toolchain: $$($(CC) --version | head -1)"

clean:
	@find . \( -name '*.o' -o -name '*.bin' \) -delete
