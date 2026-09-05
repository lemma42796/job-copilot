"""上游 429 熔断(P7)。

`llm/client.py` 的 tenacity 会对 `LLMUpstreamError` 重试 3 次。上游过载时
这等于把压力放大三倍,所以需要一个进程级熔断器兜底:连续 N 次 429 后
在冷却窗口内直接拒绝新调用,不再发请求;冷却结束自动半开放行一次,
成功即复位。

计数只在 429 上累加,其他失败(超时 / 5xx / schema)不触发熔断 ——
那些不是"上游让我们慢下来"的信号。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from jobcopilot_api.errors import JobCopilotError
from jobcopilot_api.settings import settings

log = structlog.get_logger(__name__)


class UpstreamCircuitOpenError(JobCopilotError):
    status_code = 503
    code = "upstream_circuit_open"
    title = "上游持续限流,已熔断"

    def __init__(self, detail: str = "", **kwargs: Any) -> None:
        super().__init__(detail, **kwargs)
        self.headers["Retry-After"] = str(
            max(1, int(settings.upstream_breaker_cooldown_s))
        )


@dataclass
class _BreakerState:
    consecutive_429: int = 0
    opened_until: float = 0.0
    half_open: bool = False
    name: str = "upstream"
    _last_log: float = field(default=0.0, repr=False)


_states: dict[str, _BreakerState] = {}


def _state(name: str) -> _BreakerState:
    state = _states.get(name)
    if state is None:
        state = _BreakerState(name=name)
        _states[name] = state
    return state


def check(name: str = "upstream") -> None:
    """调用前闸门。熔断期内抛 503,不发请求。"""
    state = _state(name)
    if state.opened_until <= 0:
        return
    now = time.monotonic()
    if now < state.opened_until:
        raise UpstreamCircuitOpenError(
            f"上游连续 {state.consecutive_429} 次 429,"
            f"熔断中,剩余 {state.opened_until - now:.0f}s"
        )
    # 冷却结束 → 半开,放行一次探测。
    state.opened_until = 0.0
    state.half_open = True


def record_success(name: str = "upstream") -> None:
    state = _state(name)
    if state.consecutive_429 or state.half_open:
        log.info("upstream_breaker_closed", breaker=name)
    state.consecutive_429 = 0
    state.half_open = False
    state.opened_until = 0.0


def record_rate_limited(name: str = "upstream") -> None:
    state = _state(name)
    state.consecutive_429 += 1
    if state.half_open or state.consecutive_429 >= settings.upstream_breaker_threshold:
        state.opened_until = time.monotonic() + settings.upstream_breaker_cooldown_s
        state.half_open = False
        log.warning(
            "upstream_breaker_opened",
            breaker=name,
            consecutive_429=state.consecutive_429,
            cooldown_s=settings.upstream_breaker_cooldown_s,
        )


def reset(name: str | None = None) -> None:
    """测试 / 运维 helper。"""
    if name is None:
        _states.clear()
    else:
        _states.pop(name, None)


def snapshot() -> dict[str, dict[str, float | int | bool]]:
    now = time.monotonic()
    return {
        name: {
            "consecutive_429": state.consecutive_429,
            "open": state.opened_until > now,
            "cooldown_remaining_s": max(state.opened_until - now, 0.0),
        }
        for name, state in _states.items()
    }
