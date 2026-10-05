"""数据库初始化入口：python -m app.init_db

供 Dockerfile / docker-compose 的 init 服务一次性调用（幂等，可重复执行）。
应用启动时也会自动执行同样的初始化，因此该步骤不是必需，仅用于显式初始化。
"""
from __future__ import annotations

from .db import init_db


def main() -> None:
    path = init_db()
    print(f"[init-db] database initialized at {path}")


if __name__ == "__main__":
    main()
