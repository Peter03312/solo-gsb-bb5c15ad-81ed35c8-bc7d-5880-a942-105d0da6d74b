#!/usr/bin/env bash
# 应急演练记录汇聚服务 —— curl 调用示例
# 前置：服务已在 http://127.0.0.1:8080 启动（docker compose up 或本地 uvicorn）
set -euo pipefail

BASE="${BASE_URL:-http://127.0.0.1:8080}"
ctype='Content-Type: application/json'

echo "## 0. 健康检查"
curl -s "$BASE/health"; echo

echo "## 1. 建演练并随演登记终端 T1，再补登 T2/T3"
DID=$(curl -s -X POST "$BASE/api/drills" -H "$ctype" \
  -d '{"name":"2026-10 防汛应急演练","terminals":["T1"]}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
echo "drill_id=$DID"
curl -s -X POST "$BASE/api/drills/$DID/terminals" -H "$ctype" \
  -d '{"terminals":[{"code":"T2","name":"堤岸监测点"},{"code":"T3","name":"指挥车"}]}'; echo

echo "## 2. T1 批量上报（本地序号 1,2,3）"
curl -s -X POST "$BASE/api/drills/$DID/terminals/T1/reports" -H "$ctype" -d '{
  "entries": [
    {"seq": 1, "occurred_at": "2026-10-05T09:00:00Z", "event_type": "login", "content": "终端上线自检通过"},
    {"seq": 2, "occurred_at": "2026-10-05T09:10:00Z", "event_type": "water_level", "content": "水位 21.4m，低于警戒"},
    {"seq": 3, "occurred_at": "2026-10-05T09:20:00Z", "event_type": "patrol", "content": "完成第一轮堤段巡查"}
  ]
}'; echo

echo "## 3. 幂等：同终端同序号、完整内容完全一致 => duplicate（不产生新数据）"
curl -s -X POST "$BASE/api/drills/$DID/terminals/T1/reports" -H "$ctype" -d '{
  "entries": [{"seq": 1, "occurred_at": "2026-10-05T09:00:00Z", "event_type": "login", "content": "终端上线自检通过"}]
}'; echo

echo "## 4. 冲突：同序号但内容不同 => conflict，冲突证据落库可查"
curl -s -X POST "$BASE/api/drills/$DID/terminals/T1/reports" -H "$ctype" -d '{
  "entries": [{"seq": 2, "occurred_at": "2026-10-05T09:10:00Z", "event_type": "water_level", "content": "水位 21.4m，已超警戒（被篡改）"}]
}'; echo
curl -s "$BASE/api/drills/$DID/conflicts"; echo

echo "## 5. 当前时间线（发生时间->终端标识->序号 稳定排序）与显式缺号"
curl -s "$BASE/api/drills/$DID/timeline"; echo
curl -s "$BASE/api/drills/$DID/gaps"; echo

echo "## 6. 带理由更正 T1#3，原文与历次修订均可查询"
curl -s -X PUT "$BASE/api/drills/$DID/records/T1/3" -H "$ctype" -d '{
  "content": "完成第一轮堤段巡查（东段补巡 200m）",
  "reason": "录入时遗漏东段补巡里程",
  "revised_by": "值班长"
}'; echo
curl -s "$BASE/api/drills/$DID/records/T1/3"; echo

echo "## 7. T2 只报了序号 2 => 缺号 1 未闭合时封存被拒绝（409）"
curl -s -X POST "$BASE/api/drills/$DID/terminals/T2/reports" -H "$ctype" -d '{
  "entries": [{"seq": 2, "occurred_at": "2026-10-05T09:15:00Z", "event_type": "water_level", "content": "水位 21.6m"}]
}'; echo
curl -s -X POST "$BASE/api/drills/$DID/seals" -H "$ctype" -d '{"note":"尝试封存"}'; echo

echo "## 8. 逐号确认 T2 的缺号 1 丢失"
curl -s -X POST "$BASE/api/drills/$DID/terminals/T2/gaps/lost" -H "$ctype" \
  -d '{"seq":1,"reason":"离线缓存损坏无法恢复","confirmed_by":"保障组"}'; echo

echo "## 9. 缺号全部闭合 => 封存版本 1（不可变）"
curl -s -X POST "$BASE/api/drills/$DID/seals" -H "$ctype" -d '{"note":"演练结束封存"}'; echo

echo "## 10. 封存后迟到上报：当前时间线接纳，封存版本不变"
curl -s -X POST "$BASE/api/drills/$DID/terminals/T3/reports" -H "$ctype" -d '{
  "entries": [{"seq": 1, "occurred_at": "2026-10-05T09:40:00Z", "event_type": "command", "content": "指挥车回撤指令回执（迟到补报）"}]
}'; echo
curl -s "$BASE/api/drills/$DID/seals/1"; echo
curl -s "$BASE/api/drills/$DID/timeline"; echo

echo "## 11. 错误场景"
curl -s -X POST "$BASE/api/drills/$DID/terminals/GHOST/reports" -H "$ctype" \
  -d '{"entries":[{"seq":1,"occurred_at":"2026-10-05T09:00:00Z","event_type":"x","content":"y"}]}'; echo
curl -s -X POST "$BASE/api/drills/$DID/terminals/T1/reports" -H "$ctype" \
  -d '{"entries":[{"seq":-1,"occurred_at":"2026-10-05T09:00:00Z","event_type":"x","content":"y"}]}'; echo

echo "## 12. 负责人主动完成演练：原子生成最终封存版本并转为已完成（缺号未闭合时会 409 且不产生封存）"
curl -s -X POST "$BASE/api/drills/$DID/complete" -H "$ctype" \
  -d '{"note":"演练收官","completed_by":"演练负责人"}'; echo

echo "## 13. 完成后的写操作一律 409 drill_completed：终端登记 / 非重复上报 / 更正 / 缺号确认 / 再次封存"
curl -s -X POST "$BASE/api/drills/$DID/terminals" -H "$ctype" \
  -d '{"terminals":[{"code":"T9"}]}'; echo
curl -s -X POST "$BASE/api/drills/$DID/terminals/T1/reports" -H "$ctype" \
  -d '{"entries":[{"seq":4,"occurred_at":"2026-10-05T09:50:00Z","event_type":"x","content":"完成后新序号"}]}'; echo
curl -s -X PUT "$BASE/api/drills/$DID/records/T1/1" -H "$ctype" \
  -d '{"content":"试图更正","reason":"完成后更正"}'; echo
curl -s -X POST "$BASE/api/drills/$DID/terminals/T2/gaps/lost" -H "$ctype" -d '{"seq":1}'; echo
curl -s -X POST "$BASE/api/drills/$DID/seals" -H "$ctype" -d '{"note":"再次封存"}'; echo

echo "## 14. 完成后：相同内容重试仍 duplicate；冲突内容被拒绝且不新增冲突证据"
curl -s -X POST "$BASE/api/drills/$DID/terminals/T1/reports" -H "$ctype" \
  -d '{"entries":[{"seq":1,"occurred_at":"2026-10-05T09:00:00Z","event_type":"login","content":"终端上线自检通过"}]}'; echo
curl -s -X POST "$BASE/api/drills/$DID/terminals/T1/reports" -H "$ctype" \
  -d '{"entries":[{"seq":1,"occurred_at":"2026-10-05T09:00:00Z","event_type":"login","content":"完成后的冲突内容"}]}'; echo
curl -s "$BASE/api/drills/$DID/conflicts"; echo

echo "## 15. 重复完成返回同一最终版本；历史读取仍可用"
curl -s -X POST "$BASE/api/drills/$DID/complete" -H "$ctype" -d '{"note":"重复完成"}'; echo
curl -s "$BASE/api/drills/$DID/seals"; echo
curl -s "$BASE/api/drills/$DID/timeline"; echo

echo "DONE drill_id=$DID"
