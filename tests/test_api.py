"""端到端业务流程测试。"""
from __future__ import annotations


def _create_drill(client, terminals=None):
    payload = {"name": "2026 防汛演练"}
    if terminals is not None:
        payload["terminals"] = terminals
    r = client.post("/api/drills", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _register(client, drill_id, codes):
    return client.post(
        f"/api/drills/{drill_id}/terminals",
        json={"terminals": [{"code": c} for c in codes]},
    )


def test_drill_and_terminal_lifecycle(client):
    drill = _create_drill(client)

    # 批量登记终端
    r = client.post(
        f"/api/drills/{drill}/terminals",
        json={"terminals": [{"code": "T1"}, {"code": "T2", "name": "指挥车"}]},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert {t["code"] for t in body["registered"]} == {"T1", "T2"}
    assert body["duplicates"] == []

    # 重复登记：已存在/同批重复在返回体中标记 duplicates，不重复建终端
    r = client.post(
        f"/api/drills/{drill}/terminals",
        json={"terminals": [{"code": "T1"}, {"code": "T3"}, {"code": "T3"}]},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert [t["code"] for t in body["registered"]] == ["T3"]
    dup_codes = {d["code"] for d in body["duplicates"]}
    assert "T1" in dup_codes  # 已登记
    assert "T3" in dup_codes  # 同批内重复

    # 演练不存在
    r = client.post("/api/drills/drill_nope/terminals", json={"terminals": [{"code": "X"}]})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "drill_not_found"


def test_report_batch_statuses_and_idempotency(client):
    drill = _create_drill(client)
    _register(client, drill, ["A"])

    url = f"/api/drills/{drill}/terminals/A/reports"
    r = client.post(url, json={"entries": [
        {"seq": 1, "occurred_at": "2026-10-05T09:00:00Z", "event_type": "login", "content": "终端上线"},
        {"seq": 3, "occurred_at": "2026-10-05T09:05:00Z", "event_type": "alert", "content": "水位告警"},
        {"seq": 0, "occurred_at": "2026-10-05T09:06:00Z", "event_type": "x", "content": "非法序号"},
        {"seq": 2, "occurred_at": "bad-time", "event_type": "x", "content": "非法时间"},
        {"seq": 4, "occurred_at": "2026-10-05T09:07:00Z", "event_type": "  ", "content": "非法类型"},
    ]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["counts"] == {"accepted": 2, "duplicate": 0, "conflict": 0, "rejected": 3}
    statuses = {e["index"]: (e["status"], e.get("error", {}).get("code")) for e in body["results"]}
    assert statuses[0][0] == "accepted"
    assert statuses[1][0] == "accepted"
    assert statuses[2] == ("rejected", "invalid_seq")
    assert statuses[3] == ("rejected", "invalid_occurred_at")
    assert statuses[4] == ("rejected", "invalid_event_type")

    # 完整内容重复提交 => 幂等 duplicate，不产生新数据
    r = client.post(url, json={"entries": [
        {"seq": 1, "occurred_at": "2026-10-05T09:00:00Z", "event_type": "login", "content": "终端上线"},
        # 等价时间写法（Z == +00:00）归一化后仍判重
        {"seq": 3, "occurred_at": "2026-10-05T09:05:00+00:00", "event_type": "alert", "content": "水位告警"},
    ]})
    body = r.json()
    assert {e["status"] for e in body["results"]} == {"duplicate"}
    assert body["counts"]["duplicate"] == 2

    # 同序号不同内容 => conflict 并留存证据
    r = client.post(url, json={"entries": [
        {"seq": 1, "occurred_at": "2026-10-05T09:00:00Z", "event_type": "login", "content": "被篡改的内容"},
    ]})
    body = r.json()
    assert body["counts"]["conflict"] == 1
    conflict_id = body["results"][0]["conflict_id"]

    # 冲突证据可查
    r = client.get(f"/api/drills/{drill}/conflicts")
    assert r.status_code == 200
    conflicts = r.json()["conflicts"]
    assert any(c["id"] == conflict_id and c["seq"] == 1 for c in conflicts)
    c0 = next(c for c in conflicts if c["id"] == conflict_id)
    assert "被篡改的内容" in c0["incoming"]
    assert "终端上线" in c0["existing"]

    # 非法终端上报
    r = client.post(f"/api/drills/{drill}/terminals/GHOST/reports",
                    json={"entries": [{"seq": 1, "occurred_at": "2026-10-05T09:00:00Z",
                                       "event_type": "x", "content": "y"}]})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "terminal_not_registered"

    # 空批次
    r = client.post(url, json={"entries": []})
    assert r.status_code == 400 and r.json()["error"]["code"] == "empty_batch"


def test_timeline_gaps_sorting_and_lost_gap(client):
    drill = _create_drill(client)
    _register(client, drill, ["B1", "B2"])

    def post(term, seq, t, etype="evt", content="c"):
        return client.post(
            f"/api/drills/{drill}/terminals/{term}/reports",
            json={"entries": [{"seq": seq, "occurred_at": t, "event_type": etype,
                               "content": content}]},
        )

    # B1 上报 1、3（缺 2）；B2 上报 2（缺 1）
    assert post("B1", 1, "2026-10-05T10:00:00Z").json()["counts"]["accepted"] == 1
    assert post("B1", 3, "2026-10-05T10:05:00Z").json()["counts"]["accepted"] == 1
    assert post("B2", 2, "2026-10-05T10:05:00Z").json()["counts"]["accepted"] == 1

    # 缺号显式列出
    r = client.get(f"/api/drills/{drill}/gaps")
    gaps = {(g["terminal_code"], g["seq"]): g["state"] for g in r.json()["gaps"]}
    assert gaps == {("B1", 2): "missing", ("B2", 1): "missing"}

    # 时间线稳定排序：发生时间 → 终端标识 → 序号
    r = client.get(f"/api/drills/{drill}/timeline")
    tl = [(x["occurred_at"], x["terminal_code"], x["seq"]) for x in r.json()["records"]]
    assert tl == [
        ("2026-10-05T10:00:00.000000+00:00", "B1", 1),
        ("2026-10-05T10:05:00.000000+00:00", "B1", 3),
        ("2026-10-05T10:05:00.000000+00:00", "B2", 2),
    ]

    # 确认超出最大序号的缺号 => 明确错误
    r = client.post(f"/api/drills/{drill}/terminals/B2/gaps/lost", json={"seq": 5})
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_a_gap"

    # 逐号确认丢失（幂等）
    r = client.post(f"/api/drills/{drill}/terminals/B1/gaps/lost",
                    json={"seq": 2, "reason": "离线存储损坏", "confirmed_by": "op"})
    assert r.status_code == 200 and r.json()["state"] == "lost_confirmed"
    r2 = client.post(f"/api/drills/{drill}/terminals/B1/gaps/lost", json={"seq": 2})
    assert r2.status_code == 200 and r2.json()["already_confirmed"] is True

    # B2 的缺号仍未闭合
    r = client.get(f"/api/drills/{drill}")
    assert r.json()["open_gap_count"] == 1

    # 迟到上报补齐 B2 的 1 号
    r = post("B2", 1, "2026-10-05T10:04:00Z")
    assert r.json()["counts"]["accepted"] == 1
    r = client.get(f"/api/drills/{drill}")
    assert r.json()["open_gap_count"] == 0


def test_correction_history_and_seal_immutability(client):
    drill = _create_drill(client)
    _register(client, drill, ["C"])

    post = client.post(
        f"/api/drills/{drill}/terminals/C/reports",
        json={"entries": [
            {"seq": 1, "occurred_at": "2026-10-05T11:00:00Z", "event_type": "login", "content": "原文"},
            {"seq": 2, "occurred_at": "2026-10-05T11:05:00Z", "event_type": "alert", "content": "二号"},
        ]},
    )
    assert post.status_code == 200

    # 缺号 1..1 for... seq 最大 2，缺号 1？不：有 1、2，无缺号。直接可封存。
    r = client.post(f"/api/drills/{drill}/seals", json={"note": "首封"})
    assert r.status_code == 201, r.text
    v1 = r.json()
    assert v1["version"] == 1 and v1["immutable"] is True
    assert len(v1["records"]) == 2
    assert v1["records"][0]["revisions"][0]["is_original"] is True

    # 封存后更正：当前时间线接纳，但封存版本不变
    r = client.put(f"/api/drills/{drill}/records/C/1",
                   json={"content": "更正后的内容", "reason": "笔误更正", "revised_by": "审核员"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rev"] == 1
    assert [x["rev"] for x in body["revisions"]] == [0, 1]
    assert body["revisions"][0]["content"] == "原文"
    assert body["revisions"][1]["reason"] == "笔误更正"

    # 更正必须附理由
    r = client.put(f"/api/drills/{drill}/records/C/2", json={"content": "x", "reason": "   "})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_reason"

    # 无变化的更正
    r = client.put(f"/api/drills/{drill}/records/C/2",
                   json={"event_type": "alert", "content": "二号", "reason": "重复更正"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "no_change"

    # 封存版本 1 仍是原文
    r = client.get(f"/api/drills/{drill}/seals/1")
    sealed = r.json()
    c1 = next(x for x in sealed["records"] if x["terminal_code"] == "C" and x["seq"] == 1)
    assert c1["content"] == "原文"
    assert c1["rev_at_seal"] == 0
    assert all(rv["content"] != "更正后的内容" for rv in c1["revisions"])

    # 迟到上报在封存后仍被当前时间线接纳（新序号 => 产生新缺号闭合状态）
    r = client.post(
        f"/api/drills/{drill}/terminals/C/reports",
        json={"entries": [
            {"seq": 3, "occurred_at": "2026-10-05T11:10:00Z", "event_type": "sync", "content": "迟到补报"}
        ]},
    )
    assert r.json()["counts"]["accepted"] == 1

    # 对封存版本重复提交完全相同内容（未更正过的 2 号）=> duplicate，封存版本依旧不变
    r = client.post(
        f"/api/drills/{drill}/terminals/C/reports",
        json={"entries": [
            {"seq": 2, "occurred_at": "2026-10-05T11:05:00Z", "event_type": "alert", "content": "二号"}
        ]},
    )
    assert r.json()["results"][0]["status"] == "duplicate"

    # 当前时间线体现更正与新数据
    r = client.get(f"/api/drills/{drill}/timeline")
    tl = r.json()["records"]
    assert next(x for x in tl if x["seq"] == 1)["content"] == "更正后的内容"
    assert any(x["seq"] == 3 for x in tl)

    # 再次封存生成新版本（append-only），新版本含更正与迟到数据
    r = client.post(f"/api/drills/{drill}/seals", json={"note": "第二封"})
    assert r.status_code == 201
    v2 = r.json()
    assert v2["version"] == 2 and len(v2["records"]) == 3
    c1v2 = next(x for x in v2["records"] if x["seq"] == 1)
    assert c1v2["content"] == "更正后的内容" and c1v2["rev_at_seal"] == 1
    assert len(c1v2["revisions"]) == 2  # 原文与历次修订均留存于新版本

    # 版本 1 仍可查询且保持原样
    r = client.get(f"/api/drills/{drill}/seals/1")
    assert len(r.json()["records"]) == 2
    r = client.get(f"/api/drills/{drill}/seals/9")
    assert r.status_code == 404 and r.json()["error"]["code"] == "seal_version_not_found"


def test_seal_blocked_with_open_gaps(client):
    drill = _create_drill(client)
    _register(client, drill, ["D"])
    client.post(
        f"/api/drills/{drill}/terminals/D/reports",
        json={"entries": [
            {"seq": 1, "occurred_at": "2026-10-05T12:00:00Z", "event_type": "a", "content": "x"},
            {"seq": 3, "occurred_at": "2026-10-05T12:01:00Z", "event_type": "a", "content": "y"},
        ]},
    )
    r = client.post(f"/api/drills/{drill}/seals", json={})
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "unresolved_gaps"
    assert err["details"]["open_gaps"][0]["seq"] == 2

    # 确认丢失后可封存
    r = client.post(f"/api/drills/{drill}/terminals/D/gaps/lost", json={"seq": 2, "reason": "丢失"})
    assert r.status_code == 200
    r = client.post(f"/api/drills/{drill}/seals", json={})
    assert r.status_code == 201
    closed = {(g["terminal_code"], g["seq"]): g["closed_by"] for g in r.json()["closed_gaps"]}
    assert closed == {("D", 2): "lost"}


def test_cross_drill_correction_rejected(client):
    d1 = _create_drill(client)
    d2 = _create_drill(client)
    client.post(f"/api/drills/{d1}/terminals", json={"terminals": [{"code": "X1"}]})
    client.post(
        f"/api/drills/{d1}/terminals/X1/reports",
        json={"entries": [{"seq": 1, "occurred_at": "2026-10-05T13:00:00Z",
                           "event_type": "t", "content": "old"}]},
    )
    # 在 d2 下更正 d1 终端的记录：明确的跨演练终端错误
    r = client.put(f"/api/drills/{d2}/records/X1/1",
                   json={"content": "new", "reason": "跨演练更正"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "cross_drill_terminal"

    # d2 登记了同 code 的另一台终端后，仍不能更正 d1 的记录（资源属于 d1）
    client.post(f"/api/drills/{d2}/terminals", json={"terminals": [{"code": "X1"}]})
    r = client.put(f"/api/drills/{d2}/records/X1/1",
                   json={"content": "new", "reason": "跨演练更正"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "record_not_found"

    # d1 原数据未受影响
    r = client.get(f"/api/drills/{d1}/records/X1/1")
    assert r.json()["content"] == "old"
