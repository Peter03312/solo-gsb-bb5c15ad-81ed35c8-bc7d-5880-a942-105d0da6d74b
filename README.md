# 应急演练记录汇聚服务

面向**应急演练离线终端**的记录汇聚（aggregation）服务：演练开始前先登记终端；
终端以**从 1 递增的本地序号**批量上报事件；服务端对**同终端同序号**的重复提交做
**幂等去重**或**冲突存证**，跟踪**缺号**，支持**附理由的更正与原文追溯**，
并在所有缺号闭合后生成**不可变封存版本**。演练负责人可**主动完成**演练：
缺号全部闭合时原子生成**最终封存版本**并将演练转为已完成，此后拒绝一切变更。

- 技术栈：Python 3.11 · FastAPI · SQLite（标准库 `sqlite3`，WAL + 写事务串行化）
- 无外部服务依赖，Docker 一条命令启动，数据持久化在命名卷中

---

## 1. 一键启动（Docker Compose，推荐）

```bash
docker compose up --build -d
```

启动内容：

1. `init` 一次性服务：创建数据卷并**幂等建库建表**（`python -m app.init_db`）；
2. `api` 服务：等待 init 成功完成后启动，监听 **`0.0.0.0:8080`**，带 `/health` 健康检查；
3. SQLite 数据库位于命名卷 `drill-data` 内的 `/data/drill.db`。

常用命令：

```bash
docker compose ps            # 查看状态 / 健康检查
docker compose logs -f api   # 查看日志
docker compose down          # 停止（保留数据卷）
docker compose down -v       # 停止并删除数据库卷
```

## 2. 本地直接运行

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 仅初始化数据库（可选；应用启动时也会自动幂等初始化）
python -m app.init_db

# 启动 API
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

启动后：

- 服务地址：`http://127.0.0.1:8080`
- 健康检查：`GET http://127.0.0.1:8080/health`
- 交互式 API 文档（Swagger UI）：`http://127.0.0.1:8080/docs`
- OpenAPI 描述：`http://127.0.0.1:8080/openapi.json`

## 3. 配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DB_PATH` | `/data/drill.db` | SQLite 数据库文件路径，目录自动创建 |
| `APP_HOST` | `0.0.0.0` | uvicorn 监听地址 |
| `APP_PORT` | `8080` | uvicorn 监听端口 |
| `BUSY_TIMEOUT_MS` | `30000` | SQLite `busy_timeout`，并发写等待写锁的最长毫秒数 |

compose 中将宿主机 `8080` 映射到容器 `8080`，如需换端口改
`docker-compose.yml` 的 `ports`（例如 `"18080:8080"`）。

## 4. 核心语义

- **先登记后上报**：终端必须在演练下登记；未登记终端上报/更正返回
  `404 terminal_not_registered`（同标识终端属于别的演练时返回 `404 cross_drill_terminal`）。
- **序号**：必须是 `>= 1` 的整数。条目级校验失败只拒绝该条，不影响同批其他条目。
- **批量上报逐条返回状态**：
  - `accepted`：首次接收；
  - `duplicate`：同终端同序号且**完整内容**（发生时间+类型+内容，规范化后指纹一致）完全相同——幂等，不产生任何新数据；
  - `conflict`：同序号但内容不同——**不覆盖原记录**，把迟到内容、原内容、双方指纹写入冲突证据表；
  - `rejected`：序号/时间/类型/内容非法，随条目返回错误码。
- **时间线**：按 `发生时间 → 终端标识 → 序号` 稳定排序；缺号在 `gaps` 中**显式列出**，
  状态为 `missing`（待闭合）或 `lost_confirmed`（已逐号确认丢失）。
- **迟到补齐**：若缺号先被“确认丢失”后又收到该序号上报，则缺号标记为“已补齐”（filled）。
- **更正**：必须附**非空理由**；每次更正生成新版本（`rev` 递增），
  原文（`rev=0`）与历次修订长期可查；无实质变化的更正返回 `400 no_change`。
- **封存**：
  - 条件——所有终端的缺号要么已补齐、要么已逐号确认丢失，否则 `409 unresolved_gaps` 并返回未闭合清单；
  - 封存生成**只追加（append-only）的不可变快照版本**：封存时刻的时间线、每条记录的全部修订原文、缺号闭合证据；
  - 封存**不锁死当前时间线**：封存之后迟到上报、更正仍被当前时间线接纳，可再次封存为新版本；
  - 任何迟到上报/更正/并发提交都**不会改写已封存版本**（封存数据写入独立的快照表，代码路径上不提供更新/删除）。
- **完成（主动收官）**：
  - 演练负责人调用 `POST /api/drills/{id}/complete` 主动完成演练；
  - 仅当所有缺号已闭合（补齐或逐号确认丢失）时，**同一事务内原子**生成最终不可变封存版本并把演练置为
    `completed`；条件不满足返回 `409 unresolved_gaps`（附未闭合缺号清单），**不生成任何封存版本**；
  - 完成后拒绝一切变更：终端登记、非重复上报、更正、缺号确认、再次封存均返回 `409 drill_completed`；
  - 完成后的上报重试：**相同内容仍返回 `duplicate`**（幂等不变）；**冲突内容直接拒绝且不新增冲突证据**；
  - 时间线、缺号、冲突、记录、封存版本等**历史读取不受影响**；
  - **重复完成幂等**：返回同一最终版本（`already_completed: true`），不产生新版本。

## 5. HTTP API 一览

所有业务错误统一为：

```json
{"error": {"code": "错误码", "message": "中文说明", "details": {}}}
```

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| POST | `/api/drills` | 建演练（可在 `terminals` 字段一次性登记终端标识） |
| GET | `/api/drills` | 演练列表 |
| GET | `/api/drills/{drill_id}` | 演练状态：完成状态、终端数、记录数、未闭合缺号、封存版本 |
| POST | `/api/drills/{drill_id}/terminals` | 批量登记终端 |
| GET | `/api/drills/{drill_id}/terminals` | 终端列表 |
| POST | `/api/drills/{drill_id}/terminals/{terminal_code}/reports` | **批量上报**，逐条返回状态 |
| GET | `/api/drills/{drill_id}/timeline` | 当前时间线 + 显式缺号 |
| GET | `/api/drills/{drill_id}/gaps` | 缺号清单（未闭合 / 已确认丢失） |
| POST | `/api/drills/{drill_id}/terminals/{terminal_code}/gaps/lost` | 逐号确认缺号丢失（幂等） |
| GET | `/api/drills/{drill_id}/conflicts` | 冲突证据列表 |
| GET | `/api/drills/{drill_id}/records/{terminal_code}/{seq}` | 单条记录 + 全部修订 |
| PUT | `/api/drills/{drill_id}/records/{terminal_code}/{seq}` | **附理由更正** |
| POST | `/api/drills/{drill_id}/seals` | 封存（缺号未闭合返回 409） |
| GET | `/api/drills/{drill_id}/seals` | 封存版本列表（含 `is_final` 标记） |
| GET | `/api/drills/{drill_id}/seals/{version}` | 取指定不可变封存版本 |
| POST | `/api/drills/{drill_id}/complete` | **主动完成演练**：原子生成最终封存版本并转为已完成（幂等） |

### 关键请求/响应示例

**批量上报** `POST /api/drills/{id}/terminals/T1/reports`

```json
{
  "entries": [
    {"seq": 1, "occurred_at": "2026-10-05T09:00:00Z", "event_type": "login", "content": "终端上线"},
    {"seq": 2, "occurred_at": "2026-10-05T09:10:00Z", "event_type": "water_level", "content": "水位 21.4m"}
  ]
}
```

```json
{
  "drill_id": "drill_...",
  "terminal_code": "T1",
  "counts": {"accepted": 2, "duplicate": 0, "conflict": 0, "rejected": 0},
  "results": [
    {"index": 0, "seq": 1, "status": "accepted", "record_id": "rec_..."},
    {"index": 1, "seq": 2, "status": "accepted", "record_id": "rec_..."}
  ]
}
```

同序号完全相同内容再次提交 → `"status": "duplicate"`；
内容不同 → `"status": "conflict", "conflict_id": "cfl_..."`，证据在
`GET /api/drills/{id}/conflicts` 中可查（含 incoming/existing 原文与 SHA-256 指纹）。

**更正** `PUT /api/drills/{id}/records/T1/2`（`reason` 必填）

```json
{"occurred_at": null, "event_type": null, "content": "水位 21.4m（已复核）",
 "reason": "现场二次校核后修正", "revised_by": "值班长"}
```

响应包含 `rev`（当前版本号）与 `revisions` 数组：`rev=0` 为原文，`rev>=1` 为历次修订。

**确认丢失** `POST .../gaps/lost`：`{"seq": 1, "reason": "缓存损坏", "confirmed_by": "保障组"}`
（重复确认幂等；确认一个不存在的缺号或已上报序号返回 `409`）。

**封存** `POST /api/drills/{id}/seals`：成功返回版本快照（`version`、`records` 含各自全部
`revisions`、`closed_gaps` 含 `filled`/`lost` 闭合证据，`immutable: true`）。

**完成** `POST /api/drills/{id}/complete`

```json
{"note": "演练正式收官", "completed_by": "张指挥"}
```

首次完成返回 `201`：缺号校验、最终封存版本生成、状态翻转在**同一事务**内原子生效——

```json
{
  "drill_id": "drill_...",
  "status": "completed",
  "already_completed": false,
  "completed_at": "2026-10-06T01:14:27.190000+00:00",
  "completed_by": "张指挥",
  "final_seal_version": 2,
  "seal": {"version": 2, "immutable": true, "records": [], "closed_gaps": []}
}
```

缺号未闭合时返回 `409 unresolved_gaps`（`details.open_gaps` 为未闭合清单）且不生成封存版本；
重复完成返回 `200` 与**同一最终版本**（`already_completed: true`，忽略本次 `note`/`completed_by`）。
完成后所有变更类操作返回 `409 drill_completed`；上报批次内相同内容条目仍为 `duplicate`，
新序号/冲突内容条目为 `rejected`（`error.code = drill_completed`），且不新增冲突证据。

### 明确错误码

| 场景 | 状态码 | error.code |
| --- | --- | --- |
| 演练不存在 | 404 | `drill_not_found` |
| 终端未登记 | 404 | `terminal_not_registered` |
| 同标识终端属于另一演练 | 404 | `cross_drill_terminal` |
| 序号不是 ≥1 的整数（条目级） | 200 体 `rejected` | `invalid_seq` |
| 发生时间无法解析（条目级） | 200 体 `rejected` | `invalid_occurred_at` |
| 类型/内容为空（条目级） | 200 体 `rejected` | `invalid_event_type` / `invalid_content` |
| 同序号内容冲突 | 200 体 `conflict` | 冲突证据落库 `conflicts` |
| 跨演练更正（记录不在该演练） | 404 | `record_not_found`（终端层面先报 `cross_drill_terminal`） |
| 更正无理由 / 无变化 | 400 | `invalid_reason` / `no_change` |
| 缺号未闭合就封存/完成 | 409 | `unresolved_gaps`（details 带缺号清单） |
| 对非缺号确认丢失 / 缺号超范围 | 409 | `gap_already_filled` / `not_a_gap` |
| 封存版本不存在 | 404 | `seal_version_not_found` |
| 演练已完成后的变更类操作 | 409 | `drill_completed`（上报批次内为条目级 `rejected`） |
| 空批次 | 400 | `empty_batch` |

## 6. 调用示例

- 纯标准库 Python，全流程（登记→上报→幂等→冲突→缺号→更正→封存被拒→闭合→封存→封存后迟到
  →完成被拒→闭合→完成→完成后拒绝语义→重复完成幂等）：

  ```bash
  pip install httpx   # 仅测试需要；demo_api.py 只用标准库
  python3 examples/demo_api.py
  BASE_URL=http://127.0.0.1:8080 python3 examples/demo_api.py
  ```

- curl 版（同样覆盖全流程）：

  ```bash
  bash examples/curl_examples.sh
  ```

## 7. 测试

```bash
pip install -r requirements.txt
pip install pytest
python -m pytest -q
```

测试覆盖：登记与重复登记、批量四种状态、幂等、冲突存证、稳定排序与显式缺号、
确认丢失与迟到补齐、更正与修订历史、未闭合缺号拒绝封存、封存快照不可变、
封存后当前时间线继续接纳变更、跨演练更正被拒；主动完成——缺号未闭合拒绝且不生成版本、
原子生成最终版本并转为已完成、完成后拒绝登记/非重复上报/更正/缺号确认/再封存、
相同内容重试仍 `duplicate`、冲突内容不新增证据、历史读取可用、重复完成返回同一版本；
以及 20 线程并发下**同序号相同内容恰有 1 条 accepted/19 条 duplicate**、
**不同内容恰有 1 条 accepted/19 份冲突证据**、**并发完成恰好生成 1 个最终版本**。

## 8. 并发与数据完整性设计

- 所有写操作在单条连接的 `BEGIN IMMEDIATE` 事务内完成“先查后写”，SQLite 写锁将并发提交串行化；
  `busy_timeout` 保证高并发下排队而不是立即失败。完成操作的“缺号校验 + 最终版本快照 +
  状态翻转”在同一事务内原子生效，并发完成恰好生成一个最终版本。
- 唯一约束兜底：`terminals(drill_id, code)`、`records(terminal_id, seq)`、
  `lost_gaps(terminal_id, seq)`、`seals(drill_id, version)`。
- 封存数据写入 `seals / seal_records / seal_revisions / seal_gaps` 独立快照表，仅追加；
  服务不提供任何更新/删除快照的代码路径，迟到上报与更正只作用于当前时间线表。
- 演练完成后（`drills.status = completed`）所有变更入口在事务内先行拒绝；
  最终封存版本号固化在 `drills.final_seal_version`，重复完成直接读取并返回同一版本。
- 时间统一规范化为 UTC ISO-8601（微秒精度），既保证幂等指纹稳定，也保证字符串排序即时间排序。

## 9. 目录结构

```
app/
  config.py       # 环境变量配置
  db.py           # 连接、建表 DDL、事务
  errors.py       # 统一业务错误
  timeutil.py     # 时间规范化、SHA-256 内容指纹
  models.py       # 请求模型
  service.py      # 全部业务逻辑（不依赖 Web 层）
  main.py         # FastAPI 路由
  init_db.py      # 数据库初始化入口
tests/            # pytest 端到端 + 并发测试
examples/         # curl 与 Python 调用示例
Dockerfile
docker-compose.yml
requirements.txt
```
