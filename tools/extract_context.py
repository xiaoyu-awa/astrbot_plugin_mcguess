#!/usr/bin/env python3
"""Extract version-pinned natural sources and creative tabs from Mojang game data.

This is a maintainer tool, not a runtime dependency. ``--creative`` is the TSV
export of CreativeModeTabs with operator permissions and default feature flags.
Dimensions mean natural block placement/entity spawning or direct item sources;
they do not mean where a player may carry/place/craft an object. Recipe ingredients
are deliberately not traced. See mcdle_raw/README.md for scope and special cases.
"""

from __future__ import annotations

import argparse
import collections
import copy
import gzip
import hashlib
import json
import struct
import zipfile
from pathlib import Path
from typing import Any

DIMENSIONS = ("overworld", "nether", "end")


class NbtReader:
    """Small big-endian NBT reader for the trusted vanilla structure templates."""

    def __init__(self, data: bytes):
        self.data = gzip.decompress(data) if data.startswith(b"\x1f\x8b") else data
        self.offset = 0

    def take(self, count: int) -> bytes:
        value = self.data[self.offset:self.offset + count]
        if len(value) != count:
            raise ValueError("Truncated structure NBT")
        self.offset += count
        return value

    def number(self, fmt: str) -> Any:
        return struct.unpack(">" + fmt, self.take(struct.calcsize(fmt)))[0]

    def text(self) -> str:
        return self.take(self.number("H")).decode("utf-8")

    def payload(self, kind: int) -> Any:
        if kind in range(1, 7):
            return self.number({1: "b", 2: "h", 3: "i", 4: "q", 5: "f", 6: "d"}[kind])
        if kind == 8:
            return self.text()
        if kind == 9:
            subtype, count = self.number("B"), self.number("i")
            return [self.payload(subtype) for _ in range(count)]
        if kind == 10:
            value = {}
            while subtype := self.number("B"):
                name = self.text()
                value[name] = self.payload(subtype)
            return value
        if kind in (7, 11, 12):
            self.take(self.number("i") * {7: 1, 11: 4, 12: 8}[kind])
            return None
        raise ValueError(f"Unsupported NBT tag: {kind}")

    def read(self) -> Any:
        kind = self.number("B")
        self.text()
        return self.payload(kind)


def walk(value: Any):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def plain(value: str) -> str:
    return value.removeprefix("minecraft:")


def extract(client: Path, creative: Path) -> dict[str, Any]:
    registry = json.loads((Path(__file__).resolve().parent / "mcdle_raw/registry_1_21_9.json").read_text(encoding="utf-8"))
    digest = hashlib.sha1(client.read_bytes()).hexdigest()
    if digest != registry["mojang_client_sha1"]:
        raise ValueError("The client JAR does not match the pinned Java 1.21.9 SHA-1")
    rows = [line.split("\t") for line in creative.read_text(encoding="utf-8").splitlines()]
    nether_replacements = {plain(key): plain(value) for kind, key, value in rows if kind == "R"}
    if not nether_replacements:
        raise ValueError("The creative export must include the BlackstoneReplaceProcessor replacements (R rows)")
    with zipfile.ZipFile(client) as archive:
        names = set(archive.namelist())

        def group(prefix):
            base = "data/minecraft/" + prefix + "/"
            return {name[len(base):-5]: json.loads(archive.read(name)) for name in names if name.startswith(base) and name.endswith(".json")}

        biomes = group("worldgen/biome")
        biome_tags = group("tags/worldgen/biome")
        features = group("worldgen/configured_feature")
        placed = group("worldgen/placed_feature")
        structures = group("worldgen/structure")
        pools = group("worldgen/template_pool")
        processors = group("worldgen/processor_list")
        tables = group("loot_table")
        recipes = group("recipe")
        block_sources = collections.defaultdict(set)
        entity_sources = collections.defaultdict(set)
        table_sources = collections.defaultdict(set)
        item_sources = collections.defaultdict(set)

        def biome_members(value, seen=frozenset()):
            if isinstance(value, list):
                return set().union(*(biome_members(v, seen) for v in value)) if value else set()
            if not str(value).startswith("#"):
                return {plain(str(value))}
            key = plain(value[1:])
            if key in seen:
                raise ValueError("Cyclic biome tag")
            return biome_members(biome_tags.get(key, {}).get("values", []), seen | {key})

        biome_dimensions = collections.defaultdict(set)
        for dim in DIMENSIONS:
            for biome in biome_members("#is_" + ("end" if dim == "end" else dim)):
                biome_dimensions[biome].add(dim)

        template_cache = {}

        def template(key):
            key = plain(key)
            if key not in template_cache:
                path = f"data/minecraft/structure/{key}.nbt"
                template_cache[key] = NbtReader(archive.read(path)).read() if path in names else {}
            return template_cache[key]

        def record(value, dims):
            """Record explicit block states, natural entities and linked loot tables."""
            for node in walk(value):
                if not isinstance(node, dict):
                    continue
                if isinstance(node.get("Name"), str):
                    key = plain(node["Name"])
                    if key not in {"jigsaw", "structure_block", "structure_void"}:
                        block_sources[key].update(dims)
                if isinstance(node.get("id"), str) and node["id"].startswith("minecraft:"):
                    entity_sources[plain(node["id"])].update(dims)
                if isinstance(node.get("LootTable"), str):
                    table_sources[plain(node["LootTable"])].update(dims)
                if isinstance(node.get("final_state"), str):
                    block_sources[plain(node["final_state"].split("[")[0])].update(dims)

        def visit_feature(value, dims, seen=None):
            seen = set() if seen is None else seen
            if isinstance(value, str):
                key = plain(value)
                if key in seen:
                    return
                seen.add(key)
                if key in placed:
                    visit_feature(placed[key], dims, seen)
                if key in features:
                    visit_feature(features[key], dims, seen)
                data = template(key)
                if data:
                    record(data, dims)
                return
            record(value, dims)
            if isinstance(value, (list, dict)):
                for node in walk(value):
                    if isinstance(node, str) and node.startswith("minecraft:"):
                        visit_feature(node, dims, seen)
            if isinstance(value, dict):
                kind = plain(value.get("type", ""))
                fixed = {"end_spike": ["obsidian", "bedrock", "iron_bars", "fire"], "end_platform": ["obsidian"], "end_island": ["end_stone"], "end_gateway": ["bedrock", "end_gateway"], "blue_ice": ["blue_ice"], "iceberg": ["packed_ice", "blue_ice", "snow_block"], "freeze_top_layer": ["ice", "snow"], "basalt_columns": ["basalt"], "basalt_pillar": ["basalt"], "delta_feature": ["basalt", "magma_block", "lava"], "glowstone_blob": ["glowstone"], "desert_well": ["sandstone", "sandstone_slab", "water", "sand", "suspicious_sand"]}
                for block in fixed.get(kind, []):
                    block_sources[block].update(dims)

        for biome, data in biomes.items():
            dims = biome_dimensions[biome]
            if not dims:
                continue
            for values in data.get("spawners", {}).values():
                for value in values:
                    entity_sources[plain(value["type"])].update(dims)
            visit_feature(data.get("features", []), dims)

        for setting, dim in [("overworld", "overworld"), ("nether", "nether"), ("end", "end")]:
            data = json.loads(archive.read(f"data/minecraft/worldgen/noise_settings/{setting}.json"))
            record(data.get("default_block"), {dim})
            record(data.get("default_fluid"), {dim})
            record(data.get("surface_rule"), {dim})

        def visit_pool(key, dims, seen=None):
            seen = set() if seen is None else seen
            key = plain(key)
            if key in seen or key not in pools:
                return
            seen.add(key)
            pool = pools[key]
            visit_pool(pool.get("fallback", "empty"), dims, seen)
            for node in walk(pool):
                if not isinstance(node, dict):
                    continue
                if isinstance(node.get("processors"), str):
                    record(processors.get(plain(node["processors"]), {}), dims)
                if isinstance(node.get("location"), str):
                    data = template(node["location"])
                    record(data, dims)
                    for part in walk(data):
                        if isinstance(part, dict) and isinstance(part.get("pool"), str):
                            visit_pool(part["pool"], dims, seen)
                if "feature" in node:
                    visit_feature(node["feature"], dims)

        template_prefix = {"end_city": "end_city/", "igloo": "igloo/", "shipwreck": "shipwreck/", "ocean_ruin_cold": "underwater_ruin/", "ocean_ruin_warm": "underwater_ruin/", "nether_fossil": "nether_fossils/"}
        for key, data in structures.items():
            members = biome_members(data.get("biomes", []))
            dims = set().union(*(biome_dimensions[biome] for biome in members)) if members else set()
            if not dims:
                continue
            for override in data.get("spawn_overrides", {}).values():
                for value in override.get("spawns", []):
                    entity_sources[plain(value["type"])].update(dims)
            if data.get("start_pool"):
                visit_pool(data["start_pool"], dims)
            prefix = "ruined_portal/" if key.startswith("ruined_portal") else template_prefix.get(key)
            if prefix:
                for path in sorted(names):
                    if path.startswith("data/minecraft/structure/" + prefix) and path.endswith(".nbt"):
                        data = template(path[len("data/minecraft/structure/"):-4])
                        for dim in dims:
                            current = data
                            if prefix == "ruined_portal/" and dim == "nether":
                                current = copy.deepcopy(data)
                                replacements = nether_replacements
                                for node in walk(current):
                                    if isinstance(node, dict) and plain(str(node.get("Name"))) in replacements:
                                        node["Name"] = "minecraft:" + replacements[plain(node["Name"])]
                            record(current, {dim})

        # Procedural pieces and runtime natural spawning are implemented in Java,
        # not in a structure NBT palette or biome's normal spawn list.
        procedural_blocks = {
            "overworld": "air cave_air water lava cobweb oak_planks oak_fence oak_log rail powered_rail torch wall_torch spawner chest stone_bricks mossy_stone_bricks cracked_stone_bricks infested_stone_bricks stone_brick_slab stone_brick_stairs iron_bars iron_door oak_door bookshelf end_portal_frame cobblestone cobblestone_stairs mossy_cobblestone cobblestone_wall redstone_wire tripwire tripwire_hook sticky_piston piston dispenser lever spruce_planks crafting_table furnace cauldron brown_mushroom potted_red_mushroom spruce_stairs glass_pane white_carpet light_gray_carpet sandstone sandstone_stairs sandstone_slab chiseled_sandstone cut_sandstone orange_terracotta blue_terracotta stone_pressure_plate tnt suspicious_sand prismarine prismarine_bricks dark_prismarine sea_lantern wet_sponge gold_block",
            "nether": "air cave_air lava netherrack nether_bricks nether_brick_fence nether_brick_stairs nether_wart soul_sand spawner chest bone_block",
            "end": "air cave_air end_stone obsidian bedrock iron_bars fire dragon_egg end_portal wall_torch",
        }
        for dim, values in procedural_blocks.items():
            for key in values.split():
                block_sources[key].add(dim)
        # Several feature/decorator implementations choose Blocks constants or
        # coral tags internally rather than serializing a block-state provider.
        feature_blocks = {
            "overworld": "bamboo bamboo_sapling bee_nest cocoa kelp kelp_plant seagrass tall_seagrass dripstone_block pointed_dripstone glow_lichen sculk sculk_vein sculk_catalyst sculk_sensor sculk_shrieker",
            "nether": "weeping_vines weeping_vines_plant twisting_vines twisting_vines_plant dried_ghast",
        }
        for dim, values in feature_blocks.items():
            for key in values.split():
                block_sources[key].add(dim)
        for color in ("brain", "bubble", "fire", "horn", "tube"):
            for suffix in ("coral", "coral_block", "coral_fan", "coral_wall_fan"):
                block_sources[f"{color}_{suffix}"].add("overworld")
        natural_entities = {
            "overworld": "witch villager zombie_villager cave_spider elder_guardian guardian silverfish evoker vindicator vex illusioner pillager ravager wandering_trader trader_llama phantom warden breeze bogged zombie skeleton spider husk drowned stray slime skeleton_horse iron_golem cat tadpole chicken bee snow_golem lightning_bolt chest_minecart painting item_frame armor_stand item experience_orb falling_block arrow trident breeze_wind_charge evoker_fangs fishing_bobber creaking",
            "nether": "blaze wither_skeleton skeleton ghast magma_cube piglin piglin_brute hoglin zombified_piglin strider enderman item experience_orb falling_block arrow fireball small_fireball",
            "end": "ender_dragon enderman shulker end_crystal shulker_bullet item_frame item experience_orb dragon_fireball area_effect_cloud",
        }
        # Illusioners do not spawn naturally in Java 1.21.9; snow golems and fishing
        # bobbers require player action. They remain in the answer pool, with none.
        for dim, values in natural_entities.items():
            for key in values.split():
                if key not in {"illusioner", "snow_golem", "fishing_bobber"}:
                    entity_sources[key].add(dim)

        def loot_items(key, seen=frozenset()):
            key = plain(key)
            if key in seen:
                return set()
            data = tables.get(key, {})
            result = set()
            for node in walk(data):
                if not isinstance(node, dict):
                    continue
                if node.get("type") == "minecraft:item":
                    result.add(plain(node["name"]))
                elif node.get("type") == "minecraft:loot_table" and isinstance(node.get("value"), str):
                    result.update(loot_items(node["value"], seen | {key}))
            if any(isinstance(node, dict) and node.get("function") == "minecraft:furnace_smelt" for node in walk(data)):
                for recipe in recipes.values():
                    if recipe.get("type") == "minecraft:smelting" and plain(str(recipe.get("ingredient", ""))) in result:
                        cooked = recipe.get("result", {})
                        if isinstance(cooked, dict):
                            result.add(plain(cooked["id"]))
            return result

        for key, dims in block_sources.items():
            table_sources["blocks/" + key].update(dims)
        for key, dims in entity_sources.items():
            table_sources["entities/" + key].update(dims)
            if key == "sheep":
                for table in tables:
                    if table.startswith("entities/sheep/"):
                        table_sources[table].update(dims)
        for key in tables:
            if key.startswith("chests/"):
                dim = "end" if "end_city" in key else "nether" if any(x in key for x in ("bastion", "nether_bridge", "ruined_portal")) else "overworld"
                table_sources[key].add(dim)
                if "ruined_portal" in key:
                    table_sources[key].add("overworld")
            elif key.startswith("archaeology/"):
                table_sources[key].add("overworld")
            elif key.startswith("gameplay/"):
                if "piglin_bartering" in key:
                    table_sources[key].add("nether")
                elif any(x in key for x in ("fishing", "cat_morning_gift", "hero_of_the_village", "sniffer_digging", "panda_sneeze", "chicken_lay")):
                    table_sources[key].add("overworld")
        for key, dims in table_sources.items():
            for item in loot_items(key):
                item_sources[item].update(dims)
        for key in ("milk_bucket", "honey_bottle", "honeycomb", "tadpole_bucket", "axolotl_bucket", "cod_bucket", "salmon_bucket", "pufferfish_bucket", "tropical_fish_bucket", "water_bucket", "powder_snow_bucket"):
            item_sources[key].add("overworld")
        item_sources["lava_bucket"].update({"overworld", "nether"})
        item_sources["dragon_egg"].add("end")
        item_sources["dragon_breath"].add("end")
        item_sources["goat_horn"].add("overworld")
        item_sources["scute"].add("overworld")
        item_sources["turtle_scute"].add("overworld")
        item_sources["armadillo_scute"].add("overworld")

    tabs = {}
    block_items = {}
    for kind, key, values in rows:
        if kind == "T":
            key = plain(key)
            if key not in {"search", "hotbar", "inventory"}:
                tabs[key] = [plain(item) for item in values.split(",") if item]
        elif kind == "B":
            block_items[plain(key)] = plain(values)

    if set(block_items) != set(registry["registry_block_ids"]):
        raise ValueError("The creative export must contain every registered block item form")
    if not set().union(*(set(values) for values in tabs.values())) <= set(registry["registry_item_ids"]):
        raise ValueError("The creative export contains an item outside Java 1.21.9")

    def cleaned(mapping, kind):
        allowed = set(registry[f"registry_{kind}_ids"])
        return {key: [dim for dim in DIMENSIONS if dim in values] for key, values in sorted(mapping.items()) if values and key in allowed}

    return {
        "schema_version": 1,
        "game_version": "1.21.9",
        "client_sha1": digest,
        "creative_tsv_sha256": hashlib.sha256(creative.read_bytes()).hexdigest(),
        "dimension_policy": "Natural block generation/entity spawning and direct item harvesting, loot or drops; no recipe-ingredient propagation, player transport or command placement. none means no natural/direct source recorded; it does not prevent answering or obtaining by crafting, spawning, commands or transformations.",
        "creative_policy": "Default Java 1.21.9 feature flags with operator permissions; real category tabs only (no search/hotbar/inventory); block tabs use Block.asItem(), entities have not_applicable.",
        "dimensions": {"block": cleaned(block_sources, "block"), "item": cleaned(item_sources, "item"), "mob": cleaned(entity_sources, "entity_type")},
        "nether_portal_replacements": dict(sorted(nether_replacements.items())),
        "creative_tabs": tabs,
        "block_items": dict(sorted(block_items.items())),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", required=True, type=Path)
    parser.add_argument("--creative", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = extract(args.client, args.creative)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("Natural source records:", {key: len(values) for key, values in result["dimensions"].items()})
    print("Creative tab item counts:", {key: len(values) for key, values in result["creative_tabs"].items()})


if __name__ == "__main__":
    main()
