"""Issue #61, items 1 and 2: _upgrade.py rule parsing and _clean_env.sh."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from types import ModuleType

import pytest
from conftest import TOOLKIT


def _upgrade() -> ModuleType:
    sys.path.insert(0, str(TOOLKIT / "lib"))
    spec = importlib.util.spec_from_file_location("_upgrade_61", TOOLKIT / "lib/_upgrade.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _check_block(up: ModuleType) -> list[str]:
    return list(up._harness_recipes()["check"])


def test_unchanged_copied_check_migrates() -> None:
    up = _upgrade()
    text = "all:\n\techo hi\n\n" + "\n".join(_check_block(up)) + "\n"
    out = up.migrated_makefile(text)
    assert out is not None and "\techo hi" in out and up.INCLUDE in out


def test_duplicate_check_rule_with_custom_first_is_refused() -> None:
    up = _upgrade()
    check = "\n".join(_check_block(up))
    text = f"check:\n\t./my-custom-check.sh\n\n{check}\n"
    with pytest.raises(up.MigrationError, match="check: customised"):
        up.migrated_makefile(text)


def test_duplicate_identical_check_rules_are_all_dropped() -> None:
    up = _upgrade()
    check = "\n".join(_check_block(up))
    out = up.migrated_makefile(f"{check}\n\n{check}\n")
    assert out is not None and "\n".join(_check_block(up)[1:3]) not in out


def test_space_after_line_continuation_is_a_customisation() -> None:
    up = _upgrade()
    lines = _check_block(up)
    i = next(k for k, line in enumerate(lines) if line.endswith("\\"))
    lines[i] += " "
    with pytest.raises(up.MigrationError, match="check: customised"):
        up.migrated_makefile("\n".join(lines) + "\n")


def test_trailing_space_elsewhere_is_still_shared() -> None:
    up = _upgrade()
    lines = _check_block(up)
    lines[-1] += "  "
    assert up.migrated_makefile("\n".join(lines) + "\n") is not None


def test_legacy_recipe_match_stays_exact() -> None:
    up = _upgrade()
    legacy = list(up.LEGACY_RECIPES["check"][0])
    assert up.migrated_makefile("\n".join(legacy) + "\n") is not None
    legacy[2] = legacy[2].replace("-k ", "")
    with pytest.raises(up.MigrationError):
        up.migrated_makefile("\n".join(legacy) + "\n")


def test_clean_env_strips_inherited_check_lock_held() -> None:
    script = (
        f'source "{TOOLKIT}/lib/_clean_env.sh"; '
        '"${CLEAN_ENV[@]}" sh -c \'echo "held=${HARNESS_CHECK_LOCK_HELD-unset}"\''
    )
    out = subprocess.run(
        ["bash", "-c", script],
        env={"PATH": "/usr/bin:/bin", "HARNESS_CHECK_LOCK_HELD": "1"},
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert out.strip() == "held=unset"
