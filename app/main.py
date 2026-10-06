"""FastAPI 入口：HTTP API 路由。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from . import service
from .db import connect, init_db
from .errors import APIError
from .models import (
    Correction,
    DrillComplete,
    DrillCreate,
    MarkLost,
    ReportBatch,
    SealCreate,
    TerminalsRegister,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 容器内幂等建库；docker-compose 的 init 服务也会执行同样的初始化
    init_db()
    yield


app = FastAPI(
    title="应急演练记录汇聚服务",
    version="1.0.0",
    description="离线终端批量上报、幂等/冲突、缺号管理、带理由更正与封存的记录汇聚 API",
    lifespan=lifespan,
)


def db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_response())


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------- 演练

@app.post("/api/drills", status_code=201)
def create_drill(payload: DrillCreate, conn=Depends(db)) -> dict:
    return service.create_drill(conn, payload.name, payload.note, payload.terminals)


@app.get("/api/drills")
def list_drills(conn=Depends(db)) -> list[dict]:
    return service.list_drills(conn)


@app.get("/api/drills/{drill_id}")
def drill_status(drill_id: str, conn=Depends(db)) -> dict:
    return service.get_drill_status(conn, drill_id)


# ---------------------------------------------------------------- 终端

@app.post("/api/drills/{drill_id}/terminals", status_code=201)
def register_terminals(drill_id: str, payload: TerminalsRegister, conn=Depends(db)) -> dict:
    if not payload.terminals:
        raise APIError("terminals 不能为空", code="empty_batch")
    items = [{"code": t.code, "name": t.name} for t in payload.terminals]
    return service.register_terminals(conn, drill_id, items)


@app.get("/api/drills/{drill_id}/terminals")
def list_terminals(drill_id: str, conn=Depends(db)) -> list[dict]:
    return service.list_terminals(conn, drill_id)


# ---------------------------------------------------------------- 上报

@app.post("/api/drills/{drill_id}/terminals/{terminal_code}/reports")
def submit_reports(drill_id: str, terminal_code: str, payload: ReportBatch,
                   conn=Depends(db)) -> dict:
    if not payload.entries:
        raise APIError("entries 不能为空", code="empty_batch")
    entries = [e.model_dump() for e in payload.entries]
    return service.submit_reports(conn, drill_id, terminal_code, entries)


# ---------------------------------------------------------------- 时间线 / 缺号 / 冲突

@app.get("/api/drills/{drill_id}/timeline")
def timeline(drill_id: str, conn=Depends(db)) -> dict:
    return service.get_timeline(conn, drill_id)


@app.get("/api/drills/{drill_id}/gaps")
def gaps(drill_id: str, conn=Depends(db)) -> dict:
    return service.list_gaps(conn, drill_id)


@app.post("/api/drills/{drill_id}/terminals/{terminal_code}/gaps/lost")
def mark_lost(drill_id: str, terminal_code: str, payload: MarkLost,
              conn=Depends(db)) -> dict:
    return service.mark_lost(
        conn, drill_id, terminal_code, payload.seq, payload.reason, payload.confirmed_by
    )


@app.get("/api/drills/{drill_id}/conflicts")
def conflicts(drill_id: str, conn=Depends(db)) -> dict:
    return service.list_conflicts(conn, drill_id)


# ---------------------------------------------------------------- 记录 / 更正

@app.get("/api/drills/{drill_id}/records/{terminal_code}/{seq}")
def get_record(drill_id: str, terminal_code: str, seq: int, conn=Depends(db)) -> dict:
    return service.get_record(conn, drill_id, terminal_code, seq)


@app.put("/api/drills/{drill_id}/records/{terminal_code}/{seq}")
def correct_record(drill_id: str, terminal_code: str, seq: int, payload: Correction,
                   conn=Depends(db)) -> dict:
    data = payload.model_dump()
    data["seq"] = seq
    return service.correct_record(conn, drill_id, terminal_code, seq, data)


# ---------------------------------------------------------------- 封存

@app.post("/api/drills/{drill_id}/seals", status_code=201)
def seal_drill(drill_id: str, payload: SealCreate, conn=Depends(db)) -> dict:
    return service.seal_drill(conn, drill_id, payload.note)


@app.get("/api/drills/{drill_id}/seals")
def list_seals(drill_id: str, conn=Depends(db)) -> dict:
    return service.list_seals(conn, drill_id)


@app.get("/api/drills/{drill_id}/seals/{version}")
def get_seal(drill_id: str, version: int, conn=Depends(db)) -> dict:
    return service.get_seal(conn, drill_id, version)


# ---------------------------------------------------------------- 完成演练

@app.post("/api/drills/{drill_id}/complete")
def complete_drill(drill_id: str, response: Response,
                   payload: DrillComplete | None = None, conn=Depends(db)) -> dict:
    """主动完成演练：缺号全部闭合时原子生成最终封存版本并转为已完成。

    首次完成返回 201；重复完成幂等返回 200 与同一最终版本。
    """
    payload = payload or DrillComplete()
    result = service.complete_drill(conn, drill_id, payload.note, payload.completed_by)
    if not result["already_completed"]:
        response.status_code = 201
    return result
