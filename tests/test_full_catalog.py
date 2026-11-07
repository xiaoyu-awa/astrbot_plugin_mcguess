"""Full registry coverage and playable administrator/technical answers."""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from astrbot_plugin_mcguess import core, game_service, llm_tool
from astrbot_plugin_mcguess.catalog import Catalogue

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "tools" / "mcdle_raw"


@pytest.fixture(scope="module")
def real_catalogue():
    return Catalogue.load(ROOT / "data" / "catalog.json")


@pytest.fixture(scope="module")
def builder():
    spec = importlib.util.spec_from_file_location("full_catalog_builder", ROOT / "tools" / "build_catalog.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "category,registry_kind,count,concept_key,total",
    [
        ("block", "block", 1166, "preserved_block_concept_ids", 1204),
        ("item", "item", 1488, "preserved_concept_ids", 1509),
        ("mob", "entity_type", 153, "preserved_entity_concept_ids", 153),
    ],
)
def test_every_registry_entry_and_concept_is_present_and_playable(
    real_catalogue, category, registry_kind, count, concept_key, total,
):
    manifest = json.loads((RAW / "registry_1_21_9.json").read_text(encoding="utf-8"))
    assert manifest["answer_policy"] == "all_entries"
    expected = set(manifest[f"registry_{registry_kind}_ids"])
    assert manifest[f"registry_{registry_kind}_count"] == len(expected) == count
    expected.update(manifest[concept_key])
    actual = real_catalogue.category(category)
    raw = json.loads((RAW / (category + ".json")).read_text(encoding="utf-8"))
    assert len(raw) == len(actual.entries) == total
    assert set(actual.by_id) == {row["id"] for row in raw} == expected
    assert {row["id"] for row in actual.answer_pool("hard")} == expected
    assert all(row["answerable"] for row in actual.entries)


def test_every_id_and_name_resolves_and_fits_input_limits(real_catalogue):
    for category in real_catalogue.categories.values():
        for entry in category.entries:
            for name in [entry["id"], entry["name"], entry["name_en"], *entry["aliases"]]:
                assert category.match_exact(name)["id"] == entry["id"], (category.key, name)
                assert len(name) <= llm_tool.MAX_NAME_LENGTH, name
                assert llm_tool.parse_args({"action": "猜", "name": name}).name == name


@pytest.mark.parametrize("category,missing_id", [("block", "water"), ("item", "debug_stick"), ("mob", "marker")])
def test_each_registry_guard_detects_missing_technical_entries(builder, category, missing_id):
    raw = json.loads((RAW / (category + ".json")).read_text(encoding="utf-8"))
    with pytest.raises(builder.BuildError, match=missing_id):
        builder.validate_registry_coverage([row for row in raw if row["id"] != missing_id], RAW, category)


def test_all_answer_policy_cannot_silently_restore_admin_exclusions(builder, monkeypatch):
    real_load = builder.load_json
    settings = real_load(RAW / "curation.json")
    settings["block"]["unanswerable"] = ["barrier"]

    def load(path):
        return settings if path.name == "curation.json" else real_load(path)

    monkeypatch.setattr(builder, "load_json", load)
    with pytest.raises(builder.BuildError, match="barrier"):
        builder.build_payload(RAW)


@pytest.mark.parametrize(
    "category,answer_id",
    [("block", "barrier"), ("block", "water"), ("item", "debug_stick"), ("item", "creeper_spawn_egg"),
     ("mob", "giant"), ("mob", "marker"), ("mob", "mannequin"), ("mob", "player")],
)
def test_admin_and_technical_answers_can_be_drawn_and_won(real_catalogue, tmp_dir, monkeypatch, category, answer_id):
    def choose(pool):
        return next(entry for entry in pool if entry["id"] == answer_id)

    monkeypatch.setattr(game_service.random, "choice", choose)

    async def scenario():
        service = game_service.GameService(tmp_dir)
        state = await service.start("admin-answer", category, "hard", real_catalogue)
        assert state["answerId"] == answer_id
        guess = real_catalogue.entry(category, answer_id)
        updated, row, answer = await service.guess("admin-answer", guess, real_catalogue)
        assert updated["status"] == "won" and row["correct"] is True
        assert answer["id"] == answer_id

    asyncio.run(scenario())


def test_non_living_health_and_non_item_stacks_have_clear_display(real_catalogue):
    mob = real_catalogue.category("mob")
    block = real_catalogue.category("block")
    assert core.format_value(mob.field("health"), mob.entry("marker")["attrs"]["health"]) == "不适用"
    assert core.format_value(block.field("stackable"), block.entry("water")["attrs"]["stackable"]) == "无物品形式"
    assert mob.entry("player")["attrs"]["health"] == 20


def test_player_and_fishing_bobber_are_registered_without_false_summon_methods(real_catalogue):
    mob = real_catalogue.category("mob")
    assert mob.entry("player")["attrs"]["spawn"] == ["player_login"]
    assert mob.entry("fishing_bobber")["attrs"]["spawn"] == ["fishing"]
