"""Broad variant metadata, disclosure boundaries and gameplay integration."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from astrbot_plugin_mcguess import card_renderer, card_views, core, hint_queries
from astrbot_plugin_mcguess.catalog import Catalogue
from astrbot_plugin_mcguess.game_service import GameService

ROOT = Path(__file__).resolve().parents[1]
LABELS = {"color": "颜色", "type": "品种", "none": "无"}


@pytest.fixture(scope="module")
def real():
    return Catalogue.load(ROOT / "data/catalog.json")


@pytest.fixture(scope="module")
def builder():
    spec = importlib.util.spec_from_file_location("variant_test_builder", ROOT / "tools/build_catalog.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_entry_uses_only_broad_nonempty_variant_labels(real):
    count = 0
    for category in real.categories.values():
        field = next(field for field in category.fields if field["key"] == "variants")
        assert field["kind"] == "set" and field["label"] == "变体"
        assert field["values"] == LABELS
        assert "variants" in core.hint_plan(category.fields, category.hint_fields)
        for entry in category.entries:
            count += 1
            labels = entry["attrs"]["variants"]
            assert labels and len(labels) == len(set(labels))
            assert set(labels) <= set(LABELS)
            assert "none" not in labels or labels == ["none"]
    assert count == 2866


@pytest.mark.parametrize(("category", "key", "labels"), [
    ("block", "wool", ["color"]), ("block", "red_wool", ["color"]),
    ("block", "concrete", ["color"]), ("block", "blue_concrete", ["color"]),
    ("block", "oak_door", ["type"]), ("block", "birch_log", ["type"]),
    ("block", "head", ["type"]), ("block", "stone", ["none"]),
    ("item", "dye", ["color"]), ("item", "red_dye", ["color"]),
    ("item", "diamond_sword", ["type"]), ("item", "leather_helmet", ["color", "type"]),
    ("item", "wolf_armor", ["color"]), ("item", "painting", ["type"]),
    ("item", "potion", ["type"]), ("item", "diamond", ["none"]),
    ("mob", "pig", ["type"]), ("mob", "sheep", ["color"]),
    ("mob", "wolf", ["color", "type"]), ("mob", "tropical_fish", ["color", "type"]),
    ("mob", "oak_boat", ["type"]), ("mob", "creeper", ["none"]),
])
def test_representative_series_and_concrete_members(real, category, key, labels):
    assert real.entry(category, key)["attrs"]["variants"] == labels


def test_palette_members_are_not_misidentified_by_color_prefixes(real):
    for category in ("block", "item"):
        assert real.entry(category, "red_mushroom")["attrs"]["variants"] == ["type"]
    assert real.entry("item", "redstone")["attrs"]["variants"] == ["none"]
    assert real.entry("mob", "copper_golem")["attrs"]["variants"] == ["none"]  # oxidation state


@pytest.mark.parametrize(("question", "kind", "matched"), [
    ("变体", "reveal", None), ("变体类型", "reveal", None), ("变种", "reveal", None),
    ("有什么变体", "reveal", None), ("变体有哪些", "reveal", None),
    ("变体 颜色", "predicate", True), ("有颜色变体吗", "predicate", True),
    ("是否有颜色变体", "predicate", True), ("有没有颜色变体", "predicate", True),
    ("有无颜色变体", "predicate", True),
    ("它有颜色变体吗", "predicate", True), ("颜色变体", "predicate", True),
    ("没有颜色变体吗", "predicate", False), ("变体 品种", "predicate", False),
    ("品种变体", "predicate", False), ("有变体吗", "predicate", True),
    ("没有变体吗", "predicate", False),
])
def test_reveal_and_predicate_hints_only_disclose_broad_labels(real, question, kind, matched):
    category = real.category("block")
    answer = category.entry("red_wool")
    result = hint_queries.build_question_hint(answer, category.fields, question)
    assert result["field"] == "variants" and result["kind"] == kind
    if matched is not None:
        assert result["matched"] is matched
    for detail in ("红色", "羊毛", "木种", "材质", "16"):
        assert detail not in result["text"]


def test_explicit_character_question_keeps_its_original_meaning(real):
    category = real.category("block")
    result = hint_queries.build_question_hint(category.entry("red_wool"), category.fields, "含有 颜色变体")
    assert result["kind"] == "contains" and result["field"] == "name_contains"


def test_variant_set_comparison_supports_exact_partial_and_miss(real):
    field = next(f for f in real.category("item").fields if f["key"] == "variants")
    assert core.compare_field(field, ["color"], ["color"])["status"] == core.EXACT
    partial = core.compare_field(field, ["color"], ["color", "type"])
    assert partial["status"] == core.CLOSE and partial["hits"] == ["颜色"]
    assert core.compare_field(field, ["color"], ["type"])["status"] == core.MISS
    assert core.compare_field(field, ["none"], ["none"])["status"] == core.EXACT


def test_variant_hints_persist_and_canonical_duplicates_are_free(real, tmp_dir):
    async def scenario():
        games = GameService(tmp_dir, hint_limit=2)
        await games.start("variants", "item", "hard", real)
        state, first, _ = await games.hint("variants", real, "变体")
        assert len(state["hints"]) == 1 and not state["guesses"]
        again, duplicate, _ = await games.hint("variants", real, "变种类型")
        assert again == state and duplicate["id"] == first["id"]
        state, predicate, _ = await games.hint("variants", real, "有颜色变体吗")
        assert len(state["hints"]) == 2 and predicate["kind"] == "predicate"
        same, _, _ = await games.hint("variants", real, "变体 颜色")
        assert same == state
        restarted = GameService(tmp_dir, hint_limit=2)
        assert await restarted.current("variants") == state
    asyncio.run(scenario())


def test_cards_and_clues_show_variant_property(real):
    category = real.category("item")
    answer, guess = category.entry("leather_helmet"), category.entry("wolf_armor")
    state, row = core.apply_guess({"guesses": [], "status": "playing"}, guess, answer, category.fields)
    state.update(category="item", difficulty="hard", hints=[])
    view = card_views.guess_view(state, category, row, guess, answer, max_attempts=25, hint_limit=3)
    field = next(item for item in view["tiles"] if item["key"] == "variants")
    assert field["value"] == "颜色" and field["status"] == core.CLOSE
    html = card_renderer.build_guess_card_html(view)
    assert "变体" in html and "颜色" in html
    assert "木种" not in html and "材质" not in html


@pytest.mark.parametrize("labels", [[], ["none", "color"], ["color", "color"], ["wood_type"], ["16"]])
def test_builder_rejects_specific_or_invalid_variant_values(builder, labels):
    row = json.loads((ROOT / "tools/mcdle_raw/block.json").read_text(encoding="utf-8"))[0]
    row["variants"] = labels
    with pytest.raises(builder.BuildError, match="只能使用颜色/品种"):
        builder.normalize_attrs("block", row)


def test_builder_rejects_specific_variant_display_names(builder):
    mapping = json.loads((ROOT / "tools/mcdle_raw/value_map.json").read_text(encoding="utf-8"))
    mapping["variants"]["type"] = "木种"
    row = json.loads((ROOT / "tools/mcdle_raw/block.json").read_text(encoding="utf-8"))[0]
    with pytest.raises(builder.BuildError, match="只能显示颜色、品种、无"):
        builder.build_fields("block", [builder.normalize_attrs("block", row)], mapping)


def test_snapshot_members_are_registered_and_application_is_idempotent(tmp_dir):
    raw = ROOT / "tools/mcdle_raw"
    spec = importlib.util.spec_from_file_location("variant_test_enricher", ROOT / "tools/enrich_raw_variants.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    snapshot = json.loads((raw / module.SNAPSHOT).read_text(encoding="utf-8"))
    registry = json.loads((raw / "registry_1_21_9.json").read_text(encoding="utf-8"))
    assert snapshot["client_sha1"] == registry["mojang_client_sha1"]
    for group in snapshot["groups"]:
        kind = {"block": "block", "item": "item", "mob": "entity_type"}[group["category"]]
        assert group["members"] and set(group["members"]) <= set(registry[f"registry_{kind}_ids"])
        assert group["label"] in {"color", "type"}
    names = ["block.json", "item.json", "mob.json", "value_map.json", "context_1_21_9.json", module.SNAPSHOT]
    before = {name: (raw / name).read_text(encoding="utf-8") for name in names}
    for name, content in before.items():
        (tmp_dir / name).write_text(content, encoding="utf-8")
    module.enrich(tmp_dir)
    assert {name: (tmp_dir / name).read_text(encoding="utf-8") for name in names} == before
