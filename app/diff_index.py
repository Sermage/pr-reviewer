"""Parse a unified diff into the set of lines that can host inline comments.

GitHub rejects an entire review (HTTP 422) if any inline comment points at a
line that isn't part of the diff. To stay safe we pre-compute, per file, the
new-file line numbers that are valid RIGHT-side anchors: added ('+') and
context (' ') lines. Only issues landing on those get posted inline; the rest
fall back into the review body.
"""
from __future__ import annotations

import re

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)")


def commentable_lines(diff: str) -> dict[str, set[int]]:
    """Map file path -> set of new-file line numbers commentable on the RIGHT side."""
    result: dict[str, set[int]] = {}
    path: str | None = None
    new_line = 0

    for line in diff.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            if target == "/dev/null":
                path = None  # deleted file: nothing to comment on
            else:
                path = target[2:] if target.startswith("b/") else target
                result.setdefault(path, set())
            continue
        if line.startswith("--- "):
            continue  # old-file header, not removed content
        if line.startswith("@@"):
            m = _HUNK.match(line)
            new_line = int(m.group(1)) if m else 0
            continue
        if path is None:
            continue
        if line.startswith("+"):
            result[path].add(new_line)
            new_line += 1
        elif line.startswith(" "):
            result[path].add(new_line)
            new_line += 1
        elif line.startswith("-"):
            pass  # removed line: does not advance the new-file counter

    return result
