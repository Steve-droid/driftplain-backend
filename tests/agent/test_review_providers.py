"""B8 uses synthetic HTTP responses only. These tests never activate a model."""

import json

import pytest

from agent.errors import AgentConfigError, CeilingExceeded, ModelRefused, ProviderError
from agent.review_providers import ReviewProvider
from app.review_contracts import REVIEW_PROFILES


def response(provider, model, text='{"findings": []}'):
    if provider == "openai":
        return {
            "model": model,
            "status": "completed",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": text}]}
            ],
            "usage": {
                "input_tokens": 100,
                "input_tokens_details": {"cached_tokens": 20, "cache_write_tokens": 10},
                "output_tokens": 50,
                "output_tokens_details": {"reasoning_tokens": 30},
            },
        }
    if provider == "anthropic":
        return {
            "model": model,
            "stop_reason": "end_turn",
            "content": [
                {"type": "thinking", "thinking": "do not record"},
                {"type": "text", "text": text},
            ],
            "usage": {
                "input_tokens": 100,
                "cache_read_input_tokens": 20,
                "cache_creation_input_tokens": 10,
                "output_tokens": 50,
                "output_tokens_details": {"thinking_tokens": 30},
            },
        }
    return {
        "modelVersion": model,
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {"thought": True, "text": "do not record"},
                        {"text": text},
                    ]
                },
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 100,
            "cachedContentTokenCount": 20,
            "candidatesTokenCount": 50,
            "thoughtsTokenCount": 30,
        },
    }


class Transport:
    def __init__(self, body, status=200):
        self.body, self.status, self.calls = body, status, []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.status, json.dumps(self.body).encode()


@pytest.mark.parametrize("model", list(REVIEW_PROFILES))
def test_exact_request_and_reported_usage(model):
    p = REVIEW_PROFILES[model]
    t = Transport(response(p.provider, model))
    r = ReviewProvider(p, transport=t).complete("system", "diff", 1024)
    assert r.text == '{"findings": []}'
    assert r.model == model
    assert len(t.calls) == 1
    call = t.calls[0]
    assert (
        call["body"]["model"] == model
        if p.provider != "google"
        else model in call["path"]
    )
    assert "temperature" not in call["body"]
    assert r.usage.cache_read_tokens == 20
    assert r.usage.reasoning_tokens == 30
    assert r.usage.total_tokens == (
        180 if p.provider in ("anthropic", "google") else 150
    )
    if p.provider == "openai":
        assert call["body"]["max_output_tokens"] == 1024
        assert call["body"]["store"] is False
        assert call["body"]["reasoning"] == {"effort": "low"}
    elif p.provider == "anthropic":
        assert call["body"]["thinking"] == {"type": "adaptive"}
        assert "tool_choice" not in call["body"]
    else:
        assert call["body"]["generationConfig"]["thinkingConfig"] == {
            "thinkingLevel": "LOW"
        }


@pytest.mark.parametrize("model", list(REVIEW_PROFILES))
def test_pending_profiles_cannot_construct_live_transport(model):
    with pytest.raises(AgentConfigError, match="pending"):
        ReviewProvider(REVIEW_PROFILES[model])
    from agent.review_providers import _http

    with pytest.raises(AgentConfigError, match="pending"):
        ReviewProvider(REVIEW_PROFILES[model], transport=_http)


def test_https_transport_uses_fixed_route_and_closes_on_oversize(monkeypatch):
    from dataclasses import replace

    p = replace(REVIEW_PROFILES["gpt-5.6-sol"], verification_status="verified")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-key")
    calls = []

    class Connection:
        def __init__(self, host, timeout):
            calls.append((host, timeout))

        def request(self, method, path, body, headers):
            calls.append((method, path, json.loads(body)))

        def getresponse(self):
            return self

        status = 200

        def read(self, limit):
            return b"x" * limit

        def close(self):
            calls.append("closed")

    monkeypatch.setattr("http.client.HTTPSConnection", Connection)
    with pytest.raises(CeilingExceeded):
        ReviewProvider(p, timeout=5, max_bytes=16).complete("s", "u", 100)
    assert calls[0] == ("api.openai.com", 5)
    assert calls[1][0:2] == ("POST", "/v1/responses")
    assert calls[-1] == "closed" and len(calls) == 3


@pytest.mark.parametrize(
    "provider,model",
    [
        ("openai", "gpt-5.6-sol"),
        ("anthropic", "claude-sonnet-5"),
        ("google", "gemini-3.7-flash"),
    ],
)
def test_refusal_context_and_mismatched_identity(provider, model):
    body = response(provider, model)
    if provider == "openai":
        body["output"][0]["content"] = [{"type": "refusal", "refusal": "private text"}]
    elif provider == "anthropic":
        body["stop_reason"] = "refusal"
    else:
        body["candidates"][0]["finishReason"] = "SAFETY"
    with pytest.raises(ModelRefused):
        ReviewProvider(REVIEW_PROFILES[model], transport=Transport(body)).complete(
            "s", "u", 100
        )
    body = response(provider, model)
    body["modelVersion" if provider == "google" else "model"] = "different-model"
    with pytest.raises(ProviderError, match="identity"):
        ReviewProvider(REVIEW_PROFILES[model], transport=Transport(body)).complete(
            "s", "u", 100
        )
    t = Transport(
        {"error": {"code": "context_length_exceeded", "message": "private diff"}}, 400
    )
    with pytest.raises(CeilingExceeded):
        ReviewProvider(REVIEW_PROFILES[model], transport=t).complete("s", "u", 100)
    assert len(t.calls) == 1


@pytest.mark.parametrize("status", [400, 401, 429, 500, 302])
def test_http_errors_never_retry_or_echo_body(status):
    t = Transport({"error": {"message": "private diff"}}, status)
    with pytest.raises(ProviderError) as e:
        ReviewProvider(REVIEW_PROFILES["gpt-5.6-sol"], transport=t).complete(
            "s", "u", 100
        )
    assert "private" not in str(e.value)
    assert len(t.calls) == 1


def test_timeout_and_unsupported_options():
    def timeout(**kw):
        raise TimeoutError("private data")

    with pytest.raises(CeilingExceeded):
        ReviewProvider(REVIEW_PROFILES["gpt-5.6-sol"], transport=timeout).complete(
            "s", "u", 100
        )
    with pytest.raises(TypeError):
        ReviewProvider(REVIEW_PROFILES["gpt-5.6-sol"], endpoint="https://example.test")


def test_unknown_usage_is_not_zero_and_negative_is_rejected():
    body = response("openai", "gpt-5.6-sol")
    body.pop("usage")
    r = ReviewProvider(
        REVIEW_PROFILES["gpt-5.6-sol"], transport=Transport(body)
    ).complete("s", "u", 100)
    assert r.usage.input_tokens is None and r.usage.total_tokens is None
    body["usage"] = {"input_tokens": -1, "output_tokens": 2}
    with pytest.raises(ProviderError):
        ReviewProvider(
            REVIEW_PROFILES["gpt-5.6-sol"], transport=Transport(body)
        ).complete("s", "u", 100)


def test_blocked_gemini_without_model_version_is_refusal():
    with pytest.raises(ModelRefused):
        ReviewProvider(
            REVIEW_PROFILES["gemini-3.7-flash"],
            transport=Transport({"promptFeedback": {"blockReason": "SAFETY"}}),
        ).complete("s", "u", 100)


def test_one_generation_even_if_caller_accidentally_reuses_client():
    p = REVIEW_PROFILES["gpt-5.6-sol"]
    t = Transport(response(p.provider, p.model))
    c = ReviewProvider(p, transport=t)
    c.complete("s", "u", 100)
    with pytest.raises(AgentConfigError, match="one generation"):
        c.complete("s", "u", 100)
    assert len(t.calls) == 1


@pytest.mark.parametrize(
    "model,error",
    [
        (
            "claude-sonnet-5",
            {
                "type": "invalid_request_error",
                "message": "prompt is too long: private payload",
            },
        ),
        (
            "gemini-3.7-flash",
            {
                "code": 400,
                "status": "INVALID_ARGUMENT",
                "message": "The input token count exceeds the maximum number of tokens allowed: private payload",
            },
        ),
    ],
)
def test_native_context_errors_are_classified_without_echo(model, error):
    t = Transport({"error": error}, 400)
    with pytest.raises(CeilingExceeded) as exc:
        ReviewProvider(REVIEW_PROFILES[model], transport=t).complete("s", "u", 100)
    assert "private" not in str(exc.value)


@pytest.mark.parametrize("model", list(REVIEW_PROFILES))
def test_provider_truncation_is_never_a_clean_review(model):
    p = REVIEW_PROFILES[model]
    body = response(p.provider, model)
    if p.provider == "openai":
        body["status"] = "incomplete"
        body["incomplete_details"] = {"reason": "max_output_tokens"}
    elif p.provider == "anthropic":
        body["stop_reason"] = "max_tokens"
    else:
        body["candidates"][0]["finishReason"] = "MAX_TOKENS"
    with pytest.raises(CeilingExceeded):
        ReviewProvider(p, transport=Transport(body)).complete("s", "u", 100)
