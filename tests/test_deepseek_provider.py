"""Transport contracts only: no real API calls, secrets or retained response samples."""

import asyncio
import copy
import json

import pytest

from careerpilot import deepseek_provider as ds
from careerpilot.gateway import EmptyModelResponse, ProviderReply, structured_envelope


def response():
    return {
        "model": ds.MODEL,
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": '{"risk":false}',
                    "reasoning_content": "private-reasoning-canary",
                },
            }
        ],
        "usage": {"prompt_tokens": 40, "completion_tokens": 5, "total_tokens": 45},
    }


class Connection:
    status = 200
    raw = b""
    failure = None
    instances = []

    def __init__(self, host, timeout):
        self.host, self.timeout, self.closed = host, timeout, False
        self.requests = []
        self.instances.append(self)

    def request(self, *args):
        self.requests.append(args)
        if self.failure:
            raise self.failure

    def getresponse(self):
        return self

    def read(self, maximum):
        return self.raw[:maximum]

    def close(self):
        self.closed = True


@pytest.fixture
def connection(monkeypatch):
    monkeypatch.setattr(ds.http.client, "HTTPSConnection", Connection)
    monkeypatch.setattr(Connection, "instances", [])
    monkeypatch.setattr(Connection, "raw", json.dumps(response()).encode())
    monkeypatch.setattr(Connection, "failure", None)
    monkeypatch.setattr(Connection, "status", 200)
    return Connection


def generate():
    return asyncio.run(
        ds.DeepSeekProvider("synthetic-credential-canary").generate(
            structured_envelope("private-input-canary", {"type": "object"}), 256
        )
    )


def test_fixed_authority_json_non_thinking_bounded_no_tools_and_no_reasoning(connection):
    reply = generate()
    assert reply == ProviderReply('{"risk":false}', 40, 5)
    assert "private-reasoning-canary" not in repr(reply)
    assert len(connection.instances) == 1
    wire = connection.instances[0]
    assert wire.host == "api.deepseek.com" and wire.timeout == 8 and wire.closed
    method, path, raw, headers = wire.requests[0]
    assert (method, path) == ("POST", "/chat/completions")
    assert headers["Authorization"] == "Bearer synthetic-credential-canary"
    body = json.loads(raw)
    assert body["model"] == "deepseek-flash" and body["max_tokens"] == 256
    assert body["thinking"] == {"type": "disabled"}
    assert body["temperature"] == 0 and body["stream"] is False
    assert body["response_format"] == {"type": "json_object"}
    assert "JSON" in body["messages"][0]["content"]
    assert "tools" not in body and "reasoning_content" not in body
    assert ds.DeepSeekProvider.external is True


@pytest.mark.parametrize("status", [301, 401, 402, 429, 500])
def test_http_errors_never_follow_redirect_retry_or_echo_provider_body(connection, status):
    connection.status = status
    connection.raw = b"private-input-canary synthetic-credential-canary"
    with pytest.raises(RuntimeError) as error:
        generate()
    assert str(error.value) == "DeepSeek request failed"
    assert len(connection.instances) == 1 and connection.instances[0].closed
    assert len(connection.instances[0].requests) == 1


def test_timeout_preserved_but_other_transport_exception_is_redacted(connection):
    for failure, expected in [
        (TimeoutError("private"), TimeoutError),
        (OSError("private"), RuntimeError),
    ]:
        connection.failure = failure
        with pytest.raises(expected) as error:
            generate()
        assert "private" not in str(error.value)
        assert connection.instances[-1].closed


@pytest.mark.parametrize(
    "change", ["boolean", "missing", "total", "length", "tools", "model", "empty", "oversized"]
)
def test_missing_usage_partial_output_wrong_model_and_tools_fail_closed(connection, change):
    value = copy.deepcopy(response())
    if change == "boolean":
        value["usage"]["prompt_tokens"] = True
    if change == "missing":
        del value["usage"]
    if change == "total":
        value["usage"]["total_tokens"] = 0
    if change == "length":
        value["choices"][0]["finish_reason"] = "length"
    if change == "tools":
        value["choices"][0]["message"]["tool_calls"] = [{"name": "unexpected"}]
    if change == "model":
        value["model"] = "unexpected-model"
    if change == "empty":
        value["choices"][0]["message"]["content"] = ""
    connection.raw = json.dumps(value).encode() if change != "oversized" else b"x" * 131073
    with pytest.raises(ValueError):
        generate()
    assert connection.instances[-1].closed


def test_key_configuration_only_selects_one_credential_without_exporting_other_values(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    path = tmp_path / ".env"
    path.write_text('UNRELATED=private-unrelated\nDEEPSEEK_API_KEY="synthetic-credential-canary"\n')
    assert ds.configured_key(path) == "synthetic-credential-canary"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-wins")
    assert ds.configured_key(path) == "environment-wins"
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    path.write_text("DEEPSEEK_API_KEY=first\nDEEPSEEK_API_KEY=second\n")
    with pytest.raises(ValueError):
        ds.configured_key(path)
    path.write_text("DEEPSEEK_API_KEY=\n")
    with pytest.raises(ValueError):
        ds.configured_key(path)


def test_bad_limits_or_header_injection_cannot_make_a_request(connection):
    with pytest.raises(ValueError):
        ds.DeepSeekProvider("bad\r\nInjected: header")
    for limit in (True, 0, 4097):
        with pytest.raises(ValueError):
            asyncio.run(ds.DeepSeekProvider("synthetic").generate("{}", limit))
    assert not connection.instances


def test_whitespace_reply_is_rejected_but_validated_usage_is_preserved(connection):
    value = response()
    value["choices"][0]["message"]["content"] = "\n\t "
    connection.raw = json.dumps(value).encode()
    with pytest.raises(EmptyModelResponse) as error:
        generate()
    assert (error.value.input_tokens, error.value.output_tokens) == (40, 5)
    assert "content" not in error.value.__dict__
    assert connection.instances[-1].closed
