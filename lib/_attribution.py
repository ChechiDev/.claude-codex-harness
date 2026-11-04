"""Agent attribution check shared by the guards and the issues lane.

Name terms match only as standalone words: a term glued to a word by a word
character or `-`, or by a `.`/`/` that continues into a word, is part of a name
or path (`codexion`, `.claude-codex-harness`, `codex.md`), not attribution.
A sentence-ending `Codex.` or `...Codex` still matches.
"""

from __future__ import annotations

import re

_NAMES = r"(?<![\w-])(?<!\w[./])(?:codex|anthropic|claude code)(?![\w-]|[./]\w)"

RE_ATTRIBUTION = re.compile(rf"co-authored-by|generated with|🤖|{_NAMES}", re.I)
