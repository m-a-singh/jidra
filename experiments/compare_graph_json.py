#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def make_key(obj: dict[str, Any]) -> tuple[str, str]:
    name = str(obj.get("name", "")).strip()
    qn = str(obj.get("qualified_name", "")).strip()
    return name, qn


def load_jsonl_index(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {e}") from e

            if not isinstance(obj, dict):
                continue

            key = make_key(obj)
            if key == ("", ""):
                continue

            index[key] = obj
    return index


def shallow_diff(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}

    all_keys = set(a.keys()) | set(b.keys())
    for k in sorted(all_keys):
        if a.get(k) != b.get(k):
            changes[k] = {
                "old": a.get(k),
                "new": b.get(k),
            }
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diff two JSONL files using (name, qualified_name) as key."
    )
    parser.add_argument("old_file", type=Path)
    parser.add_argument("new_file", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write diff JSON to this file instead of stdout.",
    )
    args = parser.parse_args()

    old_index = load_jsonl_index(args.old_file)
    new_index = load_jsonl_index(args.new_file)

    old_keys = set(old_index.keys())
    new_keys = set(new_index.keys())

    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    common = sorted(old_keys & new_keys)

    changed = []
    for key in common:
        old_obj = old_index[key]
        new_obj = new_index[key]
        diffs = shallow_diff(old_obj, new_obj)
        if diffs:
            changed.append(
                {
                    "key": {"name": key[0], "qualified_name": key[1]},
                    "changes": diffs,
                }
            )

    result = {
        "summary": {
            "old_count": len(old_index),
            "new_count": len(new_index),
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "unchanged": len(common) - len(changed),
        },
        "added": [{"name": n, "qualified_name": q} for n, q in added],
        "removed": [{"name": n, "qualified_name": q} for n, q in removed],
        "changed": changed,
    }

    output_text = json.dumps(result, indent=2, ensure_ascii=False)

    if args.output:
        args.output.write_text(output_text + "\n", encoding="utf-8")
    else:
        print(output_text)


if __name__ == "__main__":
    main()
