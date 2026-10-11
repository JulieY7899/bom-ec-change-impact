from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from copy import deepcopy
from typing import Any

from .config import (
    OPENAI_API_KEY_ENV,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
)
from .repository import Repository


class AIUnavailable(RuntimeError):
    pass


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(result)
    payload["paths"] = payload.get("paths", [])[:80]
    payload["sales_orders"] = payload.get("sales_orders", [])[:120]
    payload["production_orders"] = payload.get("production_orders", [])[:120]
    history = payload.get("change_history", {})
    history["items"] = history.get("items", [])[:80]
    payload["change_history"] = history
    return payload


def _extract_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"].strip()
    parts: list[str] = []
    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                parts.append(content["text"])
    return "\n".join(parts).strip()


def explain_with_openai(
    result: dict[str, Any],
    repository: Repository,
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> str:
    key = api_key or os.environ.get(OPENAI_API_KEY_ENV, "")
    if not key:
        raise AIUnavailable(
            "补充说明功能尚未配置；当前核查结果和报告不受影响。"
        )

    request_body = {
        "model": model or OPENAI_MODEL,
        "store": False,
        "safety_identifier": repository.privacy_key().safety_identifier(),
        "reasoning": {"effort": "low"},
        "text": {"verbosity": "medium"},
        "instructions": (
            "只根据用户提供的 BOM 工程变更脱敏核查结果编写中文补充说明，"
            "不自行计算 BOM 关系，不补全缺失数据，不批准或执行工程变更。"
            "先说明涉及的成品、订单和待补资料，再按研发、计划、制造列出待办事项；"
            "所有重要内容引用已有路径 ID、订单来源记录号或待确认事项 ID。"
            "明确区分已确认数据、关注等级和待确认事项。"
        ),
        "input": json.dumps(_compact_result(result), ensure_ascii=False),
    }
    request = urllib.request.Request(
        OPENAI_BASE_URL,
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise AIUnavailable(
            f"补充说明服务返回 HTTP {exc.code}，请检查服务配置。"
        ) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise AIUnavailable("无法连接或解析补充说明服务；核查结果不受影响。") from exc
    text = _extract_text(payload)
    if not text:
        raise AIUnavailable("AI 服务未返回可用文本；离线分析结果不受影响。")
    return text
