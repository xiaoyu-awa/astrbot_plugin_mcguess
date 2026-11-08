"""Deterministic, catalogue-backed hint questions; never ask a model for the answer."""

from __future__ import annotations

import math
import re
from typing import Any

from . import core

MAX_QUESTION_LENGTH = 120
RANGE_WORDS = {"范围", "能问什么", "可以问什么", "可问范围", "可问属性", "属性", "help", "?", "？"}
ALIASES = {
    "dimensions": ["维度", "来源维度", "自然生成维度", "在哪个维度", "在哪个世界", "世界"],
    "inventory_categories": ["创造物品栏分类", "创造物品栏", "创造分类", "物品栏分类", "库存类别"],
    "name_length": ["名称字数", "名字字数", "名字长度", "名称长度", "多少个字", "几个字", "字数"],
    "variants": ["变体", "变体类型", "变种", "变种类型"],
    "first_char": ["名称首字", "名字首字", "第一个字", "首字", "首字是什么"],
    "hardness": ["硬度"],
    "blast_resistance": ["爆炸抗性", "抗爆性", "抗爆"],
    "tool": ["挖掘工具", "采掘工具", "工具"],
    "luminous": ["发光性", "是否发光", "会发光", "发光"],
    "transparent": ["透明性", "是否透明", "透明"],
    "full_block": ["完整方块", "完整性"],
    "flammable": ["可燃性", "是否可燃", "能燃烧", "可燃"],
    "fire_catch": ["火焰传播性", "火焰传播"],
    "stackable": ["堆叠数量", "最大堆叠", "堆叠数", "堆叠"],
    "renewable": ["可再生性", "是否可再生", "可再生"],
    "initial_release": ["初始版本", "加入版本", "首次版本", "版本"],
    "rarity": ["稀有度", "稀有性"],
    "obtaining": ["获得方式", "获取方式", "获取途径"],
    "usage": ["用途", "用处"],
    "recipe": ["合成方式", "合成配方", "合成"],
    "loot": ["掉落方式", "掉落"],
    "health": ["生命值", "血量", "生命"],
    "height": ["高度", "身高"],
    "behavior": ["行为", "敌对性"],
    "spawn": ["生成方式", "出生方式", "生成"],
    "classification": ["生物分类", "实体分类", "分类"],
}
_VALUE_ALIASES = {
    "variants": {"颜色变体": "color", "颜色变种": "color", "种类": "type", "品种变体": "type", "品种变种": "type", "没有": "none", "无变体": "none"},
    "dimensions": {"地狱": "nether", "地獄": "nether", "地表": "overworld", "主世界": "overworld", "下界": "nether", "末地": "end"},
    "inventory_categories": {"建筑": "building_blocks", "红石": "redstone_blocks", "红石相关方块": "redstone_blocks", "自然": "natural_blocks", "功能": "functional_blocks", "功能性方块": "functional_blocks", "染色": "colored_blocks", "战斗": "combat", "战斗物品": "combat", "食物": "food_and_drinks", "食物与饮料": "food_and_drinks", "材料": "ingredients", "工具": "tools_and_utilities", "刷怪蛋": "spawn_eggs", "生成蛋": "spawn_eggs", "管理员": "operator_utilities", "管理员工具": "operator_utilities"},
}
_NUMBER_OPS = {">": "gt", ">=": "ge", "<": "lt", "<=": "le", "=": "eq", "==": "eq", "!=": "ne", "≥": "ge", "≤": "le", "≠": "ne", "大于": "gt", "超过": "gt", "高于": "gt", "晚于": "gt", "小于": "lt", "低于": "lt", "少于": "lt", "早于": "lt", "大于等于": "ge", "至少": "ge", "不少于": "ge", "小于等于": "le", "至多": "le", "不超过": "le", "等于": "eq", "不等于": "ne", "不是": "ne", "是": "eq"}
_OP_TEXT = {"gt": ">", "ge": "≥", "lt": "<", "le": "≤", "eq": "=", "ne": "≠"}


def _clean(question: str) -> str:
    text = str(question or "").strip().rstrip("？?。！!").strip()
    if text.endswith(("吗", "么", "呢")):
        text = text[:-1].strip()
    return text


def _unquote(text: str) -> str:
    return text.strip().strip("「」『』“”\"'《》").strip()


def hint_range(fields: list[dict[str, Any]]) -> str:
    labels = list(dict.fromkeys([str(field.get("label") or field["key"]) for field in fields] + ["名称字数", "名称首字", "名字含字"]))
    return (
        "可以询问：" + "、".join(labels) + "。\n"
        "指定属性：`/mc猜 提示 维度`、`/mc猜 提示 字数`、`/mc猜 提示 变体`。\n"
        "判断取值：`/mc猜 提示 维度 下界`、`/mc猜 提示 变体 颜色`、`/mc猜 提示 创造分类 建筑方块`。\n"
        "比较数值：`/mc猜 提示 字数 大于 3`（支持 >、≥、<、≤、=、≠）。\n"
        "查询文字：`/mc猜 提示 含有 石`；多个字会同时标出每个字是否出现，并判断整段文字是否连续出现。\n"
        "每个新问题使用一次提示；重复问题、无效问题和查看范围不扣次数。字数不计空白、包含标点；含字标记不表示位置或出现次数。"
    )


def _reveal(answer: dict[str, Any], fields: list[dict[str, Any]], key: str) -> dict[str, Any]:
    if key == core.HINT_FIRST_CHAR:
        hint = {"field": key, "label": "名称首字", "text": f"答案的第一个字是「{str(answer.get('name') or '')[:1]}」"}
    elif key == core.HINT_NAME_LENGTH:
        hint = {"field": key, "label": "名称字数", "text": f"答案共 {core.name_length(answer.get('name', ''))} 个字（不计空白，含标点）"}
    else:
        hint = core.build_hint(answer, fields, [key], [], limit=1)
    return {**hint, "id": key, "kind": "reveal"}


def _value(field: dict[str, Any], text: str) -> str:
    labels = field.get("values") or {}
    folded = _unquote(text).casefold()
    aliases = _VALUE_ALIASES.get(field["key"], {})
    candidate = aliases.get(folded)
    if candidate in labels:
        return candidate
    for key, label in labels.items():
        if folded in (str(key).casefold(), str(label).casefold()):
            return str(key)
    if "yes" in labels and folded in {"是", "有", "会", "能", "可以", "true"}:
        return "yes"
    if "no" in labels and folded in {"否", "没有", "不会", "不能", "不可以", "false"}:
        return "no"
    choices = "、".join(str(v) for v in labels.values())
    raise ValueError(f"{field['label']}不支持取值「{text}」。可用取值：{choices}。")


def build_question_hint(answer: dict[str, Any], fields: list[dict[str, Any]], question: str) -> dict[str, Any]:
    """Validate a targeted reveal, membership test or ordered comparison."""
    if len(question) > MAX_QUESTION_LENGTH:
        raise ValueError(f"提示问题最长 {MAX_QUESTION_LENGTH} 个字符。")
    text = _clean(question)
    text = re.sub(r"^(?:请问|请告诉我)", "", text).strip()
    text = re.sub(r"^(?:答案|这个方块|这个物品|这个生物|这个实体|这个东西|它)(?:的)?", "", text).strip()
    if text.startswith("会不会"):
        text = "是否" + text[3:]
    # "有颜色变体吗" asks about the property, not characters in the answer.
    if any(field.get("key") == "variants" for field in fields):
        variant = re.fullmatch(r"(没有|不含|不包含|无|是否有|是否|有没有|有无|有|是)?(颜色|品种)(?:变体|变种)", text)
        if variant:
            negative = variant.group(1) in {"没有", "不含", "不包含", "无"}
            return build_question_hint(answer, fields, "变体 " + ("不含 " if negative else "") + variant.group(2))
        if text in {"有什么变体", "有哪些变体", "有什么变种", "有哪些变种"}:
            return _reveal(answer, fields, "variants")
        if text in {"有变体", "有没有变体", "是否有变体", "有变种", "有没有变种"}:
            return build_question_hint(answer, fields, "变体 不是 无")
        if text in {"没有变体", "无变体", "没有变种", "无变种"}:
            return build_question_hint(answer, fields, "变体 无")
    contains = re.fullmatch(r"(?:(?:名字|名称|中文名)(?:里|中)?)?(?:是否|有没有)?(?:包含|含有|含字|含(?![有字])|有)\s*(.+)", text)
    if contains:
        query = _unquote(contains.group(1))
        if not query or core.name_length(query) > 64:
            raise ValueError("请提供要查询的文字（1–64 个字），例如 `/mc猜 提示 含有 石`。")
        matched = core.name_contains(query, str(answer.get("name") or ""))
        characters = core.name_characters(query, str(answer.get("name") or ""))
        return {
            "id": "name_contains:" + core._name_text(query),
            "kind": "contains", "field": "name_contains", "label": "名称含字", "query": query,
            "matched": matched, "characters": characters,
            "text": f"名称{'含有' if matched else '不含'}连续文字「{query}」；逐字：{core.format_name_characters(characters)}",
        }

    field_map = {field["key"]: field for field in fields}
    field_map.setdefault("name_length", {"key": "name_length", "label": "名称字数", "kind": "number", "unit": "字"})
    field_map["first_char"] = {"key": "first_char", "label": "名称首字", "kind": "enum"}
    aliases = [(alias, key) for key, field in field_map.items() for alias in [key, field["label"], *ALIASES.get(key, [])]]
    for alias, key in sorted(aliases, key=lambda pair: len(pair[0]), reverse=True):
        if not text.casefold().startswith(alias.casefold()):
            continue
        field = field_map[key]
        value_text = text[len(alias):].strip().lstrip(":：").strip()
        if value_text.startswith("是不是"):
            value_text = value_text[3:].strip()
        if value_text in {"", "是多少", "是什么", "有哪些", "多少", "是什么类型"}:
            return _reveal(answer, fields, key)
        if key == "first_char":
            raise ValueError("名称首字请直接询问 `/mc猜 提示 首字`；查某个字是否出现请用 `提示 含有 <文字>`。")
        raw = core.attribute_value(answer, key)
        if field["kind"] in {"number", "version"}:
            operator = "eq"
            for prefix in sorted(_NUMBER_OPS, key=len, reverse=True):
                if value_text.startswith(prefix):
                    operator = _NUMBER_OPS[prefix]
                    value_text = value_text[len(prefix):].strip()
                    break
            if field["kind"] == "number":
                number = re.fullmatch(r"(-?(?:\d+(?:\.\d*)?|\.\d+))\s*(?:个字|字|格|点)?", value_text)
                if not number:
                    raise ValueError(f"{field['label']}需要一个数值，例如 `{field['label']} 大于 3`。")
                expected = float(number.group(1))
                actual = float(raw)
                if not math.isfinite(expected) or not math.isfinite(actual):
                    raise ValueError("数值必须是有限数字。")
                rendered = f"{expected:g}"
            else:
                order = field.get("order") or {}
                if value_text not in order:
                    raise ValueError("版本必须是题库中收录的初始版本，例如 1.16、1.21。")
                expected, actual = order[value_text], order.get(str(raw))
                if actual is None:
                    raise ValueError("本词条没有可比较的版本顺序。")
                rendered = value_text
            matched = {"eq": actual == expected, "ne": actual != expected, "gt": actual > expected, "ge": actual >= expected, "lt": actual < expected, "le": actual <= expected}[operator]
            canonical = rendered
            condition = f"{_OP_TEXT[operator]} {rendered}"
        else:
            negative = False
            for prefix in ("不包含", "不含有", "不含", "不是", "不等于", "!=", "≠", "包含", "含有", "含", "等于", "是", "="):
                if value_text.startswith(prefix):
                    negative = prefix in {"不包含", "不含有", "不含", "不是", "不等于", "!=", "≠"}
                    value_text = value_text[len(prefix):].strip()
                    break
            canonical = _value(field, value_text)
            present = canonical in core._value_set(raw)
            matched = not present if negative else present
            operator = "not_contains" if negative else "contains"
            condition = ("不含" if negative else "含") + f"「{core.format_value(field, canonical)}」"
        return {
            "id": f"{key}:{operator}:{canonical}", "kind": "predicate", "field": key, "label": field["label"],
            "operator": operator, "query": canonical, "matched": matched,
            "text": f"{field['label']}{condition}：{'是' if matched else '否'}",
        }
    # A dimension alone is a common natural-language membership question.
    dimension = re.fullmatch(r"(?:是否|是不是|是)?(?:在|来自|属于)?(主世界|下界|末地|地狱)(?:的|生成|生成的|出现|出现的)?", text)
    if dimension and "dimensions" in field_map:
        return build_question_hint(answer, fields, "维度 " + dimension.group(1))
    raise ValueError("无法识别这个提示问题。可指定属性、判断取值、比较数值或查名字含字；发送 `/mc猜 提示 范围` 查看本局可问属性。")
