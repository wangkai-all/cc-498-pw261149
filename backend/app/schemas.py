"""请求/响应 Schema。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .models import AlertLevel, ClassifierType, LineageType, TicketStatus


# ---------- 主题 ----------
class TopicIn(BaseModel):
    code: str = Field(pattern=r"^[a-z0-9_\-]+$")
    name: str
    description: str = ""


class TopicOut(BaseModel):
    id: int
    code: str
    name: str
    description: str
    active: bool
    created_at: datetime
    archived_at: datetime | None = None

    model_config = {"from_attributes": True}


class LineageIn(BaseModel):
    type: LineageType
    source_topic_codes: list[str]
    target_topic_codes: list[str]
    reason: str = ""


# ---------- 分类器版本 ----------
class VersionIn(BaseModel):
    version: str
    type: ClassifierType
    description: str = ""
    config: dict | list
    threshold: float = 0.35
    created_by: str = "admin"


class VersionOut(BaseModel):
    id: int
    version: str
    type: ClassifierType
    description: str
    config: dict | list
    threshold: float
    is_active: bool
    created_at: datetime
    activated_at: datetime | None
    created_by: str

    model_config = {"from_attributes": True}


class ActivateIn(BaseModel):
    version: str


# ---------- 工单 ----------
class TicketIn(BaseModel):
    ticket_no: str
    title: str
    content: str
    channel: str = "web"
    customer_id: str = ""
    event_time: datetime | None = None


class TicketOut(BaseModel):
    id: int
    ticket_no: str
    title: str
    content: str
    channel: str
    customer_id: str
    status: TicketStatus
    duplicate_of: int | None
    is_corrected: bool
    created_at: datetime
    updated_at: datetime
    event_time: datetime

    model_config = {"from_attributes": True}


class TicketDetail(TicketOut):
    classifications: list["ClassificationOut"] = []


# ---------- 分类结果 ----------
class ClassificationOut(BaseModel):
    id: int
    ticket_id: int
    version_id: int
    version_name: str = ""
    topic_id: int | None
    labels: list[str]
    scores: dict
    is_unknown: bool
    confidence: float
    evidence: list
    source: str
    job_id: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReclassifyIn(BaseModel):
    version: str | None = None  # 默认用当前激活版本
    topic_codes: list[str] | None = None
    start: datetime | None = None
    end: datetime | None = None
    include_unknown_only: bool = False
    created_by: str = "admin"


class JobOut(BaseModel):
    id: int
    version_id: int
    status: str
    total: int
    changed: int
    unknown_resolved: int
    diffs: list
    created_by: str
    created_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


# ---------- 人工纠正 ----------
class CorrectionIn(BaseModel):
    corrected_labels: list[str]
    reason: str = ""
    corrected_by: str = "analyst"


class CorrectionOut(BaseModel):
    id: int
    ticket_id: int
    version_id: int
    original_labels: list[str]
    corrected_labels: list[str]
    reason: str
    corrected_by: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------- 漂移 / 告警 ----------
class DriftResult(BaseModel):
    window_start: datetime
    window_end: datetime
    total: int
    distribution: dict
    drift_score: float | None
    drifted_topics: list


class AlertRuleIn(BaseModel):
    name: str
    metric: str  # drift_spike/unknown_rate/topic_volume/version_change
    topic_codes: list[str] = []
    threshold: float = 0.2
    level: AlertLevel = AlertLevel.warning


class AlertOut(BaseModel):
    id: int
    rule_id: int | None
    level: AlertLevel
    title: str
    detail: dict
    acknowledged: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UnknownSampleOut(BaseModel):
    id: int
    ticket_id: int
    version_id: int
    top_guesses: dict
    status: str
    resolved_topic_codes: list
    created_at: datetime
    resolved_at: datetime | None

    model_config = {"from_attributes": True}


class ResolveUnknownIn(BaseModel):
    topic_codes: list[str]
    ignore: bool = False


# ---------- 报表 / 导出 ----------
class ReportQuery(BaseModel):
    period_start: datetime
    period_end: datetime


class ReportOut(BaseModel):
    period_start: datetime
    period_end: datetime
    rows: list
    lineage_applied: list
    generated_at: datetime

    model_config = {"from_attributes": True}


class ExportIn(BaseModel):
    start: datetime | None = None
    end: datetime | None = None
    include_content: bool = False
    role: str = "analyst"  # admin/analyst/viewer
    actor: str = "analyst"


TicketDetail.model_rebuild()
