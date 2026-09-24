"""Pure B8 review profiles and reported usage. No credentials, URLs from catalog or billing."""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel


@dataclass(frozen=True)
class ReviewProfile:
    provider: str
    model: str
    version: str
    credential_env: str
    # Activation requires a separate reviewed change with exact live evidence.
    verification_status: str = "pending"


REVIEW_PROFILES = {
    model: ReviewProfile(provider, model, f"review-http-v1:{model}:low", credential)
    for provider, credential, models in (
        ("openai", "OPENAI_API_KEY", ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")),
        ("anthropic", "ANTHROPIC_API_KEY", ("claude-fable-5-1", "claude-sonnet-5")),
        ("google", "GEMINI_API_KEY", ("gemini-3.7-flash",)),
    )
    for model in models
}

Count = Annotated[int, Field(strict=True, ge=0, le=10_000_000)]


class ProviderUsage(BaseModel):
    """Counts are reported, never estimated. Null means unavailable, zero means reported zero.

    OpenAI: input includes cache, output includes reasoning.
    Anthropic: input excludes cache read/write, output includes reasoning.
    Gemini: input includes cache, candidate output excludes reasoning.
    """

    model_config = ConfigDict(
        extra="forbid", alias_generator=to_camel, populate_by_name=True
    )
    version: Literal[1] = 1
    provider: Literal["openai", "anthropic", "google"]
    profile_version: str = Field(min_length=1, max_length=128)
    input_tokens: Count | None = None
    output_tokens: Count | None = None
    cache_read_tokens: Count | None = None
    cache_write_tokens: Count | None = None
    cache_write_5m_tokens: Count | None = None
    cache_write_1h_tokens: Count | None = None
    reasoning_tokens: Count | None = None
    reported_total_tokens: Count | None = None
    generation_requests: Literal[0, 1] = 1
    transport_retries: Literal[0] = 0
    reported_model_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        exclude_if=lambda value: value is None,
    )
    # Only the effective returned tier, never an assumed request/account default.
    service_tier: (
        Literal["standard", "priority", "flex", "scale", "ultrafast"] | None
    ) = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator(
        "version", "generation_requests", "transport_retries", mode="before"
    )
    @classmethod
    def integer_literal(cls, value):
        if type(value) is not int:
            raise ValueError("Expected an integer contract value")
        return value

    @model_validator(mode="after")
    def consistent_counts(self):
        if self.provider != "anthropic" and self.input_tokens is not None:
            for value in (self.cache_read_tokens, self.cache_write_tokens):
                if value is not None and value > self.input_tokens:
                    raise ValueError("Cache subset exceeds input")
        if (
            self.provider != "google"
            and self.output_tokens is not None
            and self.reasoning_tokens is not None
            and self.reasoning_tokens > self.output_tokens
        ):
            raise ValueError("Reasoning subset exceeds output")
        if self.provider != "anthropic" and (
            self.cache_write_5m_tokens is not None
            or self.cache_write_1h_tokens is not None
        ):
            raise ValueError("Unsupported cache TTL category")
        if self.provider == "google" and self.cache_write_tokens is not None:
            raise ValueError("Gemini does not report cache writes here")
        if (
            self.cache_write_tokens is not None
            and self.cache_write_5m_tokens is not None
            and self.cache_write_1h_tokens is not None
            and self.cache_write_tokens
            != self.cache_write_5m_tokens + self.cache_write_1h_tokens
        ):
            raise ValueError("Cache TTL totals disagree")
        if self.generation_requests == 0 and any(
            v is not None
            for v in (
                self.input_tokens,
                self.output_tokens,
                self.cache_read_tokens,
                self.cache_write_tokens,
                self.reasoning_tokens,
                self.reported_total_tokens,
                self.cache_write_5m_tokens,
                self.cache_write_1h_tokens,
            )
        ):
            raise ValueError("No request cannot have reported usage")
        if self.reported_total_tokens is not None and (
            self.reported_total_tokens < self.captured_tokens
            or (
                self.total_tokens is not None
                and self.reported_total_tokens != self.total_tokens
            )
        ):
            raise ValueError("Reported total disagrees with token categories")
        return self

    @property
    def total_tokens(self) -> int | None:
        values = [self.input_tokens, self.output_tokens]
        if self.provider == "anthropic":
            values += [self.cache_read_tokens, self.cache_write_tokens]
        elif self.provider == "google":
            values += [self.reasoning_tokens]
        return sum(values) if all(v is not None for v in values) else None

    @property
    def captured_tokens(self) -> int:
        # A lower bound only when categories are absent, never a billable total.
        values = [self.input_tokens, self.output_tokens]
        if self.provider == "anthropic":
            values += [self.cache_read_tokens, self.cache_write_tokens]
        elif self.provider == "google":
            values += [self.reasoning_tokens]
        return sum(v or 0 for v in values)
