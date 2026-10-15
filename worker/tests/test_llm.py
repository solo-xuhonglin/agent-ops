import pytest
from worker.llm import FakeLlm, LlmDelta, LlmResult, llm_from_env


def drain(stream):
    deltas, done = [], None
    for event in stream:
        if isinstance(event, LlmDelta):
            deltas.append(event.text)
        else:
            done = event.result
    return deltas, done


def test_scripted_reply_streams_deltas_then_done():
    llm = FakeLlm([(["你好", "世界"], LlmResult(content="你好世界", input_tokens=4, output_tokens=4, cost=0.0))])

    deltas, done = drain(
        llm.stream([{"role": "user", "content": "hi"}], model_id="m", model_params={"temperature": 0.2}, tools=[])
    )

    assert deltas == ["你好", "世界"]
    assert done == LlmResult(content="你好世界", input_tokens=4, output_tokens=4, cost=0.0)


def test_calls_recorded_and_replies_popped_in_order():
    llm = FakeLlm(
        [
            (["a"], LlmResult(content="a")),
            (["b"], LlmResult(content="b")),
        ]
    )
    messages = [{"role": "user", "content": "hi"}]

    drain(llm.stream(messages, model_id="m1", model_params={}, tools=[]))
    drain(llm.stream(messages, model_id="m2", model_params={}, tools=[]))

    assert [(call[1], call[2]) for call in llm.calls] == [("m1", {}), ("m2", {})]
    assert llm.calls[0][0] is messages


def test_default_reply_echoes_last_user_message():
    llm = FakeLlm()

    deltas, done = drain(
        llm.stream(
            [{"role": "system", "content": "s"}, {"role": "user", "content": "hello"}],
            model_id="m",
            model_params={},
            tools=[],
        )
    )

    assert "".join(deltas) == "fake: hello"
    assert done.content == "fake: hello"
    assert done.input_tokens == max(1, len("s hello") // 4)
    assert done.output_tokens == max(1, len("fake: hello") // 4)
    assert done.cost == 0.0
    assert len(deltas) == 2


def test_tool_calls_pass_through():
    llm = FakeLlm([([""], LlmResult(content="", tool_calls=[{"name": "t", "arguments": {}}]))])

    _, done = drain(llm.stream([{"role": "user", "content": "x"}], model_id="m", model_params={}, tools=[]))

    assert done.tool_calls == [{"name": "t", "arguments": {}}]


def test_llm_from_env(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert isinstance(llm_from_env(), FakeLlm)
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    assert isinstance(llm_from_env(), FakeLlm)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(ValueError):
        llm_from_env()
