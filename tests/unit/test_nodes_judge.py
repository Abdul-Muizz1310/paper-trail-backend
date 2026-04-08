"""Unit tests for agents/nodes/judge.py."""

from __future__ import annotations

from paper_trail.agents.nodes import judge as mod


class _FakeVerdict:
    def __init__(self, verdict: str, confidence: float) -> None:
        self.verdict = verdict
        self.confidence = confidence


async def test_judge_converges_high_confidence(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def fake_chat_json(messages, schema, **kw):  # type: ignore[no-untyped-def]
        return _FakeVerdict("TRUE", 0.9)

    monkeypatch.setattr(mod, "chat_json", fake_chat_json)
    out = await mod.judge({"claim": "c", "max_rounds": 5, "round": 1, "rounds": []})
    assert out["verdict"] == "TRUE"
    assert out["confidence"] == 0.9
    assert out["need_more"] is False
    assert out["round"] == 2


async def test_judge_needs_more_low_confidence(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def fake_chat_json(messages, schema, **kw):  # type: ignore[no-untyped-def]
        return _FakeVerdict("INCONCLUSIVE", 0.4)

    monkeypatch.setattr(mod, "chat_json", fake_chat_json)
    out = await mod.judge({"claim": "c", "max_rounds": 5, "round": 1, "rounds": []})
    assert out["need_more"] is True
    assert out["round"] == 2


async def test_judge_stops_at_max_rounds(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def fake_chat_json(messages, schema, **kw):  # type: ignore[no-untyped-def]
        return _FakeVerdict("INCONCLUSIVE", 0.3)

    monkeypatch.setattr(mod, "chat_json", fake_chat_json)
    out = await mod.judge({"claim": "c", "max_rounds": 3, "round": 3, "rounds": []})
    assert out["need_more"] is False
