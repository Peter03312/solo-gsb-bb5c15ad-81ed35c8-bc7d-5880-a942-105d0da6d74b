"""主动完成演练（complete）端到端测试。

语义要点：
- 仅当所有缺号已补齐或逐号确认丢失时，完成操作才原子生成最终封存版本并转为已完成；
- 条件不满足返回 409 unresolved_gaps，且不生成任何封存版本；
- 完成后拒绝终端登记、非重复上报、更正、缺号确认与再次封存；
- 相同内容的上报重试仍返回 duplicate；冲突内容不得新增冲突证据；
- 历史读取仍可用；重复完成返回同一最终版本。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor


def _create_drill(client, terminals):
    r = client.post("/api/drills", json={"name": "完成测试演练", "terminals": terminals})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _report(client, drill, term, entries):
    return client.post(f"/api/drills/{drill}/terminals/{term}/reports",
                       json={"entries": entries})


def _entry(seq, content, ts="2026-10-05T09:00:00Z", etype="evt"):
    return {"seq": seq, "occurred_at": ts, "event_type": etype, "content": content}


def test_complete_blocked_by_open_gaps_creates_no_seal(client):
    drill = _create_drill(client, ["W1"])
    _report(client, drill, "W1", [_entry(1, "一"), _entry(3, "三")])  # 缺 2

    r = client.post(f"/api/drills/{drill}/complete", json={"completed_by": "负责人"})
    assert r.status_code == 409, r.text
    err = r.json()["error"]
    assert err["code"] == "unresolved_gaps"
    assert [(g["terminal_code"], g["seq"]) for g in err["details"]["open_gaps"]] == [("W1", 2)]

    # 不生成封存版本，演练仍为 open
    assert client.get(f"/api/drills/{drill}/seals").json()["seals"] == []
    status = client.get(f"/api/drills/{drill}").json()
    assert status["drill"]["status"] == "open"
    assert status["drill"]["final_seal_version"] is None


def test_complete_success_is_atomic_and_idempotent(client):
    drill = _create_drill(client, ["W2"])
    _report(client, drill, "W2", [_entry(1, "一"), _entry(3, "三")])
    # 缺号 2 逐号确认丢失后即满足完成条件
    r = client.post(f"/api/drills/{drill}/terminals/W2/gaps/lost",
                    json={"seq": 2, "reason": "缓存损坏", "confirmed_by": "保障组"})
    assert r.status_code == 200

    r = client.post(f"/api/drills/{drill}/complete",
                    json={"note": "演练收官", "completed_by": "张指挥"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert body["already_completed"] is False
    assert body["completed_by"] == "张指挥"
    assert body["completed_at"] is not None
    assert body["final_seal_version"] == 1
    seal = body["seal"]
    assert seal["version"] == 1 and seal["immutable"] is True
    assert seal["note"] == "演练收官"
    assert len(seal["records"]) == 2
    closed = {(g["terminal_code"], g["seq"]): g["closed_by"] for g in seal["closed_gaps"]}
    assert closed == {("W2", 2): "lost"}

    # 演练状态翻转，最终版本可查
    status = client.get(f"/api/drills/{drill}").json()
    assert status["drill"]["status"] == "completed"
    assert status["drill"]["final_seal_version"] == 1
    assert status["drill"]["completed_by"] == "张指挥"
    seals = client.get(f"/api/drills/{drill}/seals").json()["seals"]
    assert len(seals) == 1 and seals[0]["is_final"] is True

    # 重复完成：同一最终版本，不产生新版本（note/completed_by 被忽略）
    r = client.post(f"/api/drills/{drill}/complete",
                    json={"note": "再次完成", "completed_by": "别人"})
    assert r.status_code == 200, r.text
    again = r.json()
    assert again["already_completed"] is True
    assert again["final_seal_version"] == 1
    assert again["seal"]["seal_id"] == seal["seal_id"]
    assert again["seal"]["note"] == "演练收官"
    assert again["completed_by"] == "张指挥"
    assert len(client.get(f"/api/drills/{drill}/seals").json()["seals"]) == 1


def test_after_complete_all_mutations_rejected_but_duplicate_retry(client):
    drill = _create_drill(client, ["W3"])
    _report(client, drill, "W3", [_entry(1, "原始内容"), _entry(2, "二号")])
    # 先制造一份冲突证据，便于验证完成后不再新增
    r = _report(client, drill, "W3", [_entry(1, "冲突内容")])
    assert r.json()["counts"]["conflict"] == 1
    conflicts_before = client.get(f"/api/drills/{drill}/conflicts").json()["conflicts"]
    assert len(conflicts_before) == 1

    r = client.post(f"/api/drills/{drill}/complete", json={})
    assert r.status_code == 201

    # 终端登记被拒
    r = client.post(f"/api/drills/{drill}/terminals", json={"terminals": [{"code": "W9"}]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "drill_completed"

    # 混合批次：相同内容重试 => duplicate；新序号 => rejected；冲突内容 => rejected
    r = _report(client, drill, "W3", [
        _entry(1, "原始内容"),                 # 与既有记录完全一致
        _entry(3, "迟到的新序号"),              # 非重复上报
        _entry(2, "二号被篡改"),                # 冲突内容
    ])
    assert r.status_code == 200
    body = r.json()
    assert body["counts"] == {"accepted": 0, "duplicate": 1, "conflict": 0, "rejected": 2}
    statuses = [e["status"] for e in body["results"]]
    assert statuses == ["duplicate", "rejected", "rejected"]
    assert body["results"][1]["error"]["code"] == "drill_completed"
    assert body["results"][2]["error"]["code"] == "drill_completed"

    # 冲突内容不得新增冲突证据
    conflicts_after = client.get(f"/api/drills/{drill}/conflicts").json()["conflicts"]
    assert len(conflicts_after) == len(conflicts_before)

    # 更正被拒
    r = client.put(f"/api/drills/{drill}/records/W3/1",
                   json={"content": "改", "reason": "试图更正"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "drill_completed"

    # 缺号确认被拒（即使是已确认过的幂等重试也一律拒绝）
    r = client.post(f"/api/drills/{drill}/terminals/W3/gaps/lost", json={"seq": 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "drill_completed"

    # 再次封存被拒
    r = client.post(f"/api/drills/{drill}/seals", json={"note": "试图再封"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "drill_completed"

    # 历史读取全部可用，且数据未被改写
    tl = client.get(f"/api/drills/{drill}/timeline").json()
    assert tl["record_count"] == 2
    rec = client.get(f"/api/drills/{drill}/records/W3/1").json()
    assert rec["content"] == "原始内容"
    seal = client.get(f"/api/drills/{drill}/seals/1").json()
    assert seal["immutable"] is True and len(seal["records"]) == 2
    assert client.get(f"/api/drills/{drill}/gaps").status_code == 200


def test_complete_with_filled_and_lost_gaps_evidence(client):
    drill = _create_drill(client, ["W4"])
    _report(client, drill, "W4", [_entry(1, "一"), _entry(2, "二"), _entry(4, "四")])
    # 缺号 3 先确认丢失，后被迟到上报补齐 => filled
    client.post(f"/api/drills/{drill}/terminals/W4/gaps/lost", json={"seq": 3, "reason": "丢"})
    _report(client, drill, "W4", [_entry(3, "迟到补齐")])

    r = client.post(f"/api/drills/{drill}/complete", json={})
    assert r.status_code == 201, r.text
    closed = {(g["terminal_code"], g["seq"]): g["closed_by"]
              for g in r.json()["seal"]["closed_gaps"]}
    assert closed == {("W4", 3): "filled"}
    assert len(r.json()["seal"]["records"]) == 4


def test_complete_unknown_drill_404(client):
    r = client.post("/api/drills/drill_nope/complete", json={})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "drill_not_found"


def test_concurrent_complete_exactly_one_final_version(client):
    drill = _create_drill(client, ["W5"])
    _report(client, drill, "W5", [_entry(1, "一"), _entry(2, "二")])

    def complete(_):
        return client.post(f"/api/drills/{drill}/complete",
                           json={"completed_by": "并发负责人"})

    with ThreadPoolExecutor(max_workers=16) as pool:
        responses = list(pool.map(complete, range(20)))

    assert all(r.status_code in (200, 201) for r in responses)
    bodies = [r.json() for r in responses]
    # 恰好一个请求真正完成，其余幂等返回同一最终版本
    assert sum(1 for b in bodies if not b["already_completed"]) == 1
    assert {b["final_seal_version"] for b in bodies} == {1}
    assert len({b["seal"]["seal_id"] for b in bodies}) == 1

    seals = client.get(f"/api/drills/{drill}/seals").json()["seals"]
    assert len(seals) == 1 and seals[0]["is_final"] is True
    status = client.get(f"/api/drills/{drill}").json()
    assert status["drill"]["status"] == "completed"
