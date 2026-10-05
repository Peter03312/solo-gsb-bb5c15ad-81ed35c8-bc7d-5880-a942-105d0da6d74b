"""应用配置。

全部配置项均可通过环境变量覆盖，默认值面向容器内 SQLite 文件部署。
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # SQLite 数据库文件路径（容器内默认挂载卷下）
    db_path: str
    # uvicorn 监听地址 / 端口
    host: str
    port: int
    # SQLite busy_timeout（毫秒），并发写入时等待锁的最长时间
    busy_timeout_ms: int


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def get_settings() -> Settings:
    """每次读取环境变量，便于测试与初始化脚本复用。"""
    return Settings(
        db_path=os.environ.get("DB_PATH", "/data/drill.db"),
        host=os.environ.get("APP_HOST", "0.0.0.0"),
        port=_int_env("APP_PORT", 8080),
        busy_timeout_ms=_int_env("BUSY_TIMEOUT_MS", 30000),
    )
