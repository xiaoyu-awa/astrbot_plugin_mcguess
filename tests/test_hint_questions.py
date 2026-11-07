"""Expanded hint questions, name feedback, persistence and disclosure boundaries."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from astrbot_plugin_mcguess import card_views, core, hint_queries, llm_tool
from astrbot_plugin_mcguess.card_renderer import build_guess_card_html, build_status_card_html
from astrbot_plugin_mcguess.catalog import Catalogue
from astrbot_plugin_mcguess.game_service import GameService

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def real():
    return Catalogue.load(ROOT / "data/catalog.json")


def test_every_entry_has_context_and_displayed_name_length(real):
    context = json.loads((ROOT / "tools/mcdle_raw/context_1_21_9.json").read_text(encoding="utf-8"))
    memberships = {}
    for tab, items in context["creative_tabs"].items():
        for item in items:
            memberships.setdefault(item, set()).add("operator_utilities" if tab == "op_blocks" else tab)
    count = 0
    for category in real.categories.values():
        assert {"dimensions", "inventory_categories", "name_length"} <= {field["key"] for field in category.fields}
        for entry in category.entries:
            count += 1
            attrs = entry["attrs"]
            assert attrs["name_length"] == core.name_length(entry["name"])
            assert set(attrs["dimensions"]) <= {"overworld", "nether", "end", "none"}
            members = context["concept_members"][category.key].get(entry["id"], [entry["id"]])
            expected_dims = set().union(*(set(context["dimensions"][category.key].get(member, [])) for member in members)) or {"none"}
            assert set(attrs["dimensions"]) == expected_dims
            if category.key == "mob":
                assert attrs["inventory_categories"] == ["not_applicable"]
            else:
                items = members if category.key == "item" else [context["block_items"].get(member, "air") for member in members]
                expected_tabs = set().union(*(memberships.get(item, set()) for item in items)) or {"none"}
                assert set(attrs["inventory_categories"]) == expected_tabs
    assert count == 2866


@pytest.mark.parametrize(
    ("category", "entry_id", "dimension"),
    [("block", "diamond_ore", "overworld"), ("block", "ancient_debris", "nether"), ("block", "end_stone", "end"),
     ("block", "bamboo", "overworld"), ("block", "barrier", "none"), ("item", "dragon_breath", "end"),
     ("mob", "blaze", "nether"), ("mob", "ender_dragon", "end"), ("mob", "creaking", "overworld"), ("mob", "marker", "none")],
)
def test_representative_natural_sources(real, category, entry_id, dimension):
    assert dimension in real.entry(category, entry_id)["attrs"]["dimensions"]


def test_creative_tabs_are_real_multiple_memberships_and_operator_entries(real):
    assert set(real.entry("item", "oak_planks")["attrs"]["inventory_categories"]) == {"building_blocks"}
    assert "redstone_blocks" in real.entry("block", "oak_button")["attrs"]["inventory_categories"]
    assert "operator_utilities" in real.entry("item", "command_block")["attrs"]["inventory_categories"]
    assert real.entry("block", "water")["attrs"]["inventory_categories"] == ["none"]
    assert any(len(row["attrs"]["inventory_categories"]) > 1 for row in real.entries("item"))


def test_name_length_comparison_points_toward_the_answer(real):
    category = real.category("block")
    short, long = category.entry("stone"), category.entry("diamond_ore")
    up = {item["key"]: item for item in core.evaluate_guess(category.fields, short, long)["feedback"]}["name_length"]
    down = {item["key"]: item for item in core.evaluate_guess(category.fields, long, short)["feedback"]}["name_length"]
    assert up["direction"] == core.UP and up["status"] == core.MISS and up["value"] == "2字"
    assert down["direction"] == core.DOWN and down["status"] == core.MISS and down["value"] == "4字"


def test_all_answers_still_compare_exactly_against_themselves(real):
    for category in real.categories.values():
        for entry in category.entries:
            result = core.evaluate_guess(category.fields, entry, entry)
            assert result["correct"] and all(item["status"] == core.EXACT for item in result["feedback"])
            assert all(item["present"] for item in result["name_feedback"])


def test_character_feedback_is_membership_not_wordle_positions_or_counts():
    feedback = core.name_characters("石钻钻木", "钻石矿石")
    assert [item["present"] for item in feedback] == [True, True, True, False]
    assert core.name_contains("石钻", "钻石矿石") is False
    assert core.name_contains("钻石", "钻石矿石") is True
    assert core.name_contains("tnt", "TNT") is True
    assert core.name_length("墙上的 木牌（旧）") == 8
    assert core.format_name_characters(feedback) == "🟩石 🟩钻 🟩钻 ⬜木"


@pytest.mark.parametrize(
    ("question", "key", "kind", "matched"),
    [("维度", "dimensions", "reveal", None), ("创造物品栏分类", "inventory_categories", "reveal", None),
     ("字数", "name_length", "reveal", None), ("几个字", "name_length", "reveal", None), ("首字", "first_char", "reveal", None),
     ("名字里有石吗", "name_contains", "contains", True), ("含有 金石", "name_contains", "contains", False),
     ("字数 大于 3", "name_length", "predicate", True), ("字数>=4", "name_length", "predicate", True),
     ("字数 小于 4", "name_length", "predicate", False), ("字数=4", "name_length", "predicate", True),
     ("硬度不等于3", "hardness", "predicate", False), ("硬度是不是大于2", "hardness", "predicate", True),
     ("维度 下界", "dimensions", "predicate", False), ("它是在主世界的吗", "dimensions", "predicate", True),
     ("创造分类 自然方块", "inventory_categories", "predicate", True),
     ("版本 早于 1.21", "initial_release", "predicate", True)],
)
def test_questions_are_grounded_in_real_catalogue(real, question, key, kind, matched):
    category = real.category("block")
    hint = hint_queries.build_question_hint(category.entry("diamond_ore"), category.fields, question)
    assert hint["field"] == key and hint["kind"] == kind
    if matched is not None:
        assert hint["matched"] is matched
    assert "钻石矿石" not in hint["text"]  # no full answer disclosure


@pytest.mark.parametrize("question", ["告诉我答案", "价格", "字数 无穷", "维度 火星", "生命值", "含有", "硬度 nan", "q" * 121])
def test_invalid_or_inapplicable_questions_reject_without_guessing(real, question):
    category = real.category("block")
    with pytest.raises(ValueError):
        hint_queries.build_question_hint(category.entry("diamond_ore"), category.fields, question)


def test_predicate_does_not_reveal_the_whole_hidden_attribute(real):
    category = real.category("block")
    answer = category.entry("diamond_ore")
    predicate = hint_queries.build_question_hint(answer, category.fields, "硬度 > 1")
    clues = {row["key"]: row for row in core.summarize_clues(category.fields, [], answer, [predicate])}
    assert clues["hardness"]["state"] == core.CLUE_PARTIAL
    assert "= 3" not in clues["hardness"]["text"]
    assert all(clue["state"] == core.CLUE_NONE for key, clue in clues.items() if key != "hardness")


def test_hint_queries_persist_deduplicate_and_respect_shared_budget(real, tmp_dir):
    async def scenario():
        service = GameService(tmp_dir, hint_limit=2)
        state = await service.start("queries", "block", "normal", real)
        original_guesses = state["guesses"][:]
        updated, first, _ = await service.hint("queries", real, "字数 >= 3")
        assert len(updated["hints"]) == 1 and updated["guesses"] == original_guesses
        assert updated["status"] == "playing"
        again, repeated, _ = await service.hint("queries", real, "名称长度 大于等于 3")
        assert again == updated and repeated["id"] == first["id"]
        with pytest.raises(ValueError):
            await service.hint("queries", real, "告诉我答案")
        assert await service.current("queries") == updated
        restarted = GameService(tmp_dir, hint_limit=2)
        updated, _, _ = await restarted.hint("queries", real, "含有 石")
        assert len(updated["hints"]) == 2
        with pytest.raises(ValueError, match="次数已经用完"):
            await restarted.hint("queries", real, "首字")
        with pytest.raises(ValueError, match="次数已经用完"):
            await restarted.hint("queries", real)
        same, _, _ = await restarted.hint("queries", real, "含有 石")
        assert len(same["hints"]) == 2
    asyncio.run(scenario())


def test_same_query_concurrently_only_counts_once(real, tmp_dir):
    async def scenario():
        service = GameService(tmp_dir, hint_limit=1)
        await service.start("concurrent", "item", "normal", real)
        await asyncio.gather(service.hint("concurrent", real, "字数"), service.hint("concurrent", real, "名称长度"))
        assert len((await service.current("concurrent"))["hints"]) == 1
    asyncio.run(scenario())


def test_all_fields_can_be_revealed_and_automatic_hints_are_unique(real):
    for category in real.categories.values():
        answer = category.entries[0]
        for field in category.fields:
            hint = hint_queries.build_question_hint(answer, category.fields, field["label"])
            assert hint["field"] == field["key"]
            assert hint["kind"] == "reveal"
        plan = core.hint_plan(category.fields, category.hint_fields)
        assert set(field["key"] for field in category.fields) <= set(plan)
        assert len(plan) == len(set(plan))
        used = []
        for _ in plan:
            hint = core.build_hint(answer, category.fields, category.hint_fields, used, limit=50)
            assert hint["field"] not in used
            used.append(hint["field"])


def test_card_and_history_show_name_characters_and_queries_without_answer(real):
    category = real.category("block")
    answer, guess = category.entry("diamond_ore"), category.entry("gold_ore")
    state, row = core.apply_guess({"guesses": [], "status": "playing"}, guess, answer, category.fields)
    hint = hint_queries.build_question_hint(answer, category.fields, "名字里有木吗")
    state.update(category="block", difficulty="normal", hints=[hint])
    view = card_views.guess_view(state, category, row, guess, answer, max_attempts=25, hint_limit=3)
    assert [value["present"] for value in view["name_feedback"]] == [False, True, True]
    assert view["history"][0]["name_feedback"] == view["name_feedback"]
    assert "⬜木" in "".join(view["hint_notes"])
    html = build_guess_card_html(view)
    assert 'class="name-char present"' in html and 'class="name-char absent"' in html
    assert "钻石矿石" not in html
    status = card_views.status_view(state, category, answer, max_attempts=25, hint_limit=3)
    assert "不含" in build_status_card_html(status)
    assert "钻石矿石" not in build_status_card_html(status)


def test_tool_schema_exposes_targeted_hint_question():
    assert llm_tool.SCHEMA["properties"]["question"]["maxLength"] == hint_queries.MAX_QUESTION_LENGTH
    assert llm_tool.parse_args({"action": "提示", "question": "名字里有石吗"}).question == "名字里有石吗"
    with pytest.raises(llm_tool.InvalidArgs):
        llm_tool.parse_args({"action": "提示", "question": "石" * 121})
