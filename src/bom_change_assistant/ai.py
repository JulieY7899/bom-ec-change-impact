"""External LLM access is intentionally disabled in the privacy-first build.

All BOM analysis and reporting stay local. Reintroducing a model requires a
separate, reviewed data-sharing design and explicit consent.
"""
from __future__ import annotations

from typing import Any

from .repository import Repository


class AIUnavailable(RuntimeError):
    pass


def explain_with_openai(
    result: dict[str, Any],
    repository: Repository,
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> str:
    raise AIUnavailable("外部 AI 说明功能已禁用；BOM 规则核查与本地报告不受影响。")
