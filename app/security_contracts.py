"""Pinned B9 candidate profiles and OpenCode-normalized usage; no activation or billing."""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

OPENCODE_VERSION = "1.18.20"
OPENCODE_REVISION = "7248bc1964b13fa67e601733f89ee9dc6dfa0563"
OPENCODE_BINARY_SHA256 = (
    "5dce99ea079d925736e332b20f5bf869fe9a1fa67dc0a09027156b0ed8e41b16"
)
OPENCODE_SHA256 = "8603214aa1e2e18f9312360c9360dfae6174f5cff6dc4f72b6045c80608af4d4"


@dataclass(frozen=True)
class SecurityProfile:
    provider: str
    model: str
    credential_env: str
    verification_status: str = "pending"

    @property
    def route(self):
        return f"{self.provider}/{self.model}"

    @property
    def version(self):
        return f"security-oc{OPENCODE_VERSION}-v1:{self.model}:low"


SECURITY_PROFILES = {
    model: SecurityProfile(provider, model, credential)
    for provider, credential, models in (
        ("openai", "OPENAI_API_KEY", ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")),
        ("anthropic", "ANTHROPIC_API_KEY", ("claude-fable-5-1", "claude-sonnet-5")),
        ("google", "GEMINI_API_KEY", ("gemini-3.7-flash",)),
    )
    for model in models
}
Count = Annotated[int, Field(ge=0, le=10_000_000)]


class RunnerUsage(BaseModel):
    """Disjoint normalized buckets, NOT native provider usage.

    The pinned runner fills absent categories with zero. Even a complete event stream
    cannot establish complete provider billing or exact HTTP request/retry counts.
    """

    model_config = ConfigDict(
        extra="forbid", strict=True, alias_generator=to_camel, populate_by_name=True
    )
    version: Literal[1] = 1
    runner_version: Literal["1.18.20"] = OPENCODE_VERSION
    provider: Literal["openai", "anthropic", "google"]
    profile_version: str = Field(min_length=1, max_length=128)
    input_tokens: Count | None = None
    output_tokens: Count | None = None
    reasoning_tokens: Count | None = None
    cache_read_tokens: Count | None = None
    cache_write_tokens: Count | None = None
    reported_total_tokens: Count | None = None
    attempts: int = Field(ge=0, le=3)
    completed_steps: int = Field(ge=0, le=1000)
    tool_calls: int = Field(ge=0, le=1000)
    generation_requests: None = None
    transport_retries: None = None
    billing_complete: Literal[False] = False

    @field_validator("version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int:
            raise ValueError("Expected integer version")
        return value

    @field_validator("billing_complete", mode="before")
    @classmethod
    def strict_completeness(cls, value):
        if value is not False:
            raise ValueError("Runner cannot establish billing completeness")
        return value

    @property
    def captured_tokens(self):
        return sum(
            v or 0
            for v in (
                self.input_tokens,
                self.output_tokens,
                self.reasoning_tokens,
                self.cache_read_tokens,
                self.cache_write_tokens,
            )
        )

    @property
    def complete(self):
        return (
            self.attempts > 0
            and self.completed_steps > 0
            and all(
                v is not None
                for v in (
                    self.input_tokens,
                    self.output_tokens,
                    self.reasoning_tokens,
                    self.cache_read_tokens,
                    self.cache_write_tokens,
                    self.reported_total_tokens,
                )
            )
        )

    @model_validator(mode="after")
    def counts(self):
        if (
            self.reported_total_tokens is not None
            and self.reported_total_tokens < self.captured_tokens
        ):
            raise ValueError("Runner total is below captured categories")
        if self.attempts == 0 and (
            self.completed_steps or self.tool_calls or self.captured_tokens
        ):
            raise ValueError("No attempt cannot report runner activity")
        return self
