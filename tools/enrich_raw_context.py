#!/usr/bin/env python3
"""Apply the pinned context snapshot to all raw entries without changing answers."""

from __future__ import annotations

import json
from pathlib import Path

RAW = Path(__file__).resolve().parent / "mcdle_raw"


def load(name):
    return json.loads((RAW / name).read_text(encoding="utf-8"))


def save(name, value):
    (RAW / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def concept_members(category, key, registered):
    result = {item for item in registered if item.startswith(key + "_") or item.endswith("_" + key)}
    special = {
        "aquatic_mob_bucket": {"axolotl_bucket", "cod_bucket", "salmon_bucket", "pufferfish_bucket", "tropical_fish_bucket", "tadpole_bucket"},
        "mushroom": {"brown_mushroom", "red_mushroom"},
        "nether_fungus": {"warped_fungus", "crimson_fungus"},
        "dry_grass": {"short_dry_grass", "tall_dry_grass"},
        "eyeblossom": {"open_eyeblossom", "closed_eyeblossom"},
        "head": {item for item in registered if item.endswith(("_head", "_skull"))},
        "infested_block": {item for item in registered if item.startswith("infested_")},
        "flower": {"dandelion", "poppy", "blue_orchid", "allium", "azure_bluet", "red_tulip", "orange_tulip", "white_tulip", "pink_tulip", "oxeye_daisy", "cornflower", "lily_of_the_valley", "wither_rose", "torchflower", "sunflower", "lilac", "rose_bush", "peony", "pitcher_plant", "closed_eyeblossom", "open_eyeblossom"},
    }
    result.update(special.get(key, set()) & registered)
    if category == "block" and key == "log":
        result.update(item for item in registered if item.endswith("_stem") and item.startswith(("warped", "crimson", "stripped_warped", "stripped_crimson")))
    if category == "block" and key == "wood":
        result.update(item for item in registered if item.endswith("_hyphae"))
    if category == "item" and key in {"boat", "chest_boat"}:
        result.update({"bamboo_raft" if key == "boat" else "bamboo_chest_raft"})
    return sorted(result)


def main():
    context = load("context_1_21_9.json")
    registry = load("registry_1_21_9.json")
    curated = load("curation.json")
    tabs_by_item = {}
    for tab, ids in context["creative_tabs"].items():
        canonical = "operator_utilities" if tab == "op_blocks" else tab
        for item in ids:
            tabs_by_item.setdefault(item, set()).add(canonical)
    concept_audit = {}
    for category, kind in [("block", "block"), ("item", "item"), ("mob", "entity_type")]:
        records = load(category + ".json")
        registered = set(registry[f"registry_{kind}_ids"])
        sources = context["dimensions"][category]
        concept_audit[category] = {}
        for row in records:
            key = row["id"]
            members = [key] if key in registered else concept_members(category, key, registered)
            if key not in registered:
                if not members:
                    raise ValueError(f"Unmapped concept: {category}/{key}")
                concept_audit[category][key] = members
            dims = set().union(*(set(sources.get(member, [])) for member in members))
            row["dimensions"] = [dim for dim in ("overworld", "nether", "end") if dim in dims] or ["none"]
            if category == "mob":
                row["inventory_categories"] = ["not_applicable"]
            else:
                item_ids = members if category == "item" else [context["block_items"].get(member, "air") for member in members]
                tabs = set().union(*(tabs_by_item.get(item, set()) for item in item_ids))
                row["inventory_categories"] = sorted(tabs) or ["none"]
            name = str(curated.get(category, {}).get("name_fixes", {}).get(key) or row["chinese_title"])
            row["name_length"] = sum(not char.isspace() for char in name)
        save(category + ".json", records)
        print(category, len(records), "context attributes updated")
    context["concept_members"] = concept_audit
    save("context_1_21_9.json", context)
    mapping = load("value_map.json")
    mapping["dimensions"] = {"overworld": "主世界", "nether": "下界", "end": "末地", "none": "无直接自然来源记录"}
    mapping["inventory_categories"].update({"building_blocks": "建筑方块", "colored_blocks": "染色方块", "combat": "战斗用品", "food_and_drinks": "食物与饮品", "functional_blocks": "功能方块", "ingredients": "原材料", "natural_blocks": "自然方块", "operator_utilities": "管理员用品", "redstone_blocks": "红石方块", "spawn_eggs": "刷怪蛋", "tools_and_utilities": "工具与实用物品", "none": "不在创造分类中", "not_applicable": "不适用（实体无物品栏分类）"})
    save("value_map.json", mapping)


if __name__ == "__main__":
    main()
