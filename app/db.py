"""SQLite 连接、建表初始化与事务辅助。"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .config import get_settings

SCHEMA_STATEMENTS = [
    # 演练
    """
    CREATE TABLE IF NOT EXISTS drills (
        id          TEXT PRIMARY KEY,
        name        TEXT,
        note        TEXT,
        created_at  TEXT NOT NULL
    )
    """,
    # 终端先登记，terminal_code 在同一演练内唯一
    """
    CREATE TABLE IF NOT EXISTS terminals (
        id          TEXT PRIMARY KEY,
        drill_id    TEXT NOT NULL REFERENCES drills(id),
        code        TEXT NOT NULL,
        name        TEXT,
        created_at  TEXT NOT NULL,
        UNIQUE (drill_id, code)
    )
    """,
    # 上报记录：同一终端同一序号至多一条，内容经修订演进
    """
    CREATE TABLE IF NOT EXISTS records (
        id           TEXT PRIMARY KEY,
        drill_id     TEXT NOT NULL REFERENCES drills(id),
        terminal_id  TEXT NOT NULL REFERENCES terminals(id),
        seq          INTEGER NOT NULL,
        occurred_at  TEXT NOT NULL,
        event_type   TEXT NOT NULL,
        content      TEXT NOT NULL,
        fingerprint  TEXT NOT NULL,
        rev          INTEGER NOT NULL DEFAULT 0,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL,
        UNIQUE (terminal_id, seq)
    )
    """,
    # 修订历史：rev=0 为首次上报原文，rev>=1 为带理由的更正
    """
    CREATE TABLE IF NOT EXISTS record_revisions (
        id           TEXT PRIMARY KEY,
        record_id    TEXT NOT NULL REFERENCES records(id),
        drill_id     TEXT NOT NULL REFERENCES drills(id),
        rev          INTEGER NOT NULL,
        occurred_at  TEXT NOT NULL,
        event_type   TEXT NOT NULL,
        content      TEXT NOT NULL,
        fingerprint  TEXT NOT NULL,
        reason       TEXT,
        revised_by   TEXT,
        created_at   TEXT NOT NULL,
        UNIQUE (record_id, rev)
    )
    """,
    # 冲突证据：同终端同序号但内容不一致的迟到提交全部留存
    """
    CREATE TABLE IF NOT EXISTS conflicts (
        id              TEXT PRIMARY KEY,
        drill_id        TEXT NOT NULL REFERENCES drills(id),
        terminal_id     TEXT NOT NULL REFERENCES terminals(id),
        record_id       TEXT REFERENCES records(id),
        seq             INTEGER NOT NULL,
        incoming        TEXT NOT NULL,
        existing        TEXT NOT NULL,
        incoming_fingerprint TEXT NOT NULL,
        existing_fingerprint TEXT NOT NULL,
        created_at      TEXT NOT NULL
    )
    """,
    # 逐号确认丢失的缺号
    """
    CREATE TABLE IF NOT EXISTS lost_gaps (
        id           TEXT PRIMARY KEY,
        drill_id     TEXT NOT NULL REFERENCES drills(id),
        terminal_id  TEXT NOT NULL REFERENCES terminals(id),
        seq          INTEGER NOT NULL,
        reason       TEXT,
        confirmed_by TEXT,
        created_at   TEXT NOT NULL,
        resolved     INTEGER NOT NULL DEFAULT 0,
        resolved_at  TEXT,
        UNIQUE (terminal_id, seq)
    )
    """,
    # 封存版本（append-only；表本身只追加不改写）
    """
    CREATE TABLE IF NOT EXISTS seals (
        id           TEXT PRIMARY KEY,
        drill_id     TEXT NOT NULL REFERENCES drills(id),
        version      INTEGER NOT NULL,
        note         TEXT,
        created_at   TEXT NOT NULL,
        UNIQUE (drill_id, version)
    )
    """,
    # 封存时点的时间线快照
    """
    CREATE TABLE IF NOT EXISTS seal_records (
        seal_id      TEXT NOT NULL REFERENCES seals(id),
        drill_id     TEXT NOT NULL REFERENCES drills(id),
        record_id    TEXT NOT NULL,
        terminal_id  TEXT NOT NULL,
        terminal_code TEXT NOT NULL,
        seq          INTEGER NOT NULL,
        occurred_at  TEXT NOT NULL,
        event_type   TEXT NOT NULL,
        content      TEXT NOT NULL,
        rev          INTEGER NOT NULL,
        PRIMARY KEY (seal_id, record_id)
    )
    """,
    # 封存时点的修订原文快照
    """
    CREATE TABLE IF NOT EXISTS seal_revisions (
        seal_id      TEXT NOT NULL REFERENCES seals(id),
        record_id    TEXT NOT NULL,
        rev          INTEGER NOT NULL,
        occurred_at  TEXT NOT NULL,
        event_type   TEXT NOT NULL,
        content      TEXT NOT NULL,
        reason       TEXT,
        revised_by   TEXT,
        created_at   TEXT NOT NULL,
        PRIMARY KEY (seal_id, record_id, rev)
    )
    """,
    # 封存时点的缺号闭合情况（含已补齐 / 确认丢失两种闭合方式）
    """
    CREATE TABLE IF NOT EXISTS seal_gaps (
        seal_id       TEXT NOT NULL REFERENCES seals(id),
        terminal_id   TEXT NOT NULL,
        terminal_code TEXT NOT NULL,
        seq           INTEGER NOT NULL,
        closed_by     TEXT NOT NULL,
        PRIMARY KEY (seal_id, terminal_id, seq)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_records_drill ON records(drill_id)",
    "CREATE INDEX IF NOT EXISTS idx_revisions_record ON record_revisions(record_id)",
    "CREATE INDEX IF NOT EXISTS idx_conflicts_drill ON conflicts(drill_id)",
    "CREATE INDEX IF NOT EXISTS idx_lost_drill ON lost_gaps(drill_id, resolved)",
]


def connect(db_path: str | None = None, *, busy_timeout_ms: int | None = None) -> sqlite3.Connection:
    """打开一个连接：ROW 工厂、外键与 busy_timeout 均已配置。"""
    settings = get_settings()
    path = db_path or settings.db_path
    timeout = settings.busy_timeout_ms if busy_timeout_ms is None else busy_timeout_ms
    conn = sqlite3.connect(path, timeout=timeout / 1000.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(timeout)}")
    return conn


def init_db(db_path: str | None = None) -> str:
    """幂等建库建表；返回实际数据库路径。"""
    settings = get_settings()
    path = db_path or settings.db_path
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        for stmt in SCHEMA_STATEMENTS:
            conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()
    return path


@contextmanager
def transaction(conn: sqlite3.Connection, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
    """事务上下文。

    immediate=True 使用 BEGIN IMMEDIATE 立即取写锁，将并发写串行化，
    配合 busy_timeout 可保证同序号并发提交的幂等/冲突判定互斥。
    """
    if immediate:
        conn.execute("BEGIN IMMEDIATE")
    else:
        conn.execute("BEGIN")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
