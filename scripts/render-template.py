#!/usr/bin/env python3
"""Render @KEY@ placeholders in a deployment template."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 4:
        raise SystemExit("usage: render-template.py SOURCE DEST KEY=VALUE [...]")
    source = Path(sys.argv[1])
    destination = Path(sys.argv[2])
    rendered = source.read_text(encoding="utf-8")
    for assignment in sys.argv[3:]:
        if "=" not in assignment:
            raise SystemExit(f"invalid assignment: {assignment}")
        key, value = assignment.split("=", 1)
        rendered = rendered.replace(f"@{key}@", value)
    if "@" in rendered:
        unresolved = sorted(
            {
                token.split("@", 1)[1]
                for token in rendered.split("@")[1::2]
                if token
            }
        )
        if unresolved:
            raise SystemExit(f"unresolved placeholders: {', '.join(unresolved)}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
