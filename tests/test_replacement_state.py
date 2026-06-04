
"""ContentReplacementState 测试 — Design B（决策冻结，不变更）。"""
from __future__ import annotations

import json
from pathlib import Path

from lazycode.context.manager import (
    AGGREGATE_CHAR_LIMIT,
    PERSISTED_TAG,
    REPLACEMENT_RECORDS_FILENAME,
    SINGLE_RESULT_CHAR_LIMIT,
    ContentReplacementRecord,
    append_replacement_records,
    apply_tool_result_budget,
    clone_replacement_state,
    create_replacement_state,
    load_replacement_records,
    reconstruct_replacement_state,
)
from lazycode.conversation import ConversationManager, Message, ToolResultBlock

def _one_msg_conv(*results: ToolResultBlock) -> ConversationManager:
    conv = ConversationManager()
    conv.history.append(Message(role="user", content="", tool_results=list(results)))
    return conv

# ---------------------------------------------------------------------------
# 状态容器基础
# ---------------------------------------------------------------------------

def test_create_returns_empty() -> None:
    state = create_replacement_state()
    assert state.seen_ids == set()
    assert state.replacements == {}

def test_clone_independent() -> None:
    src = create_replacement_state()
    src.seen_ids.add("a")
    src.replacements["a"] = "preview_a"

    cloned = clone_replacement_state(src)
    cloned.seen_ids.add("b")
    cloned.replacements["b"] = "preview_b"

    assert "b" not in src.seen_ids
    assert "b" not in src.replacements
    assert cloned.seen_ids == {"a", "b"}
    assert cloned.replacements == {"a": "preview_a", "b": "preview_b"}

# ---------------------------------------------------------------------------
# Design B：apply 不会修改输入 conversation
# ---------------------------------------------------------------------------

def test_apply_does_not_mutate_conv(tmp_path: Path) -> None:
    big = "x" * (SINGLE_RESULT_CHAR_LIMIT + 100)
    conv = _one_msg_conv(ToolResultBlock(tool_use_id="t1", content=big))
    orig_content = conv.history[0].tool_results[0].content
    orig_history_id = id(conv.history)
    state = create_replacement_state()

    api_conv, _ = apply_tool_result_budget(conv, tmp_path, state)

    # 原始 conv 必须保持不变（Design B 不变量）
    assert conv.history[0].tool_results[0].content == orig_content
    # api_conv 是由不同 list 支撑的另一个 ConversationManager
    assert api_conv is not conv
    assert api_conv.history is not conv.history
    # 并且它携带替换内容
    assert api_conv.history[0].tool_results[0].content.startswith(PERSISTED_TAG)

def test_first_call_freezes_unreplaced(tmp_path: Path) -> None:
    """低于预算的结果必须标记为 seen，但不添加到 replacements。"""
    small = "x" * 100
    conv = _one_msg_conv(ToolResultBlock(tool_use_id="t1", content=small))
    state = create_replacement_state()

    _, records = apply_tool_result_budget(conv, tmp_path, state)

    assert state.seen_ids == {"t1"}
    assert state.replacements == {}
    assert records == []

# ---------------------------------------------------------------------------
# 跨轮次字节级一致重放
# ---------------------------------------------------------------------------

def test_replacement_byte_identical(tmp_path: Path) -> None:
    """对同一 conv 调用两次 apply 会产生字节级一致的 api_conv 内容。"""
    big = "x" * (SINGLE_RESULT_CHAR_LIMIT + 100)
    conv = _one_msg_conv(ToolResultBlock(tool_use_id="t_big", content=big))
    state = create_replacement_state()

    api1, recs1 = apply_tool_result_budget(conv, tmp_path, state)
    api2, recs2 = apply_tool_result_budget(conv, tmp_path, state)

    c1 = api1.history[0].tool_results[0].content
    c2 = api2.history[0].tool_results[0].content
    assert c1 == c2, "second pass must produce byte-identical content"
    assert recs1[0].replacement == c1
    # 第二次处理是纯重新应用：没有新记录，也没有新文件写入
    assert recs2 == []

# ---------------------------------------------------------------------------
# 决策冻结：一旦 seen 但未替换，之后永不替换
# ---------------------------------------------------------------------------

def test_frozen_never_replaced(tmp_path: Path) -> None:
    """第 1 轮中被视为 'not replaced' 的 id 绝不能被选中替换，
    即使之后消息的总量原本会选择它。"""
    # 第 1 轮：单个约 4K 的结果，远低于总量限制
    quarter = AGGREGATE_CHAR_LIMIT // 4  # 5000
    conv = _one_msg_conv(ToolResultBlock(tool_use_id="t1", content="a" * quarter))
    state = create_replacement_state()

    apply_tool_result_budget(conv, tmp_path, state)
    assert "t1" in state.seen_ids
    assert "t1" not in state.replacements

    # 第 2 轮：模拟同一条消息现在增长（并行 tool result 合并），
    # 将总量推到超出预算。（现实中这不会发生——messages
    # 一旦添加就不可变——但这里强制这样做以断言该不变量。）
    fresh_large = "b" * (quarter * 3 + 100)  # 非常大的新候选项
    conv.history[0].tool_results.append(
        ToolResultBlock(tool_use_id="t2", content=fresh_large)
    )

    api_conv, _ = apply_tool_result_budget(conv, tmp_path, state)

    # 第一次处理只会溢出 t2（它 > SINGLE_RESULT_CHAR_LIMIT），因此 t1 保持原样
    # 与总量无关。重点是：t1 从未被重新考虑。
    api_t1 = next(tr for tr in api_conv.history[0].tool_results if tr.tool_use_id == "t1")
    assert api_t1.content == "a" * quarter
    assert "t1" not in state.replacements

def test_aggregate_only_picks_fresh(tmp_path: Path) -> None:
    """当总量超过预算且只有新候选项符合条件时，frozen
    ids 即使最大也不可被处理。"""
    # 四个结果都低于 SINGLE_RESULT_CHAR_LIMIT，但总和 > AGGREGATE。
    big_under = SINGLE_RESULT_CHAR_LIMIT - 1
    conv = _one_msg_conv(
        ToolResultBlock(tool_use_id="t1", content="a" * big_under),
        ToolResultBlock(tool_use_id="t2", content="b" * big_under),
        ToolResultBlock(tool_use_id="t3", content="c" * big_under),
        ToolResultBlock(tool_use_id="t4", content="d" * big_under),
        ToolResultBlock(tool_use_id="t5", content="e" * big_under),
    )
    # 总量 = 5 * 4999 = 24995 > 20000
    state = create_replacement_state()

    api_conv, recs = apply_tool_result_budget(conv, tmp_path, state)

    # 某个子集已被替换；现在总量 ≤ 限制
    api_total = sum(len(tr.content) for tr in api_conv.history[0].tool_results)
    assert api_total <= AGGREGATE_CHAR_LIMIT
    assert len(recs) >= 1, "at least one result should have been spilled"

    # 所有 id 现在都应位于 seen_ids 中（每个都已做出决策）
    assert {"t1", "t2", "t3", "t4", "t5"} <= state.seen_ids

# ---------------------------------------------------------------------------
# 重建
# ---------------------------------------------------------------------------

def test_reconstruct_from_records() -> None:
    msgs = [
        Message(
            role="user", content="",
            tool_results=[
                ToolResultBlock(tool_use_id="t1", content="raw"),
                ToolResultBlock(tool_use_id="t2", content="raw"),
            ],
        ),
    ]
    records = [
        ContentReplacementRecord(tool_use_id="t1", replacement="t1_preview"),
        # t2 没有记录 → 重建后处于 frozen-unreplaced
    ]

    state = reconstruct_replacement_state(msgs, records)

    assert state.seen_ids == {"t1", "t2"}
    assert state.replacements == {"t1": "t1_preview"}

def test_reconstruct_with_inherited_parent() -> None:
    """Fork-resume：父级 live replacements 会补齐 records 中不存在的 ids。"""
    msgs = [
        Message(
            role="user", content="",
            tool_results=[
                ToolResultBlock(tool_use_id="t_parent", content="raw"),
                ToolResultBlock(tool_use_id="t_child", content="raw"),
            ],
        ),
    ]
    records = [
        ContentReplacementRecord(tool_use_id="t_child", replacement="child_preview"),
    ]
    inherited = {"t_parent": "parent_preview"}

    state = reconstruct_replacement_state(msgs, records, inherited_replacements=inherited)

    assert state.replacements == {
        "t_child": "child_preview",
        "t_parent": "parent_preview",
    }

# ---------------------------------------------------------------------------
# Transcript I/O
# ---------------------------------------------------------------------------

def test_append_and_load_records_roundtrip(tmp_path: Path) -> None:
    recs = [
        ContentReplacementRecord(tool_use_id="a", replacement="aaa"),
        ContentReplacementRecord(tool_use_id="b", replacement="bbb"),
    ]
    append_replacement_records(tmp_path, recs)
    append_replacement_records(tmp_path, [
        ContentReplacementRecord(tool_use_id="c", replacement="ccc"),
    ])

    out = load_replacement_records(tmp_path)
    assert [r.tool_use_id for r in out] == ["a", "b", "c"]
    assert [r.replacement for r in out] == ["aaa", "bbb", "ccc"]
    assert all(r.kind == "tool-result" for r in out)

    # 文件是 JSONL，每行一个对象
    raw = (tmp_path / REPLACEMENT_RECORDS_FILENAME).read_text(encoding="utf-8")
    lines = raw.strip().split("\n")
    assert len(lines) == 3
    for line in lines:
        obj = json.loads(line)
        assert set(obj.keys()) >= {"kind", "tool_use_id", "replacement"}
