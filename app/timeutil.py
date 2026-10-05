"""时间规范化与内容指纹工具。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .errors import APIError


def now_iso() -> str:
    """UTC 时间，固定 6 位微秒，保证字符串排序与发生时间排序一致。"""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def normalize_occurred_at(raw: object) -> str:
    """把上报的发生时间规范化为 UTC ISO-8601。

    - 接受 2026-10-05T09:00:00Z、带偏移量、无时区（按 UTC 处理）等形式；
    - 拒绝无法解析的值，保证幂等指纹稳定。
    """
    if not isinstance(raw, str):
        raise APIError("occurred_at 必须是 ISO-8601 字符串", code="invalid_occurred_at")
    text = raw.strip()
    if not text:
        raise APIError("occurred_at 不能为空", code="invalid_occurred_at")
    try:
        # Python 3.11 可解析 'Z'；早期版本需手动替换，这里统一处理。
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise APIError(f"occurred_at 不是合法的 ISO-8601 时间: {text!r}", code="invalid_occurred_at")
    if dt.tzinfo is None:
        # 离线终端可能给出本地时间且不带时区，统一按 UTC 存储
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.isoformat(timespec="microseconds")


def compute_fingerprint(occurred_at: str, event_type: str, content: str) -> str:
    """完整上报内容指纹：发生时间 + 类型 + 内容任一不同即判冲突。"""
    payload = json.dumps(
        {"occurred_at": occurred_at, "event_type": event_type, "content": content},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
