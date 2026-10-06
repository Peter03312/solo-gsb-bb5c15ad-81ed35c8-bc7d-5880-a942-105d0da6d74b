"""完成演练（主动完成 + 最终封存）的端到端测试。"""
from __future__ import annotations


def _create_drill(client, terminals):
    r = client.post("/api/drills", json={"name": "完成演练测试", "terminals": terminals})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _report(client, drill, terminal, seq, content="c", occurred_at="2026-10-05T09:00:00Z"):
    return client.post(
        f"/api/drills/{drill}/terminals/{terminal}/reports",
        json={"entries": [{"seq": seq, "occurred_at": occurred_at,
                           "event_type": "evt", "content": content}]},
    )


def test_complete_blocked_by_open_gaps_and_creates_no_seal(client):
    drill = _create_drill(client, ["E"])
    _report(client, drill, "E", 1)
    _report(client, drill, "E", 3)  # 缺号 2 未闭合

    r = client.post(f"/api/drills/{drill}/complete", json={"completed_by": "负责人"})
    assert r.status_code == 409, r.text
    err = r.json()["error"]
    assert err["code"] == "unresolved_gaps"
    assert [g["seq"] for g in err["details"]["open_gaps"]] == [2]

    # 条件不满足：不生成任何封存版本，演练仍为进行中
    assert client.get(f"/api/drills/{drill}/seals").json()["seals"] == []
    status = client.get(f"/api/drills/{drill}").json()
    assert status["drill"]["status"] == "active"
    assert status["drill"]["final_seal_version"] is None

    # 演练不存在
    r = client.post("/api/drills/drill_nope/complete", json={})
    assert r.status_code == 404 and r.json()["error"]["code"] == "drill_not_found"


def test_complete_then_all_writes_rejected_but_reads_and_idempotent_retry(client):
    drill = _create_drill(client, ["F", "G"])
    _report(client, drill, "F", 1, "F1")
    _report(client, drill, "F", 2, "F2")
    _report(client, drill, "G", 2, "G2")  # G 缺 1
    # 逐号确认丢失闭合 G 的缺号 1
    r = client.post(f"/api/drills/{drill}/terminals/G/gaps/lost",
                    json={"seq": 1, "reason": "设备损坏", "confirmed_by": "保障组"})
    assert r.status_code == 200

    # 完成：原子生成最终封存版本并转为已完成
    r = client.post(f"/api/drills/{drill}/complete",
                    json={"note": "演练收官", "completed_by": "演练负责人"})
    assert r.status_code == 200, r.text
    final = r.json()
    assert final["final"] is True
    assert final["drill_status"] == "completed"
    assert final["already_completed"] is False
    assert final["immutable"] is True
    assert final["version"] == 1
    assert len(final["records"]) == 3
    assert {(g["terminal_code"], g["seq"]): g["closed_by"]
            for g in final["closed_gaps"]} == {("G", 1): "lost"}

    status = client.get(f"/api/drills/{drill}").json()
    assert status["drill"]["status"] == "completed"
    assert status["drill"]["completed_by"] == "演练负责人"
    assert status["drill"]["completed_at"] is not None
    assert status["drill"]["final_seal_version"] == 1

    # ---- 完成后：终端登记被拒绝
    r = client.post(f"/api/drills/{drill}/terminals", json={"terminals": [{"code": "NEW"}]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "drill_completed"

    # ---- 完成后：非重复上报（新序号）条目级拒绝
    r = _report(client, drill, "F", 3, "F3-late")
    body = r.json()
    assert body["counts"]["rejected"] == 1
    assert body["results"][0]["status"] == "rejected"
    assert body["results"][0]["error"]["code"] == "drill_completed"

    # ---- 完成后：相同内容重试仍是 duplicate
    r = _report(client, drill, "F", 1, "F1")
    assert r.json()["results"][0]["status"] == "duplicate"

    # ---- 完成后：冲突内容被拒绝且不得新增冲突证据
    assert client.get(f"/api/drills/{drill}/conflicts").json()["conflicts"] == []
    r = _report(client, drill, "F", 1, "被篡改的内容")
    body = r.json()
    assert body["results"][0]["status"] == "rejected"
    assert body["results"][0]["error"]["code"] == "drill_completed"
    assert client.get(f"/api/drills/{drill}/conflicts").json()["conflicts"] == []

    # ---- 完成后：更正被拒绝
    r = client.put(f"/api/drills/{drill}/records/F/1",
                   json={"content": "x", "reason": "试图更正"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "drill_completed"

    # ---- 完成后：缺号确认被拒绝
    r = client.post(f"/api/drills/{drill}/terminals/G/gaps/lost", json={"seq": 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "drill_completed"

    # ---- 完成后：再次封存被拒绝
    r = client.post(f"/api/drills/{drill}/seals", json={"note": "再次封存"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "drill_completed"

    # ---- 重复完成：返回同一最终版本
    r = client.post(f"/api/drills/{drill}/complete", json={"note": "重复完成"})
    assert r.status_code == 200, r.text
    again = r.json()
    assert again["already_completed"] is True
    assert again["seal_id"] == final["seal_id"]
    assert again["version"] == final["version"]
    # 不会生成新封存版本
    seals = client.get(f"/api/drills/{drill}/seals").json()["seals"]
    assert [s["version"] for s in seals] == [1]

    # ---- 历史读取仍可用
    assert client.get(f"/api/drills/{drill}/timeline").status_code == 200
    assert client.get(f"/api/drills/{drill}/gaps").status_code == 200
    assert client.get(f"/api/drills/{drill}/records/F/1").status_code == 200
    assert client.get(f"/api/drills/{drill}/seals/1").status_code == 200
    assert client.get(f"/api/drills/{drill}/terminals").status_code == 200


def test_complete_with_late_filled_gap_records_filled_evidence(client):
    drill = _create_drill(client, ["H"])
    _report(client, drill, "H", 1, "H1")
    _report(client, drill, "H", 3, "H3")
    # 先确认缺号 2 丢失，随后迟到上报补齐 => closed_by = filled
    client.post(f"/api/drills/{drill}/terminals/H/gaps/lost", json={"seq": 2})
    r = _report(client, drill, "H", 2, "H2-迟到补齐")
    assert r.json()["counts"]["accepted"] == 1

    r = client.post(f"/api/drills/{drill}/complete", json={})
    assert r.status_code == 200, r.text
    closed = {(g["terminal_code"], g["seq"]): g["closed_by"]
              for g in r.json()["closed_gaps"]}
    assert closed == {("H", 2): "filled"}
