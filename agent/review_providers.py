"""Fixed native HTTP routes for B8. One POST, no retries, redirects, tools or fallback.

Fixture transports are injected in tests. Pending profiles cannot construct live transport.
The existing SDK adapters remain the legacy/in-cluster seam.
"""

import http.client
import json
import os
from dataclasses import dataclass

from pydantic import ValidationError

from agent.errors import AgentConfigError, CeilingExceeded, ModelRefused, ProviderError
from app.llm.base import LLMResponse
from app.review_contracts import ProviderUsage, ReviewProfile


@dataclass(frozen=True)
class ReviewResponse(LLMResponse):
    usage: ProviderUsage


def _http(*, host, path, headers, body, timeout, max_bytes):
    conn = http.client.HTTPSConnection(host, timeout=timeout)
    try:
        conn.request("POST", path, body=json.dumps(body).encode(), headers=headers)
        response = conn.getresponse()
        raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise CeilingExceeded("output", "provider response exceeds byte limit")
        return response.status, raw
    finally:
        conn.close()


class ReviewProvider:
    def __init__(
        self, profile: ReviewProfile, *, transport=None, timeout=60, max_bytes=1048576
    ):
        if (
            transport is None or transport is _http
        ) and profile.verification_status != "verified":
            raise AgentConfigError("Review integration pending exact live verification")
        self.profile = profile
        self.transport = transport or _http
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.usage = ProviderUsage(
            provider=profile.provider,
            profile_version=profile.version,
            generation_requests=0,
        )

    def complete(self, system, user, max_tokens):
        p = self.profile
        if self.usage.generation_requests:
            raise AgentConfigError("Review profile permits only one generation request")
        if type(max_tokens) is not int or not 1 <= max_tokens <= 16384:
            raise AgentConfigError("Review output token limit must be 1..16384")
        key = os.environ.get(p.credential_env, "")
        if self.transport is _http and not key:
            raise AgentConfigError("Required provider credential is missing")
        headers = {"Content-Type": "application/json"}
        if p.provider == "openai":
            host, path = "api.openai.com", "/v1/responses"
            headers["Authorization"] = "Bearer " + key
            body = {
                "model": p.model,
                "instructions": system,
                "input": user,
                "max_output_tokens": max_tokens,
                "reasoning": {"effort": "low"},
                "store": False,
                "truncation": "disabled",
            }
        elif p.provider == "anthropic":
            host, path = "api.anthropic.com", "/v1/messages"
            headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
            body = {
                "model": p.model,
                "system": system,
                "messages": [{"role": "user", "content": user}],
                "max_tokens": max_tokens,
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "low"},
            }
        elif p.provider == "google":
            host, path = (
                "generativelanguage.googleapis.com",
                f"/v1beta/models/{p.model}:generateContent",
            )
            headers["x-goog-api-key"] = key
            body = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {
                    "maxOutputTokens": max_tokens,
                    "candidateCount": 1,
                    "thinkingConfig": {"thinkingLevel": "LOW"},
                },
            }
        else:
            raise AgentConfigError("Unsupported review provider")
        self.usage = self.usage.model_copy(update={"generation_requests": 1})
        try:
            status, raw = self.transport(
                host=host,
                path=path,
                headers=headers,
                body=body,
                timeout=self.timeout,
                max_bytes=self.max_bytes,
            )
            if len(raw) > self.max_bytes:
                raise CeilingExceeded("output", "provider response exceeds byte limit")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise TypeError("not an object")
            if status != 200:
                error = data.get("error") or {}
                code = error.get("code")
                message = str(error.get("message", "")).lower()
                if (
                    status == 413
                    or code
                    in ("context_length_exceeded", "model_context_window_exceeded")
                    or (
                        status == 400
                        and (
                            "prompt is too long" in message
                            or "maximum context length" in message
                            or ("input token count" in message and "exceeds" in message)
                        )
                    )
                ):
                    raise CeilingExceeded("context", "provider rejected context size")
                raise ProviderError(f"Provider HTTP {status}; request not retried")
            self.usage = self._usage(data)
            text = self._text(data)
            actual = data.get("modelVersion" if p.provider == "google" else "model")
            if actual != p.model:
                raise ProviderError("Provider returned unverified model identity")
            return ReviewResponse(
                text=text,
                model=p.model,
                usage=self.usage,
                tokens_in=self.usage.input_tokens or 0,
                tokens_out=self.usage.output_tokens or 0,
            )
        except TimeoutError:
            raise CeilingExceeded("wall-clock", "provider request timed out") from None
        except (CeilingExceeded, ModelRefused, ProviderError):
            raise
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            IndexError,
            ValidationError,
            RecursionError,
        ):
            raise ProviderError("Invalid provider response contract") from None
        except (OSError, http.client.HTTPException):
            raise ProviderError(
                "Provider transport failed; request not retried"
            ) from None

    def _usage(self, data):
        p = self.profile.provider
        u = data.get("usageMetadata" if p == "google" else "usage") or {}
        raw_tier = (
            data.get("service_tier")
            if p == "openai"
            else u.get("service_tier" if p == "anthropic" else "serviceTier")
        )
        tier_maps = {
            "openai": {
                "default": "standard",
                "priority": "priority",
                "flex": "flex",
                "scale": "scale",
                "ultrafast": "ultrafast",
            },
            "anthropic": {"standard": "standard", "priority": "priority"},
            "google": {"standard": "standard", "priority": "priority", "flex": "flex"},
        }
        tier = tier_maps[p].get(raw_tier) if isinstance(raw_tier, str) else None
        common = {
            "provider": p,
            "profile_version": self.profile.version,
            "service_tier": tier,
            "reported_model_id": data.get("modelVersion" if p == "google" else "model"),
        }
        if p == "openai":
            i, o = (
                u.get("input_tokens_details") or {},
                u.get("output_tokens_details") or {},
            )
            return ProviderUsage(
                **common,
                input_tokens=u.get("input_tokens"),
                output_tokens=u.get("output_tokens"),
                cache_read_tokens=i.get("cached_tokens"),
                cache_write_tokens=i.get("cache_write_tokens"),
                reasoning_tokens=o.get("reasoning_tokens"),
                reported_total_tokens=u.get("total_tokens"),
            )
        if p == "anthropic":
            c, o = u.get("cache_creation") or {}, u.get("output_tokens_details") or {}
            return ProviderUsage(
                **common,
                input_tokens=u.get("input_tokens"),
                output_tokens=u.get("output_tokens"),
                cache_read_tokens=u.get("cache_read_input_tokens"),
                cache_write_tokens=u.get("cache_creation_input_tokens"),
                cache_write_5m_tokens=c.get("ephemeral_5m_input_tokens"),
                cache_write_1h_tokens=c.get("ephemeral_1h_input_tokens"),
                reasoning_tokens=o.get("thinking_tokens"),
            )
        return ProviderUsage(
            **common,
            input_tokens=u.get("promptTokenCount"),
            output_tokens=u.get("candidatesTokenCount"),
            cache_read_tokens=u.get("cachedContentTokenCount"),
            reasoning_tokens=u.get("thoughtsTokenCount"),
            reported_total_tokens=u.get("totalTokenCount"),
        )

    def _text(self, d):
        p = self.profile.provider
        if p == "openai":
            if any(
                item["type"] not in ("message", "reasoning") for item in d["output"]
            ):
                raise ProviderError("Unexpected provider output type")
            blocks = [
                b
                for item in d["output"]
                if item["type"] == "message"
                for b in item["content"]
            ]
            if any(b["type"] == "refusal" for b in blocks):
                raise ModelRefused("Provider refused the review")
            if d["status"] == "incomplete":
                if (d.get("incomplete_details") or {}).get(
                    "reason"
                ) == "content_filter":
                    raise ModelRefused("Provider filtered the review")
                raise CeilingExceeded("output", "provider response incomplete")
            if d["status"] != "completed":
                raise ProviderError("Provider did not complete the review")
            return "".join(b["text"] for b in blocks if b["type"] == "output_text")
        if p == "anthropic":
            reason = d["stop_reason"]
            if reason == "refusal":
                raise ModelRefused("Provider refused the review")
            if reason in ("max_tokens", "model_context_window_exceeded"):
                raise CeilingExceeded("output", "provider response incomplete")
            if reason != "end_turn":
                raise ProviderError("Unexpected provider stop reason")
            return "".join(b["text"] for b in d["content"] if b["type"] == "text")
        if d.get("promptFeedback", {}).get("blockReason"):
            raise ModelRefused("Provider blocked the review")
        candidates = d["candidates"]
        if len(candidates) != 1:
            raise ProviderError("Expected exactly one response candidate")
        c = candidates[0]
        reason = c["finishReason"]
        if reason in (
            "SAFETY",
            "RECITATION",
            "BLOCKLIST",
            "PROHIBITED_CONTENT",
            "SPII",
        ):
            raise ModelRefused("Provider refused the review")
        if reason == "MAX_TOKENS":
            raise CeilingExceeded("output", "provider response incomplete")
        if reason != "STOP":
            raise ProviderError("Unexpected provider stop reason")
        return "".join(
            b["text"]
            for b in c["content"]["parts"]
            if "text" in b and not b.get("thought")
        )
