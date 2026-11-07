#!/usr/bin/env python3
"""Flatten vanilla JAR tag references for ExtractCreative.java (maintenance only)."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path


def resolve(client: Path) -> str:
    with zipfile.ZipFile(client) as archive:
        tags = {}
        prefix = "data/minecraft/tags/"
        for name in archive.namelist():
            if name.startswith(prefix) and name.endswith(".json"):
                path = name[len(prefix):-5]
                if path.startswith("worldgen/"):
                    _, kind, tag = path.split("/", 2)
                    kind = "worldgen/" + kind
                else:
                    kind, tag = path.split("/", 1)
                tags[kind, tag] = json.loads(archive.read(name))["values"]

    def members(kind, tag, seen=frozenset()):
        if (kind, tag) in seen:
            raise ValueError(f"Cyclic vanilla tag: {kind}/{tag}")
        values = set()
        for value in tags.get((kind, tag), []):
            if isinstance(value, dict):
                value = value["id"]
            if value.startswith("#"):
                values.update(members(kind, value[1:].removeprefix("minecraft:"), seen | {(kind, tag)}))
            else:
                values.add(value if ":" in value else "minecraft:" + value)
        return values

    return "\n".join(kind + "\t" + tag + "\t" + ",".join(sorted(members(kind, tag))) for kind, tag in sorted(tags))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("client", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(resolve(args.client), encoding="utf-8")
    print("Resolved vanilla tags:", args.output)


if __name__ == "__main__":
    main()
