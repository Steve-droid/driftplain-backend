"""Independent B10 task/mode candidates. Fixtures never confer live support."""

from dataclasses import dataclass

from app.security_contracts import OPENCODE_VERSION


@dataclass(frozen=True)
class OtherProfile:
    provider: str
    model: str
    credential_env: str
    mode: str
    verification_status: str = "pending"

    @property
    def route(self):
        return f"{self.provider}/{self.model}"

    @property
    def version(self):
        transport = "http" if self.mode == "single_call" else f"oc{OPENCODE_VERSION}"
        return f"other-{transport}-v1:{self.model}:low"


OTHER_PROFILES = {
    (mode, model): OtherProfile(provider, model, credential, mode)
    for provider, model, credential in (
        ("openai", "gpt-5.6-sol", "OPENAI_API_KEY"),
        ("anthropic", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
        ("google", "gemini-3.7-flash", "GEMINI_API_KEY"),
    )
    for mode in ("single_call", "opencode")
}
