"""固定人民币单价的模型用量与参考费用计算。"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

DEFAULT_PRICING = {
    "selected_profile": "deepseek-flash-cny",
    "profiles": [
        {"id": "deepseek-flash-cny", "name": "DeepSeek Flash（官网固定参考）",
         "uncached_input": 2.0, "cached_input": 0.04, "output": 8.0,
         "note": "DeepSeek 中文官网高峰单价；固定计算，不按时段区分"},
        {"id": "deepseek-pro-cny", "name": "DeepSeek V4 Pro（官网固定参考）",
         "uncached_input": 9.0, "cached_input": 0.30, "output": 27.0,
         "note": "DeepSeek 中文官网高峰单价；固定计算，不按时段区分"},
    ],
}


def selected_profile(pricing: dict[str, Any]) -> dict[str, Any] | None:
    return next((item for item in pricing.get("profiles", [])
                 if item.get("id") == pricing.get("selected_profile")), None)


def estimate_cny(usage: dict[str, Any], profile: dict[str, Any] | None) -> float | None:
    """DSH 的 inputTokens 与 cacheRead/WriteTokens 是互不重叠的分项。"""
    if not profile or not usage or "inputTokens" not in usage or "outputTokens" not in usage:
        return None
    number = lambda name: Decimal(str(usage.get(name, 0) or 0))
    price = lambda name: Decimal(str(profile[name]))
    total = ((number("inputTokens") + number("cacheWriteTokens")) * price("uncached_input")
             + number("cacheReadTokens") * price("cached_input")
             + number("outputTokens") * price("output")) / Decimal(1_000_000)
    return float(total)
