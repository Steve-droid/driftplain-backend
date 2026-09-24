"""Diagnosis profile identity, bounded redaction and conservative repair policy."""

from dataclasses import dataclass
from pathlib import PurePosixPath
import re

from app.security_contracts import OPENCODE_VERSION


@dataclass(frozen=True)
class DiagnosisProfile:
    propose_fix: bool
    provider: str
    model: str
    credential_env: str
    verification_status: str = "pending"

    @property
    def route(self):
        return f"{self.provider}/{self.model}"

    @property
    def version(self):
        kind = "fix" if self.propose_fix else "readonly"
        return f"diagnosis-{kind}-oc{OPENCODE_VERSION}-v1:{self.model}:low"


DIAGNOSIS_PROFILES = {
    (fix, model): DiagnosisProfile(fix, provider, model, credential)
    for fix in (False, True)
    for provider, model, credential in (
        ("openai", "gpt-5.6-sol", "OPENAI_API_KEY"),
        ("anthropic", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
        ("google", "gemini-3.7-flash", "GEMINI_API_KEY"),
    )
}
AGENT_STAGE = "Driftplain failure diagnosis"


def redact(text, credentials=()):
    """Known credential values plus common log formats; no raw-log persistence."""
    for value in sorted((v for v in credentials if v), key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    text = re.sub(
        r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)",
        "[REDACTED]",
        text,
    )
    text = re.sub(r"(?im)(authorization\s*[:=]\s*)[^\r\n]+", r"\1[REDACTED]", text)
    text = re.sub(
        r"(?i)((?:[\w-]*(?:token|password|passwd|api[_-]?key|credential)[\w-]*)\s*[=:]\s*)[^\s,;]+",
        r"\1[REDACTED]",
        text,
    )
    sensitive = (
        r"(?:[\w-]*(?:token|password|passwd|api[_-]?key|credential|se" + r"cret)[\w-]*)"
    )
    text = re.sub(
        r'(?i)(["\x27]' + sensitive + r'["\x27]\s*:\s*)["\x27][^"\x27\r\n]*["\x27]',
        r'\1"[REDACTED]"',
        text,
    )
    text = re.sub(r"(?i)(--" + sensitive + r"\s+)[^\s]+", r"\1[REDACTED]", text)
    text = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", text)
    text = re.sub(
        r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{12,}|AKIA[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{20,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b",
        "[REDACTED]",
        text,
    )
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)


def validate_fix_paths(paths):
    """V1: exact production source files only; no directories, tests or build controls."""
    for path in paths:
        p = PurePosixPath(path)
        if (
            p.suffix
            not in {
                ".py",
                ".js",
                ".ts",
                ".jsx",
                ".tsx",
                ".java",
                ".go",
                ".rs",
                ".c",
                ".h",
                ".cpp",
            }
            or len(p.parts) < 2
            or p.parts[0] not in {"src", "app", "lib"}
            or any(
                x.startswith(".")
                or re.search(
                    r"(?i)(test|spec|fixture|mock|config|conftest|setup|jenkins|pipeline|lint|coverage|quality|check)",
                    x,
                )
                for x in p.parts
            )
        ):
            raise ValueError(
                "Repair v1 permits exact production source files; tests and quality configuration are protected"
            )
