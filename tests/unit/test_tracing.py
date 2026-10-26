"""Langfuse tracing — docs/specs/12-tracing.md, cases T1-T15.

Spans go to an in-memory exporter (the ``traces`` fixture); nothing reaches Langfuse.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import create_async_engine

import paper_trail.core.db as db_mod
from paper_trail.agents import graph as graph_mod
from paper_trail.agents.nodes import plan as plan_mod
from paper_trail.agents.tools.fetch import fetch
from paper_trail.agents.tools.search import search
from paper_trail.core import llm as llm_mod
from paper_trail.core import tracing
from paper_trail.core.config import Settings, settings
from paper_trail.core.errors import LLMError, ToolError
from paper_trail.models.debate import Base
from paper_trail.services.debates import DebateService
from tests.conftest import Traces, tracing_client

FIXTURE_HTML = (Path(__file__).parent.parent / "fixtures" / "sample_article.html").read_text(
    encoding="utf-8"
)
TAVILY_URL = "https://api.tavily.com/search"
ARTICLE_URL = "https://example.org/boiling"
CLAIM = "Water boils at 100 degrees Celsius at sea level."
INPUT = "langfuse.observation.input"
OUTPUT = "langfuse.observation.output"


def _chat_url() -> str:
    return f"{settings.openrouter_base_url}/chat/completions"


def _reply(content: str | None, **extra: Any) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": content}}], **extra}


class _Answer(BaseModel):
    x: int


@pytest.fixture(autouse=True)
def _three_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openrouter_model_primary", "m/primary:free")
    monkeypatch.setattr(settings, "openrouter_model_fast", "m/fast:free")
    monkeypatch.setattr(settings, "openrouter_model_fallback", "m/fallback:free")

    async def no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(llm_mod, "_backoff_sleep", no_sleep)


# ---------------------------------------------------------------------------
# T1 — the client from the settings
# ---------------------------------------------------------------------------


class _Recorder:
    seen: ClassVar[dict[str, Any]] = {}

    def __init__(self, **kwargs: Any) -> None:
        _Recorder.seen = kwargs


def test_t1_both_keys_build_an_enabled_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing, "Langfuse", _Recorder)
    tracing.build_client(
        Settings(
            _env_file=None,
            langfuse_public_key="pk-lf-1",
            langfuse_secret_key="sk-lf-1",
            langfuse_base_url="https://us.cloud.langfuse.com",
            app_env="production",
        )
    )
    assert _Recorder.seen == {
        "public_key": "pk-lf-1",
        "secret_key": "sk-lf-1",
        "base_url": "https://us.cloud.langfuse.com",
        "environment": "production",
    }


def test_t1_no_base_url_leaves_the_sdk_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing, "Langfuse", _Recorder)
    tracing.build_client(
        Settings(_env_file=None, langfuse_public_key="pk", langfuse_secret_key="sk")
    )
    assert _Recorder.seen["base_url"] is None


@pytest.mark.parametrize(("public", "secret"), [("", "sk-lf-1"), ("pk-lf-1", ""), ("", "")])
def test_t1_a_blank_key_disables_tracing(
    monkeypatch: pytest.MonkeyPatch, public: str, secret: str
) -> None:
    monkeypatch.setattr(tracing, "Langfuse", _Recorder)
    tracing.build_client(
        Settings(_env_file=None, langfuse_public_key=public, langfuse_secret_key=secret)
    )
    assert _Recorder.seen == {"tracing_enabled": False}


async def test_t1_a_disabled_client_traces_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    client = tracing.build_client(
        Settings(_env_file=None, langfuse_public_key="", langfuse_secret_key="")
    )
    monkeypatch.setattr(tracing, "_client", client)
    with tracing.debate_trace(debate_id=uuid4(), claim="c", max_rounds=1, entry="api") as root:
        root.update(output={"verdict": "TRUE"})
        assert client.get_current_trace_id() is None
    repo = _FakeRepo()
    debate = await repo.create("c", 1)
    monkeypatch.setattr(graph_mod, "build_graph", lambda: _StubGraph())
    done = await DebateService(repo).run(debate.id, entry="api")
    assert done is not None
    assert done.verdict == "TRUE"


def test_t1_the_client_is_built_once(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[Settings] = []

    def build(cfg: Settings) -> str:
        built.append(cfg)
        return "client"

    monkeypatch.setattr(tracing, "_client", None)
    monkeypatch.setattr(tracing, "build_client", build)
    assert tracing.client() == "client"
    assert tracing.client() == "client"
    assert built == [settings]


def test_t1_shutdown_before_first_use_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing, "_client", None)
    tracing.shutdown()


# ---------------------------------------------------------------------------
# T2 — APP_ENV is a valid Langfuse environment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["production", "test", "dev_1", "pr-12"])
def test_t2_app_env_accepts_langfuse_environments(value: str) -> None:
    assert Settings(_env_file=None, app_env=value).app_env == value


@pytest.mark.parametrize("value", ["Production", "langfuse-ci", "", "x" * 41, "prod env"])
def test_t2_app_env_rejects_what_langfuse_would_drop(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env=value)


# ---------------------------------------------------------------------------
# T3, T4 — usage and cost
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ({}, {}),
        ({"prompt": 10, "completion": 4}, {"input": 10, "output": 4}),
        (
            {"prompt": 10, "completion": 4, "cached": 6},
            {"input": 4, "input_cached_tokens": 6, "output": 4},
        ),
        (
            {"prompt": 10, "completion": 4, "reasoning": 3},
            {"input": 10, "output": 1, "output_reasoning_tokens": 3},
        ),
        ({"prompt": 10, "completion": 4, "cached": 0, "reasoning": 0}, {"input": 10, "output": 4}),
        (
            {"prompt": 10, "completion": 4, "cached": 12, "reasoning": 9},
            {"input": 0, "input_cached_tokens": 10, "output": 0, "output_reasoning_tokens": 4},
        ),
        ({"completion": 4, "cached": 3}, {"output": 4}),
        ({"prompt": 7, "reasoning": 2}, {"input": 7}),
    ],
)
def test_t3_usage_details_are_exclusive_buckets(
    counts: dict[str, int], expected: dict[str, int]
) -> None:
    assert tracing.usage_details(**counts) == expected


@pytest.mark.parametrize(("cost", "expected"), [(0.0, {"total": 0.0}), (0.0012, {"total": 0.0012})])
def test_t4_a_reported_cost_is_the_total(cost: float, expected: dict[str, float]) -> None:
    assert tracing.cost_details(cost) == expected


def test_t4_no_cost_is_no_cost_details() -> None:
    assert tracing.cost_details(None) is None


@pytest.mark.parametrize(
    "usage",
    [
        '{"prompt_tokens": "10", "completion_tokens": 4.5, "cost": "0.1"}',
        '{"prompt_tokens": -3, "completion_tokens": true, "cost": true}',
        # Not JSON, but Python's parser (and so httpx's) reads these.
        '{"prompt_tokens": null, "completion_tokens": null, "cost": NaN}',
        '{"cost": Infinity}',
        '"not-a-mapping"',
        "[1, 2]",
    ],
)
@respx.mock
async def test_t3_t4_unreadable_usage_never_fails_the_call(traces: Traces, usage: str) -> None:
    body = '{"choices": [{"message": {"content": "{\\"x\\": 1}"}}], "usage": ' + usage + "}"
    respx.post(_chat_url()).mock(return_value=httpx.Response(200, content=body.encode()))
    answer = await llm_mod.chat_json(
        [{"role": "user", "content": "u"}], _Answer, name="score-round"
    )
    assert answer.x == 1
    gen = traces.one("score-round")
    assert traces.decoded(gen, "langfuse.observation.usage_details") is None
    assert traces.decoded(gen, "langfuse.observation.cost_details") is None


@respx.mock
async def test_t3_unreadable_details_drop_only_their_bucket(traces: Traces) -> None:
    usage = {
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "prompt_tokens_details": {"cached_tokens": -1},
        "completion_tokens_details": "oops",
    }
    respx.post(_chat_url()).mock(
        return_value=httpx.Response(200, json=_reply('{"x": 1}', usage=usage))
    )
    await llm_mod.chat_json([{"role": "user", "content": "u"}], _Answer, name="score-round")
    gen = traces.one("score-round")
    assert traces.decoded(gen, "langfuse.observation.usage_details") == {"input": 10, "output": 4}


# ---------------------------------------------------------------------------
# T5-T8 — generations
# ---------------------------------------------------------------------------

MESSAGES: list[llm_mod.ChatMessage] = [
    {"role": "system", "content": "be terse"},
    {"role": "user", "content": "answer"},
]


@respx.mock
async def test_t5_an_answered_call_is_one_generation(traces: Traces) -> None:
    usage = {
        "prompt_tokens": 12,
        "completion_tokens": 5,
        "total_tokens": 17,
        "cost": 0,
        "prompt_tokens_details": {"cached_tokens": 2},
        "completion_tokens_details": {"reasoning_tokens": 0},
    }
    respx.post(_chat_url()).mock(
        return_value=httpx.Response(
            200,
            json=_reply(
                '{"x": 1}', model="m/primary:free", provider="Google AI Studio", usage=usage
            ),
        )
    )
    answer = await llm_mod.chat_json(MESSAGES, _Answer, name="decompose-claim")
    assert answer.x == 1
    gen = traces.one("decompose-claim")
    assert traces.type_of(gen) == "generation"
    assert traces.level(gen) == "DEFAULT"
    assert traces.attr(gen, "langfuse.observation.model.name") == "m/primary:free"
    assert traces.decoded(gen, "langfuse.observation.model.parameters") == {
        "temperature": 0.2,
        "response_format": "json_object",
    }
    assert traces.decoded(gen, INPUT) == MESSAGES
    assert traces.decoded(gen, OUTPUT) == {"role": "assistant", "content": '{"x": 1}'}
    assert traces.decoded(gen, "langfuse.observation.usage_details") == {
        "input": 10,
        "input_cached_tokens": 2,
        "output": 5,
    }
    assert traces.decoded(gen, "langfuse.observation.cost_details") == {"total": 0.0}
    assert traces.metadata(gen) == {"tier": "primary", "attempt": 1, "provider": "Google AI Studio"}


@respx.mock
async def test_t5_plain_chat_has_no_response_format(traces: Traces) -> None:
    respx.post(_chat_url()).mock(return_value=httpx.Response(200, json=_reply("an argument")))
    assert await llm_mod.chat(MESSAGES, name="write-argument") == "an argument"
    gen = traces.one("write-argument")
    assert traces.decoded(gen, "langfuse.observation.model.parameters") == {"temperature": 0.2}
    assert traces.metadata(gen) == {"tier": "primary", "attempt": 1}


@respx.mock
async def test_t6_a_retried_attempt_is_its_own_error_generation(traces: Traces) -> None:
    respx.post(_chat_url()).mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json=_reply("ok"))]
    )
    assert await llm_mod.chat(MESSAGES, name="write-argument") == "ok"
    first, second = traces.named("write-argument")
    assert traces.level(first) == "ERROR"
    assert traces.status_message(first) == "rate_limited: status=429"
    assert traces.metadata(first) == {"tier": "primary", "attempt": 1}
    assert traces.level(second) == "DEFAULT"
    assert traces.metadata(second) == {"tier": "primary", "attempt": 2}
    assert traces.decoded(second, OUTPUT) == {"role": "assistant", "content": "ok"}


@respx.mock
async def test_t7_every_tier_failing_is_one_error_generation_per_attempt(traces: Traces) -> None:
    respx.post(_chat_url()).mock(return_value=httpx.Response(400))
    with pytest.raises(LLMError) as exc:
        await llm_mod.chat_json(MESSAGES, _Answer, name="score-round")
    assert exc.value.stage == "all_models_exhausted"
    gens = traces.named("score-round")
    assert [traces.attr(g, "langfuse.observation.model.name") for g in gens] == [
        "m/primary:free",
        "m/fast:free",
        "m/fallback:free",
    ]
    assert [traces.metadata(g)["tier"] for g in gens] == ["primary", "fast", "fallback"]
    assert {traces.level(g) for g in gens} == {"ERROR"}
    assert {traces.status_message(g) for g in gens} == {"client_error: status=400"}


@respx.mock
async def test_t7_attempts_cut_by_the_deadline_are_errors(
    traces: Traces, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "llm_node_deadline_s", 0.3)

    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(10)
        return httpx.Response(200, json=_reply("never"))  # pragma: no cover

    respx.post(_chat_url()).mock(side_effect=hang)
    with pytest.raises(LLMError):
        await llm_mod.chat(MESSAGES, name="write-argument")
    gens = traces.named("write-argument")
    assert [traces.metadata(g)["tier"] for g in gens] == ["primary", "fast", "fallback"]
    assert {traces.level(g) for g in gens} == {"ERROR"}
    assert {traces.status_message(g) for g in gens} == {"cancelled before the model answered"}


@respx.mock
async def test_t8_reasoning_is_kept_on_the_output(traces: Traces) -> None:
    body = {"choices": [{"message": {"content": "ok", "reasoning": "first, think"}}]}
    respx.post(_chat_url()).mock(return_value=httpx.Response(200, json=body))
    await llm_mod.chat(MESSAGES, name="write-argument")
    assert traces.decoded(traces.one("write-argument"), OUTPUT) == {
        "role": "assistant",
        "content": "ok",
        "reasoning": "first, think",
    }


@pytest.mark.parametrize(
    "bad",
    [
        _reply(None),
        _reply("   "),
        {"choices": []},
        {"choices": [{"message": {"content": ["parts"]}}]},
        {"nothing": "here"},
    ],
)
@respx.mock
async def test_t8_a_200_without_a_completion_asks_the_next_tier(
    traces: Traces, bad: dict[str, Any]
) -> None:
    respx.post(_chat_url()).mock(
        side_effect=[httpx.Response(200, json=bad), httpx.Response(200, json=_reply("ok"))]
    )
    assert await llm_mod.chat(MESSAGES, name="write-argument") == "ok"
    first, second = traces.named("write-argument")
    assert traces.level(first) == "ERROR"
    assert str(traces.status_message(first)).startswith("bad_response")
    assert traces.metadata(second)["tier"] == "fast"


@respx.mock
async def test_t8_a_200_that_is_not_json_asks_the_next_tier(traces: Traces) -> None:
    respx.post(_chat_url()).mock(
        side_effect=[httpx.Response(200, text="<html>"), httpx.Response(200, json=_reply("ok"))]
    )
    assert await llm_mod.chat(MESSAGES, name="write-argument") == "ok"
    assert traces.level(traces.named("write-argument")[0]) == "ERROR"


# ---------------------------------------------------------------------------
# T9, T10 — tools
# ---------------------------------------------------------------------------


@respx.mock
async def test_t9_search_web_is_a_tool(traces: Traces) -> None:
    respx.post(TAVILY_URL).mock(
        return_value=httpx.Response(
            200, json={"results": [{"title": "Boiling point", "url": ARTICLE_URL, "content": "s"}]}
        )
    )
    hits = await search("water boiling point")
    assert len(hits) == 1
    tool = traces.one("search-web")
    assert traces.type_of(tool) == "tool"
    assert traces.decoded(tool, INPUT) == {"query": "water boiling point", "k": 5}
    assert traces.decoded(tool, OUTPUT) == {"hit_count": 1, "titles": ["Boiling point"]}


@respx.mock
async def test_t9_a_failed_search_is_an_error(traces: Traces) -> None:
    respx.post(TAVILY_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(ToolError):
        await search("q")
    tool = traces.one("search-web")
    assert traces.level(tool) == "ERROR"
    assert traces.status_message(tool) == "tavily_error: status=500"


@respx.mock
async def test_t10_fetch_article_is_a_tool(traces: Traces) -> None:
    respx.get(ARTICLE_URL).mock(
        return_value=httpx.Response(200, text=FIXTURE_HTML, headers={"content-type": "text/html"})
    )
    doc = await fetch(ARTICLE_URL)
    tool = traces.one("fetch-article")
    assert traces.type_of(tool) == "tool"
    assert traces.decoded(tool, INPUT) == {"url": ARTICLE_URL}
    assert traces.decoded(tool, OUTPUT) == {"char_count": len(doc.text)}


@respx.mock
async def test_t10_a_failed_fetch_is_an_error(traces: Traces) -> None:
    respx.get(ARTICLE_URL).mock(return_value=httpx.Response(404))
    with pytest.raises(ToolError):
        await fetch(ARTICLE_URL)
    tool = traces.one("fetch-article")
    assert traces.level(tool) == "ERROR"
    assert traces.status_message(tool) == "fetch_bad_status: status=404"


@respx.mock
async def test_t10_a_plan_with_failed_searches_is_a_warning(traces: Traces) -> None:
    respx.post(_chat_url()).mock(
        return_value=httpx.Response(
            200, json=_reply(json.dumps({"sub_questions": ["q"], "search_queries": ["q"]}))
        )
    )
    respx.post(TAVILY_URL).mock(return_value=httpx.Response(500))
    out = await plan_mod.plan({"claim": CLAIM})
    assert out["plan"]["evidence"] == []
    node = traces.one("plan-debate")
    assert traces.level(node) == "WARNING"
    assert traces.status_message(node) == "1 of 1 searches or fetches failed"
    assert traces.metadata(node)["failed_queries"] == [
        {"query": "q", "error": "ToolError: tavily_error: status=500"}
    ]


# ---------------------------------------------------------------------------
# T11, T12 — a whole debate through the API
# ---------------------------------------------------------------------------


def _openrouter(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    task = body["messages"][-1]["content"]
    if task.startswith("Decompose this claim"):
        content = json.dumps({"sub_questions": ["Why?"], "search_queries": ["water boiling point"]})
    elif "## Debate so far" in task:
        content = json.dumps({"verdict": "TRUE", "confidence": 0.9, "reasoning": "Agreed."})
    else:
        content = "At sea level it does. [Boiling point](https://example.org/boiling)"
    return httpx.Response(
        200,
        json=_reply(
            content, model=body["model"], usage={"prompt_tokens": 20, "completion_tokens": 5}
        ),
    )


def _stub_debate_http() -> None:
    respx.post(_chat_url()).mock(side_effect=_openrouter)
    respx.post(TAVILY_URL).mock(
        return_value=httpx.Response(
            200, json={"results": [{"title": "Boiling point", "url": ARTICLE_URL, "content": "s"}]}
        )
    )
    respx.get(ARTICLE_URL).mock(
        return_value=httpx.Response(200, text=FIXTURE_HTML, headers={"content-type": "text/html"})
    )


@pytest_asyncio.fixture
async def debate_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[str]:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'debates.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url)
    monkeypatch.setattr(settings, "evidence_fetch_top_n", 1)
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_sessionmaker", None)
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    yield url
    if db_mod._engine is not None:
        await db_mod._engine.dispose()


async def _post_debate(headers: dict[str, str]) -> UUID:
    from paper_trail.main import create_app

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as c:
        resp = await c.post("/debates", json={"claim": CLAIM, "max_rounds": 1}, headers=headers)
    assert resp.status_code == 201, resp.text
    return UUID(resp.json()["debate_id"])


def _trace_wide(traces: Traces, spans: Sequence[ReadableSpan], key: str) -> set[Any]:
    return {traces.attr(s, key) for s in spans}


@respx.mock
async def test_t11_a_debate_is_one_trace_shaped_like_the_debate(
    traces: Traces, debate_db: str
) -> None:
    _stub_debate_http()
    debate_id = await _post_debate({"X-Request-Id": "dossier-7"})

    spans = traces.spans()
    assert len({s.context.trace_id for s in spans}) == 1
    root = traces.one("run-debate")
    assert traces.parent(root) is None
    assert traces.type_of(root) == "agent"
    assert traces.decoded(root, INPUT) == {"claim": CLAIM, "max_rounds": 1}
    assert traces.decoded(root, OUTPUT) == {"verdict": "TRUE", "confidence": 0.9, "rounds_run": 2}

    nodes = traces.children(root)
    assert nodes[0].name == "plan-debate"
    assert nodes[-1].name == "render-transcript"
    assert sorted(n.name for n in nodes) == sorted(
        ["plan-debate", "argue-for", "argue-against", "judge-round", "render-transcript"]
    )
    types = {n.name: traces.type_of(n) for n in nodes}
    assert types == {
        "plan-debate": "agent",
        "argue-for": "agent",
        "argue-against": "agent",
        "judge-round": "agent",
        "render-transcript": "chain",
    }
    by_name = {n.name: n for n in nodes}
    assert sorted(c.name for c in traces.children(by_name["plan-debate"])) == [
        "decompose-claim",
        "fetch-article",
        "search-web",
    ]
    assert [c.name for c in traces.children(by_name["argue-for"])] == ["write-argument"]
    assert [c.name for c in traces.children(by_name["argue-against"])] == ["write-argument"]
    assert [c.name for c in traces.children(by_name["judge-round"])] == ["score-round"]
    assert traces.children(by_name["render-transcript"]) == []

    assert _trace_wide(traces, spans, "langfuse.trace.name") == {"run-debate"}
    assert _trace_wide(traces, spans, "langfuse.trace.tags") == {("paper-trail", "entry:api")}
    assert _trace_wide(traces, spans, "langfuse.environment") == {"test"}
    assert _trace_wide(traces, spans, "session.id") == {"dossier-7"}
    assert _trace_wide(traces, spans, "langfuse.trace.metadata.debate_id") == {
        json.dumps(str(debate_id))
    }


@respx.mock
async def test_t12_no_request_id_no_session(traces: Traces, debate_db: str) -> None:
    _stub_debate_http()
    await _post_debate({})
    spans = traces.spans()
    assert spans
    assert _trace_wide(traces, spans, "session.id") == {None}


# ---------------------------------------------------------------------------
# T13, T14 — failures
# ---------------------------------------------------------------------------


class _FakeDebate:
    def __init__(self, claim: str, max_rounds: int) -> None:
        self.id = uuid4()
        self.claim = claim
        self.max_rounds = max_rounds
        self.status = "pending"
        self.verdict: str | None = None
        self.confidence: float | None = None
        self.rounds: list[dict[str, Any]] = []
        self.transcript_md: str | None = None
        self.evidence_pool: list[dict[str, Any]] | None = None
        self.rounds_struct: list[dict[str, Any]] | None = None
        self.transcript_hash: str | None = None


class _FakeRepo:
    def __init__(self) -> None:
        self.store: dict[UUID, _FakeDebate] = {}

    async def create(
        self, claim: str, max_rounds: int, evidence_pool: list[dict[str, Any]] | None = None
    ) -> _FakeDebate:
        d = _FakeDebate(claim, max_rounds)
        self.store[d.id] = d
        return d

    async def get(self, debate_id: UUID) -> _FakeDebate | None:
        return self.store.get(debate_id)

    async def set_status(self, debate_id: UUID, status: str) -> None:
        self.store[debate_id].status = status

    async def update_rounds(self, debate_id: UUID, rounds: list[dict[str, Any]]) -> None:
        self.store[debate_id].rounds = rounds

    async def update_judge_progress(
        self, debate_id: UUID, *, verdict: str | None, confidence: float | None
    ) -> None:
        self.store[debate_id].verdict = verdict
        self.store[debate_id].confidence = confidence

    async def update_result(self, debate_id: UUID, **fields: Any) -> None:
        d = self.store[debate_id]
        for key, value in fields.items():
            setattr(d, key, value)
        d.status = "done"


class _StubGraph:
    async def astream(
        self, state: dict[str, Any], stream_mode: str = "updates"
    ) -> AsyncIterator[dict[str, Any]]:
        yield {"judge": {"verdict": "TRUE", "confidence": 0.9, "need_more": False, "round": 1}}
        yield {"render": {"transcript_md": "# T"}}


class _FailingGraph:
    async def astream(
        self, state: dict[str, Any], stream_mode: str = "updates"
    ) -> AsyncIterator[dict[str, Any]]:
        raise LLMError("all_models_exhausted", "primary=rate_limited")
        yield {}  # pragma: no cover - makes this an async generator


async def test_t13_a_failed_debate_is_an_error_trace(
    traces: Traces, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _FakeRepo()
    debate = await repo.create("c", 1)
    monkeypatch.setattr(graph_mod, "build_graph", lambda: _FailingGraph())
    with pytest.raises(LLMError):
        await DebateService(repo).run(debate.id, entry="platform")
    assert repo.store[debate.id].status == "error"
    root = traces.one("run-debate")
    assert traces.level(root) == "ERROR"
    assert "all_models_exhausted" in str(traces.status_message(root))
    assert traces.attr(root, "langfuse.trace.tags") == ("paper-trail", "entry:platform")


class _BrokenExporter(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        raise RuntimeError("langfuse is down")

    def shutdown(self) -> None:
        return None


async def test_t14_an_exporter_failure_never_changes_the_debate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = tracing_client(_BrokenExporter())
    monkeypatch.setattr(tracing, "_client", client)
    repo = _FakeRepo()
    debate = await repo.create("c", 1)
    monkeypatch.setattr(graph_mod, "build_graph", lambda: _StubGraph())
    done = await DebateService(repo).run(debate.id, entry="eval")
    client.flush()
    client.shutdown()
    assert done is not None
    assert done.status == "done"
    assert done.verdict == "TRUE"


# ---------------------------------------------------------------------------
# T15 — app shutdown flushes
# ---------------------------------------------------------------------------


async def test_t15_app_shutdown_shuts_tracing_down_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from paper_trail.main import create_app

    calls: list[str] = []
    monkeypatch.setattr(tracing, "shutdown", lambda: calls.append("shutdown"))
    app = create_app()
    async with app.router.lifespan_context(app):
        assert calls == []
    assert calls == ["shutdown"]
