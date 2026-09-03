"""Tests for the memory write gate and content normalization. / 记忆写入闸门与内容归一化的测试。"""

from __future__ import annotations

from zharness.memory.gate import fact_gate_reason, normalize_content


def test_normalize_content_collapses_whitespace_and_case() -> None:
    assert normalize_content("  Hello   World\n") == "hello world"
    assert normalize_content("café") == "café"


def test_gate_accepts_user_durable_descriptive() -> None:
    classification = {
        "scope": "user",
        "durability": "durable",
        "authority": "descriptive",
    }
    assert fact_gate_reason(classification) is None


def test_gate_rejects_wrong_scope() -> None:
    classification = {
        "scope": "thread",
        "durability": "durable",
        "authority": "descriptive",
    }
    assert fact_gate_reason(classification) == "scope"


def test_gate_rejects_non_durable() -> None:
    classification = {
        "scope": "user",
        "durability": "temporary",
        "authority": "descriptive",
    }
    assert fact_gate_reason(classification) == "durability"


def test_gate_rejects_transactional_authority() -> None:
    classification = {
        "scope": "user",
        "durability": "durable",
        "authority": "transactional",
    }
    assert fact_gate_reason(classification) == "authority"


def test_gate_rejects_missing_labels() -> None:
    assert fact_gate_reason({"scope": "user"}) == "missing"
    assert fact_gate_reason({}) == "missing"


def test_gate_normalizes_label_casing() -> None:
    classification = {
        "scope": "USER",
        "durability": "Durable",
        "authority": "Descriptive",
    }
    assert fact_gate_reason(classification) is None
