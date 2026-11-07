"""Exercise plugin initialization and starting rounds without an AstrBot install."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture()
def plugin_main(monkeypatch, tmp_dir):
    """Provide the imported AstrBot API, with no instance logger on Star."""
    modules: dict[str, ModuleType] = {}

    def module(name):
        if name not in modules:
            value = ModuleType(name)
            value.__path__ = []
            modules[name] = value
            monkeypatch.setitem(sys.modules, name, value)
            parent, _, child = name.rpartition(".")
            if parent:
                setattr(module(parent), child, value)
        return modules[name]

    api = module("astrbot.api")
    api.AstrBotConfig = dict
    api.logger = Mock()

    class Star:
        def __init__(self, context):
            self.context = context

    star = module("astrbot.api.star")
    star.Star = Star
    star.Context = SimpleNamespace
    star.StarTools = SimpleNamespace(get_data_dir=lambda name: tmp_dir)
    module("astrbot.api.event").AstrMessageEvent = SimpleNamespace
    module("astrbot.core.message.components").Image = SimpleNamespace

    registered = []
    module("astrbot.core.provider.register").llm_tools = SimpleNamespace(
        func_list=registered,
        get_func=lambda name: next((tool for tool in registered if tool.name == name), None),
    )

    def decorator(*args, **kwargs):
        return lambda func: func

    def llm_tool(*, name):
        def register(func):
            registered.append(SimpleNamespace(name=name, parameters=None))
            return func

        return register

    filters = module("astrbot.api.event.filter")
    filters.EventMessageType = SimpleNamespace(ALL="all")
    filters.command = decorator
    filters.event_message_type = decorator
    filters.on_llm_request = decorator
    filters.llm_tool = llm_tool

    name = "astrbot_plugin_mcguess._initialization_test_main"
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / "main.py")
    loaded = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, loaded)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.mark.parametrize("local_supported", [True, False], ids=["playwright", "no-playwright"])
def test_plugin_initializes_without_instance_logger(plugin_main, monkeypatch, local_supported):
    monkeypatch.setattr(plugin_main.LocalCardRenderer, "is_supported", staticmethod(lambda: local_supported))
    plugin = plugin_main.MCGuessPlugin(SimpleNamespace())

    assert not hasattr(plugin, "logger")
    assert plugin.catalogue is not None
    assert plugin.rendering._logger is plugin_main.logger
    assert plugin.rendering.local_available is local_supported
    if local_supported:
        assert plugin.rendering._local._logger is plugin_main.logger

    async def lifecycle():
        await plugin.initialize()
        await plugin.terminate()

    asyncio.run(lifecycle())
    plugin_main.logger.error.assert_not_called()
    assert plugin_main.logger.info.call_count == 2
    assert plugin_main.logger.warning.call_count == (0 if local_supported else 1)


@pytest.fixture()
def start_plugin(plugin_main, monkeypatch, catalogue):
    monkeypatch.setattr(plugin_main.LocalCardRenderer, "is_supported", staticmethod(lambda: False))
    plugin = plugin_main.MCGuessPlugin(SimpleNamespace(), {"render_cards": False})
    plugin.catalogue = catalogue
    event = SimpleNamespace(
        message_str="",
        unified_msg_origin="test:GroupMessage:optional-category",
        message_obj=SimpleNamespace(group_id="optional-category"),
        is_private_chat=lambda: False,
    )
    return plugin, event


@pytest.mark.parametrize("via_tool", [False, True], ids=["command", "tool"])
@pytest.mark.parametrize(
    ("argument", "difficulty", "category"),
    [("", "normal", "mob"), ("困难", "hard", "block"), ("简单", "easy", "item")],
)
def test_start_without_category(plugin_main, start_plugin, monkeypatch, via_tool, argument, difficulty, category):
    plugin, event = start_plugin
    picker = Mock(return_value=category)
    monkeypatch.setattr(plugin_main, "random", SimpleNamespace(choice=picker))

    async def scenario():
        if via_tool:
            args = {"action": "开始"}
            if argument:
                args["difficulty"] = argument
            result = json.loads(await plugin.mc_guess_tool(event, **args))
            assert result["status"] == "ok"
            text = result["text"]
        else:
            event.message_str = f"/mc猜 开始 {argument}".strip()
            result = await plugin._dispatch(event, plugin._command_args(event.message_str))
            assert not result.error
            text = result.text
        state = await plugin.games.current(event.unified_msg_origin)
        assert state["category"] == category
        assert state["difficulty"] == difficulty
        assert f"已开始「{plugin.catalogue.category(category).label}」猜谜" in text
        assert plugin_main.DIFFICULTY_LABELS[difficulty] in text
        answer = plugin.games.answer_of(state, plugin.catalogue)
        guessed = await plugin._dispatch(event, f"猜 {answer['name']}")
        assert not guessed.error
        assert "猜中了" in guessed.text

    asyncio.run(scenario())
    picker.assert_called_once_with(["block", "item", "mob"])


@pytest.mark.parametrize(
    ("word", "category"), [("方块", "block"), ("物品", "item"), ("生物", "mob"), ("实体", "mob")]
)
def test_explicit_category_still_starts(plugin_main, start_plugin, monkeypatch, word, category):
    plugin, event = start_plugin
    picker = Mock(side_effect=AssertionError("An explicit category must not be randomized"))
    monkeypatch.setattr(plugin_main, "random", SimpleNamespace(choice=picker))

    async def scenario():
        result = await plugin._dispatch(event, f"开始 {word} 困难")
        assert not result.error
        state = await plugin.games.current(event.unified_msg_origin)
        assert state["category"] == category
        assert state["difficulty"] == "hard"

    asyncio.run(scenario())
    picker.assert_not_called()


@pytest.mark.parametrize("arguments", ["开始 不存在", "开始 方块 不存在", "开始 困难 多余"])
def test_invalid_start_does_not_create_round(start_plugin, arguments):
    plugin, event = start_plugin

    async def scenario():
        result = await plugin._dispatch(event, arguments)
        assert result.error
        assert await plugin.games.current(event.unified_msg_origin) is None

    asyncio.run(scenario())


def test_random_start_skips_empty_pools(plugin_main, start_plugin, monkeypatch):
    plugin, event = start_plugin
    answer_pool = plugin.catalogue.answer_pool
    monkeypatch.setattr(
        plugin.catalogue, "answer_pool", lambda key, difficulty: answer_pool(key, difficulty) if key == "item" else []
    )
    picker = Mock(return_value="item")
    monkeypatch.setattr(plugin_main, "random", SimpleNamespace(choice=picker))
    assert not asyncio.run(plugin._dispatch(event, "开始")).error
    picker.assert_called_once_with(["item"])


def test_random_start_reports_empty_catalogue(start_plugin, monkeypatch):
    plugin, event = start_plugin
    monkeypatch.setattr(plugin.catalogue, "answer_pool", lambda key, difficulty: [])

    async def scenario():
        result = await plugin._dispatch(event, "开始")
        assert result.error
        assert "没有可用答案" in result.text
        assert await plugin.games.current(event.unified_msg_origin) is None

    asyncio.run(scenario())


def test_random_start_preserves_active_group_round(start_plugin):
    plugin, event = start_plugin

    async def scenario():
        assert not (await plugin._dispatch(event, "开始")).error
        original = await plugin.games.current(event.unified_msg_origin)
        second = await plugin._dispatch(event, "开始 困难")
        assert second.error and "还没结束" in second.text
        assert await plugin.games.current(event.unified_msg_origin) == original

    asyncio.run(scenario())


@pytest.mark.parametrize("text", ["随机来一局", "随机开一局"])
def test_random_start_request_offers_llm_tool(start_plugin, text):
    plugin, event = start_plugin
    event.message_str = text
    assert asyncio.run(plugin._tool_visibility(event)) == (True, False)


@pytest.mark.parametrize("via_tool", [False, True], ids=["command", "tool"])
def test_targeted_hints_and_free_range_are_wired(plugin_main, start_plugin, via_tool):
    plugin, event = start_plugin

    async def ask(question):
        if via_tool:
            payload = json.loads(await plugin.mc_guess_tool(event, action="提示", question=question))
            assert payload["status"] == "ok", payload
            return payload["text"]
        result = await plugin._dispatch(event, "提示 " + question)
        assert not result.error, result.text
        return result.text

    async def scenario():
        assert not (await plugin._dispatch(event, "开始 方块 简单")).error
        original = await plugin.games.current(event.unified_msg_origin)
        assert "可以询问" in await ask("范围")
        assert await plugin.games.current(event.unified_msg_origin) == original
        assert "个字" in await ask("字数")
        assert "逐字" in await ask("名字里有石吗")
        assert "逐字" in await ask("含有 石")  # canonical duplicate is free
        state = await plugin.games.current(event.unified_msg_origin)
        assert len(state["hints"]) == 2 and not state["guesses"]
        status = await plugin._dispatch(event, "状态")
        assert "逐字" in status.text and "名称字数" not in status.text  # sample catalogue has no length field

    asyncio.run(scenario())


def test_woken_free_text_can_ask_a_hint(start_plugin):
    plugin, event = start_plugin
    event.message_str = "提示 含有 石"
    event.is_at_or_wake_command = True
    event.plain_result = lambda text: text

    async def scenario():
        await plugin._dispatch(event, "开始 方块")
        replies = [reply async for reply in plugin.free_text_guess(event)]
        assert len(replies) == 1 and "逐字" in replies[0]
        assert len((await plugin.games.current(event.unified_msg_origin))["hints"]) == 1

    asyncio.run(scenario())
