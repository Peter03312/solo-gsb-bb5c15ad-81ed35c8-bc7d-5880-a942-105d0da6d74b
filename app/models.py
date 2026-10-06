"""Pydantic 请求模型。

上报/确认丢失等批量接口允许在“条目级”返回校验结果，
因此条目内部字段使用宽松类型，由业务层逐条校验并回写状态，
而外层结构（ drill_id 缺失、entries 非列表等）仍由 Pydantic 拦截。
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class DrillCreate(BaseModel):
    name: Optional[str] = None
    note: Optional[str] = None
    # 可在建演练时一次性登记终端
    terminals: list[str] = Field(default_factory=list)


class TerminalRegister(BaseModel):
    code: str
    name: Optional[str] = None


class TerminalsRegister(BaseModel):
    terminals: list[TerminalRegister]


class ReportEntry(BaseModel):
    # 宽松类型：非法类型要在条目级给出拒绝结果而不是整批 422
    seq: Any = None
    occurred_at: Any = None
    event_type: Any = None
    content: Any = None


class ReportBatch(BaseModel):
    entries: list[ReportEntry]


class Correction(BaseModel):
    occurred_at: Any = None
    event_type: Any = None
    content: Any = None
    reason: str
    revised_by: Optional[str] = None


class MarkLost(BaseModel):
    seq: Any = None
    reason: Optional[str] = None
    confirmed_by: Optional[str] = None


class SealCreate(BaseModel):
    note: Optional[str] = None


class DrillComplete(BaseModel):
    # 完成时生成的最终封存版本附注；completed_by 记录演练负责人
    note: Optional[str] = None
    completed_by: Optional[str] = None
