#!/usr/bin/env python3
"""应急演练记录汇聚服务 —— 完整调用示例。

默认访问 http://127.0.0.1:8080，可用环境变量 BASE_URL 覆盖。
仅依赖标准库（urllib），无需安装第三方包。

用法：
    python3 examples/demo_api.py
    BASE_URL=http://127.0.0.1:8080 python3 examples/demo_api.py
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8080")


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE_URL + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def step(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def show(tag: str, result: tuple[int, dict]) -> dict:
    status, body = result
    print(f"[{tag}] HTTP {status}")
    print(json.dumps(body, ensure_ascii=False, indent=2))
    return body


def main() -> None:
    # 0. 健康检查
    step("0. 健康检查 GET /health")
    print(call("GET", "/health"))

    # 1. 建演练并随演练登记终端 T1，再批量补登 T2/T3
    step("1. 登记演练与终端")
    drill = show("建演练+T1", call("POST", "/api/drills",
                                  {"name": "2026-10 防汛应急演练",
                                   "note": "离线终端汇聚演示",
                                   "terminals": ["T1"]}))
    drill_id = drill["id"]

    show("补登终端", call("POST", f"/api/drills/{drill_id}/terminals",
                          {"terminals": [{"code": "T2", "name": "堤岸监测点"},
                                         {"code": "T3", "name": "指挥车"}]}))

    # 2. T1 批量上报序号 1..3；T2 只报 2（故意缺 1）
    step("2. 批量上报")
    show("T1 上报 1,2,3", call(
        "POST", f"/api/drills/{drill_id}/terminals/T1/reports",
        {"entries": [
            {"seq": 1, "occurred_at": "2026-10-05T09:00:00Z",
             "event_type": "login", "content": "终端上线自检通过"},
            {"seq": 2, "occurred_at": "2026-10-05T09:10:00Z",
             "event_type": "water_level", "content": "水位 21.4m，低于警戒"},
            {"seq": 3, "occurred_at": "2026-10-05T09:20:00Z",
             "event_type": "patrol", "content": "完成第一轮堤段巡查"},
        ]}))
    show("T2 上报 2（缺 1）", call(
        "POST", f"/api/drills/{drill_id}/terminals/T2/reports",
        {"entries": [
            {"seq": 2, "occurred_at": "2026-10-05T09:15:00Z",
             "event_type": "water_level", "content": "水位 21.6m"},
            # 条目级校验失败不影响其他条目
            {"seq": 0, "occurred_at": "2026-10-05T09:16:00Z",
             "event_type": "x", "content": "非法序号"},
        ]}))

    # 3. 幂等：完全相同内容重复提交
    step("3. 幂等重复提交（同终端同序号完整内容一致）")
    show("重复 T1#1", call(
        "POST", f"/api/drills/{drill_id}/terminals/T1/reports",
        {"entries": [
            {"seq": 1, "occurred_at": "2026-10-05T09:00:00Z",
             "event_type": "login", "content": "终端上线自检通过"},
        ]}))

    # 4. 冲突：同序号内容不同，留存证据
    step("4. 冲突提交（同序号不同内容，证据留存）")
    show("冲突 T1#2", call(
        "POST", f"/api/drills/{drill_id}/terminals/T1/reports",
        {"entries": [
            {"seq": 2, "occurred_at": "2026-10-05T09:10:00Z",
             "event_type": "water_level", "content": "水位 21.4m，已超警戒（被篡改）"},
        ]}))
    show("冲突证据列表", call("GET", f"/api/drills/{drill_id}/conflicts"))

    # 5. 缺号显式列出
    step("5. 当前时间线与缺号（稳定排序：发生时间→终端→序号）")
    show("时间线", call("GET", f"/api/drills/{drill_id}/timeline"))
    show("缺号", call("GET", f"/api/drills/{drill_id}/gaps"))

    # 6. 更正（附理由），原文与历次修订可查询
    step("6. 带理由更正 T1#3")
    show("更正", call("PUT", f"/api/drills/{drill_id}/records/T1/3",
                      {"content": "完成第一轮堤段巡查（东段补巡 200m）",
                       "reason": "录入时遗漏东段补巡里程",
                       "revised_by": "值班长"}))
    show("记录+全部修订", call("GET", f"/api/drills/{drill_id}/records/T1/3"))

    # 7. 封存前：缺号未闭合应被拒绝
    step("7. 未闭合缺号时尝试封存 => 明确错误")
    show("封存被拒", call("POST", f"/api/drills/{drill_id}/seals", {"note": "第一次尝试"}))

    # 8. 闭合缺号：T2#1 确认为丢失
    step("8. 逐号确认 T2 缺号 1 丢失")
    show("确认丢失", call("POST", f"/api/drills/{drill_id}/terminals/T2/gaps/lost",
                          {"seq": 1, "reason": "离线缓存损坏无法恢复",
                           "confirmed_by": "保障组"}))

    # 9. 封存 v1
    step("9. 封存版本 1（不可变快照）")
    show("封存", call("POST", f"/api/drills/{drill_id}/seals", {"note": "演练结束封存"}))

    # 10. 封存后的迟到上报与更正：当前时间线接纳，封存版不变
    step("10. 封存后迟到上报 T3#1（当前时间线接纳，封存版不变）")
    show("迟到上报", call(
        "POST", f"/api/drills/{drill_id}/terminals/T3/reports",
        {"entries": [
            {"seq": 1, "occurred_at": "2026-10-05T09:40:00Z",
             "event_type": "command", "content": "指挥车回撤指令回执（迟到补报）"},
        ]}))
    show("封存版本 1 仍为原样", call("GET", f"/api/drills/{drill_id}/seals/1"))
    show("当前时间线已含迟到数据", call("GET", f"/api/drills/{drill_id}/timeline"))

    # 11. 非法场景
    step("11. 错误场景")
    show("未登记终端上报", call(
        "POST", f"/api/drills/{drill_id}/terminals/GHOST/reports",
        {"entries": [{"seq": 1, "occurred_at": "2026-10-05T09:00:00Z",
                      "event_type": "x", "content": "y"}]}))
    show("无效序号", call(
        "POST", f"/api/drills/{drill_id}/terminals/T1/reports",
        {"entries": [{"seq": -3, "occurred_at": "2026-10-05T09:00:00Z",
                      "event_type": "x", "content": "y"}]}))


if __name__ == "__main__":
    main()
