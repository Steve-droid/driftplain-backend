from typing import Literal

from pydantic import ConfigDict, Field

from app.schemas.base import CamelModel


class SelectionInput(CamelModel):
    model_config = ConfigDict(extra="forbid")
    task: Literal[
        "ci_review",
        "security_analysis",
        "test_generation",
        "ci_failure_diagnosis",
        "other",
    ]
    mode: Literal["single_call", "opencode"]
    language: Literal["python", "node"] | None = None
    propose_fix: bool = False
    runtime_id: int = Field(gt=0)
    observation_id: int = Field(gt=0)
    method: Literal["benchmark_ranked", "supported_unranked"]
    group: str | None = Field(default=None, max_length=64)


class ExplicitProjectCreate(CamelModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    selection: SelectionInput
    review_preferences: str | None = Field(default=None, max_length=2000)


class ExplicitProjectUpdate(CamelModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    selection: SelectionInput | None = None
    review_preferences: str | None = Field(default=None, max_length=2000)
