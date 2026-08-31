from types import SimpleNamespace

from agent import parallel_context


def _agent():
    return SimpleNamespace(
        provider="lmstudio",
        model="ornith-1.5-9b-uncensored",
        base_url="http://127.0.0.1:1234/v1",
        api_key="",
        api_mode="chat_completions",
    )


def test_split_request_preserves_order_and_overlap():
    config = parallel_context.resolve_config(
        {"chunk_chars": 2000, "max_chunk_chars": 3000, "max_chunks": 8, "chunk_overlap_chars": 200}
    )
    chunks = parallel_context.split_request(("alpha beta gamma delta epsilon\n\n" * 180) + "zeta eta theta iota", config)

    assert chunks is not None
    assert chunks[0].startswith("alpha")
    assert chunks[-1].endswith("iota")
    assert len(chunks) > 1


def test_split_request_respects_max_chunks_with_overlap():
    config = parallel_context.resolve_config(
        {"chunk_chars": 2000, "max_chunk_chars": 3000, "max_chunks": 4, "chunk_overlap_chars": 200}
    )

    chunks = parallel_context.split_request("A" * 8000, config)

    assert chunks is not None
    assert len(chunks) <= 4


def test_prepare_request_runs_workers_then_reducer(monkeypatch):
    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        text = kwargs["messages"][-1]["content"]
        content = "CONSOLIDATED" if "Worker 1:" in text else f"BRIEF:{text[:12]}"
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    monkeypatch.setattr(parallel_context, "call_llm", fake_call)
    result = parallel_context.prepare_request(
        "A" * 8000,
        _agent(),
        {
            "min_input_chars": 1000,
            "chunk_chars": 2000,
            "max_chunks": 8,
            "max_workers": 4,
            "chunk_overlap_chars": 0,
        },
    )

    assert result is not None
    assert result.endswith("CONSOLIDATED")
    assert len(calls) == 5  # four workers plus a single reducer
    assert all(call["tools"] is None for call in calls)


def test_prepare_request_fails_open_when_workers_fail(monkeypatch):
    monkeypatch.setattr(parallel_context, "call_llm", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))

    result = parallel_context.prepare_request(
        "A" * 8000,
        _agent(),
        {"min_input_chars": 1000, "chunk_chars": 2000, "max_chunks": 8, "chunk_overlap_chars": 0},
    )

    assert result is None


def test_prepare_request_skips_non_lmstudio_provider(monkeypatch):
    monkeypatch.setattr(parallel_context, "call_llm", lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not call")))
    agent = _agent()
    agent.provider = "openrouter"

    assert parallel_context.prepare_request("A" * 8000, agent, {"min_input_chars": 1000}) is None


def test_string_false_disables_parallel_context():
    config = parallel_context.resolve_config({"enabled": "false"})

    assert config["enabled"] is False
