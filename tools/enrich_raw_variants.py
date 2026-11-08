#!/usr/bin/env python3
"""Apply broad variant labels from a pinned, auditable vanilla family snapshot.

Normal use is fully offline. Maintainers may rebuild the snapshot with --client
and the official 1.21.9 client; the game binary is never a runtime dependency.
Only color/type labels reach the catalogue, never family names or member counts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

RAW = Path(__file__).resolve().parent / "mcdle_raw"
SNAPSHOT = "variant_families_1_21_9.json"
LABELS = {"color": "颜色", "type": "品种", "none": "无"}
COLORS = "white orange magenta light_blue yellow lime pink gray light_gray cyan purple blue brown green red black".split()
COLOR_FAMILIES = (
    "wool", "carpet", "terracotta", "glazed_terracotta", "concrete", "concrete_powder",
    "stained_glass", "stained_glass_pane", "bed", "banner", "wall_banner",
    "shulker_box", "candle", "candle_cake", "dye", "bundle", "harness",
)
# Explicitly chosen series, not every tag or every shared ID prefix. Gameplay
# tags such as mineable/pickaxe do not by themselves imply a variant family.
BLOCK_TYPE_TAGS = (
    "buttons", "chains", "doors", "fences", "fence_gates", "leaves", "logs",
    "planks", "pressure_plates", "saplings", "wooden_shelves", "all_signs",
    "slabs", "stairs", "walls", "trapdoors", "coral_blocks", "coral_plants",
    "corals", "wall_corals", "small_flowers", "nylium", "stone_bricks",
    "sand", "ice", "rails", "campfires", "lanterns", "bars",
    "coal_ores", "copper_ores", "diamond_ores", "emerald_ores", "gold_ores",
    "iron_ores", "lapis_ores", "redstone_ores", "copper_chests",
)
ITEM_TYPE_TAGS = (
    "axes", "pickaxes", "hoes", "shovels", "swords", "head_armor", "chest_armor",
    "leg_armor", "foot_armor", "boats", "chest_boats", "skulls", "eggs",
    "decorated_pot_sherds", "arrows",
)
# Fixed vanilla gameplay variants are included even if they share a registry ID.
# Arbitrary NBT/components, names, poses, ages, health and oxidation states are
# not classified as variant families merely because their values can change.
BUILTIN_MOB_TYPES = (
    "axolotl", "fox", "goat", "horse", "llama", "trader_llama", "mooshroom",
    "panda", "parrot", "rabbit", "tropical_fish", "villager", "zombie_villager",
)
BUILTIN_MOB_COLORS = ("axolotl", "cat", "wolf", "horse", "llama", "trader_llama", "parrot", "rabbit", "sheep", "shulker", "tropical_fish")
EXTRA_FAMILIES = {
    "fungus": ("crimson_fungus", "warped_fungus"),
    "mushroom": ("red_mushroom", "brown_mushroom"),
    "mushroom_block": ("red_mushroom_block", "brown_mushroom_block"),
    "roots": ("crimson_roots", "warped_roots", "hanging_roots", "mangrove_roots", "muddy_mangrove_roots"),
    "flower": ("sunflower", "lilac", "rose_bush", "peony", "pitcher_plant"),
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def extract_snapshot(client: Path, raw: Path = RAW):
    registry = read_json(raw / "registry_1_21_9.json")
    expected = registry["mojang_client_sha1"]
    if hashlib.sha1(client.read_bytes()).hexdigest() != expected:
        raise ValueError("Client SHA-1 does not match the pinned Java 1.21.9 version")
    ids = {category: set(registry[f"registry_{kind}_ids"]) for category, kind in
           [("block", "block"), ("item", "item"), ("mob", "entity_type")]}
    context = read_json(raw / "context_1_21_9.json")
    groups = []

    def add(category, key, label, members, evidence):
        members = sorted(set(members) & ids[category])
        if members:
            groups.append({"category": category, "family": key, "label": label,
                           "members": members, "evidence": evidence})

    with ZipFile(client) as archive:
        def tag(kind, name, seen=()):
            if name in seen:
                raise ValueError(f"Cyclic vanilla tag: {kind}/{name}")
            values = json.loads(archive.read(f"data/minecraft/tags/{kind}/{name}.json"))["values"]
            result = set()
            for value in values:
                value = value["id"] if isinstance(value, dict) else value
                if value.startswith("#minecraft:"):
                    result.update(tag(kind, value.split(":", 1)[1], (*seen, name)))
                elif value.startswith("minecraft:"):
                    result.add(value.split(":", 1)[1])
            return result

        for family in COLOR_FAMILIES:
            colored = {f"{color}_{family}" for color in COLORS}
            base = {family}
            if family == "stained_glass":
                base.add("glass")
            elif family == "stained_glass_pane":
                base.add("glass_pane")
            for category in ("block", "item"):
                if len(colored & ids[category]) == 16:
                    add(category, family, "color", colored | base, "complete_vanilla_dye_palette")
        for category in ("block", "item"):
            add(category, "tulip", "color", {f"{c}_tulip" for c in ("red", "orange", "white", "pink")}, "vanilla_tulip_palette")
            for family, members in EXTRA_FAMILIES.items():
                if len(set(members) & ids[category]) > 1:
                    add(category, family, "type", members, "curated_vanilla_family")
            add(category, "froglight", "color", {f"{c}_froglight" for c in ("ochre", "verdant", "pearlescent")}, "vanilla_froglight_palette")
        for name in BLOCK_TYPE_TAGS:
            members = tag("block", name)
            if len(members) > 1:
                add("block", name, "type", members, f"vanilla_tag:block/{name}")
                item_members = {context["block_items"].get(member, "air") for member in members} - {"air"}
                if len(item_members) > 1:
                    add("item", name, "type", item_members, f"block_item_forms:{name}")
        for name in ITEM_TYPE_TAGS:
            members = tag("item", name)
            if len(members) > 1:
                add("item", name, "type", members, f"vanilla_tag:item/{name}")
        skull_items = tag("item", "skulls")
        skull_blocks = {block for block, item in context["block_items"].items() if item in skull_items}
        add("block", "head", "type", skull_blocks, "block_item_forms:item/skulls")
        add("item", "dyeable", "color", tag("item", "dyeable"), "vanilla_tag:item/dyeable")
        for suffix, key in [("_helmet", "helmet"), ("_horse_armor", "horse_armor")]:
            add("item", key, "type", {item for item in ids["item"] if item.endswith(suffix)}, "curated_equipment_family")
        for prefix in ("music_disc_", "pottery_sherd_", "spawn_egg_"):
            # Most families use a suffix instead; keep matching explicit.
            key = prefix.rstrip("_")
            members = {item for item in ids["item"] if item.startswith(prefix) or item.endswith("_" + key)}
            add("item", key, "type", members, "registered_vanilla_series")
        for key in ("banner_pattern", "armor_trim_smithing_template"):
            members = {item for item in ids["item"] if item.endswith("_" + key)}
            add("item", key, "type", members, "registered_vanilla_series")
        for item in ("potion", "splash_potion", "lingering_potion", "tipped_arrow", "goat_horn", "painting"):
            add("item", item, "type", {item}, "fixed_vanilla_component_variants")
        for category in ("item", "mob"):
            for key in ("boat", "chest_boat"):
                members = {item for item in ids[category] if item.endswith("_" + key)}
                members.add("bamboo_raft" if key == "boat" else "bamboo_chest_raft")
                add(category, key, "type", members, "registered_vanilla_vehicle_family")
        for name in archive.namelist():
            # Variant registries such as cat_variant and wolf_variant are direct
            # evidence of within-entity varieties, not separate answer entries.
            parts = name.split("/")
            if len(parts) == 4 and parts[:2] == ["data", "minecraft"] and parts[2].endswith("_variant"):
                entity = parts[2][:-len("_variant")]
                if entity in ids["mob"]:
                    add("mob", entity, "type", {entity}, f"vanilla_registry:{parts[2]}")
        for entity in BUILTIN_MOB_TYPES:
            add("mob", entity, "type", {entity}, "vanilla_builtin_variants")
        for entity in BUILTIN_MOB_COLORS:
            add("mob", entity, "color", {entity}, "vanilla_color_or_dye_variants")
    # One variant registry has multiple JSON members; retain one audit record.
    groups = list({json.dumps(group, sort_keys=True): group for group in groups}.values())
    return {"schema_version": 1, "game_version": "1.21.9", "client_sha1": expected,
            "labels": LABELS, "groups": sorted(groups, key=lambda g: (g["category"], g["family"], g["label"], g["evidence"]))}


def enrich(raw: Path = RAW):
    snapshot = read_json(raw / SNAPSHOT)
    if snapshot.get("game_version") != "1.21.9" or snapshot.get("labels") != LABELS:
        raise ValueError("Unexpected variant snapshot version or labels")
    context = read_json(raw / "context_1_21_9.json")
    for category in ("block", "item", "mob"):
        records = read_json(raw / f"{category}.json")
        labels_by_id = {}
        for group in snapshot["groups"]:
            if group["category"] == category:
                if group["label"] not in {"color", "type"}:
                    raise ValueError("Unknown variant label")
                for key in group["members"]:
                    labels_by_id.setdefault(key, set()).add(group["label"])
        concepts = context["concept_members"][category]
        for row in records:
            key = row["id"]
            members = concepts.get(key, [key])
            labels = set().union(*(labels_by_id.get(member, set()) for member in members))
            row["variants"] = sorted(labels) or ["none"]
        save_json(raw / f"{category}.json", records)
        print(category, len(records), "broad variant labels updated")
    values = read_json(raw / "value_map.json")
    values["variants"] = LABELS
    save_json(raw / "value_map.json", values)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", type=Path, help="Rebuild the pinned family snapshot with an official client JAR")
    args = parser.parse_args()
    if args.client:
        save_json(RAW / SNAPSHOT, extract_snapshot(args.client))
    enrich()


if __name__ == "__main__":
    main()
