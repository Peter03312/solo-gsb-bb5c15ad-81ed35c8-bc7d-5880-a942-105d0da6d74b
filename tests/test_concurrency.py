"""并发提交测试：同终端同序号的并发写必须被串行化。"""
from __future__ import annotations

import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

# 独立数据库，避免与其他测试共享连接状态
_TMP = tempfile.mkdtemp(prefix="drill-conc-")
os.environ["DB_PATH"] = os.path.join(_TMP, "conc.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def _post(client, url, payload):
    return client.post(url, json=payload).json()


def test_concurrent_identical_submissions_idempotent():
    with TestClient(app) as client:
        drill = client.post("/api/drills", json={"name": "并发幂等",
                                                 "terminals": ["P"]}).json()["id"]
        url = f"/api/drills/{drill}/terminals/P/reports"
        payload = {"entries": [{"seq": 1, "occurred_at": "2026-10-05T14:00:00Z",
                                "event_type": "evt", "content": "相同内容"}]}
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(lambda _: _post(client, url, payload), range(20)))

        accepted = sum(r["counts"]["accepted"] for r in results)
        duplicate = sum(r["counts"]["duplicate"] for r in results)
        assert accepted == 1
        assert duplicate == 19

        timeline = client.get(f"/api/drills/{drill}/timeline").json()
        assert timeline["record_count"] == 1


def test_concurrent_different_submissions_one_accepted_rest_conflict_evidence():
    with TestClient(app) as client:
        drill = client.post("/api/drills", json={"name": "并发冲突",
                                                 "terminals": ["Q"]}).json()["id"]
        url = f"/api/drills/{drill}/terminals/Q/reports"

        def payload(i):
            return {"entries": [{"seq": 2, "occurred_at": "2026-10-05T14:05:00Z",
                                 "event_type": "evt", "content": f"内容-{i}"}]}

        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(lambda i: _post(client, url, payload(i)), range(20)))

        accepted = sum(r["counts"]["accepted"] for r in results)
        conflict = sum(r["counts"]["conflict"] for r in results)
        assert accepted == 1
        assert conflict == 19

        confs = client.get(f"/api/drills/{drill}/conflicts").json()["conflicts"]
        assert len(confs) == 19
        assert all(c["seq"] == 2 for c in confs)
        # 19 份不同的迟到内容均作为证据留存
        assert len({c["incoming_fingerprint"] for c in confs}) == 19

        timeline = client.get(f"/api/drills/{drill}/timeline").json()
        assert timeline["record_count"] == 1
