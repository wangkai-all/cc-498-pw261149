"""客服工单主题分析系统的领域模型。

核心实体关系：
- Ticket（工单）1—N Classification（不同版本下的分类结果）
- Topic（主题）N—N TopicLineage（合并/拆分血缘）
- ClassifierVersion（词表/规则/模型三种版本）
- ReclassificationJob（重分类任务）保留 original 与 new 双结果
- AlertRule / DriftSnapshot / UnknownSample / ReportSnapshot / ManualCorrection / AuditLog
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ClassifierType(str, enum.Enum):
    keyword = "keyword"  # 词表匹配
    rule = "rule"        # 规则表达式
    model = "model"      # 统计模型（朴素贝叶斯）


class TicketStatus(str, enum.Enum):
    open = "open"
    updated = "updated"        # 迟到更新后重新打开
    closed = "closed"


class LineageType(str, enum.Enum):
    merge = "merge"
    split = "split"


class AlertLevel(str, enum.Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    classifications: Mapped[list["Classification"]] = relationship(back_populates="topic")


class ClassifierVersion(Base):
    """词表 / 规则 / 模型的一个不可变版本，同一时间最多一个激活版本。"""

    __tablename__ = "classifier_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    type: Mapped[ClassifierType] = mapped_column(Enum(ClassifierType), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    # keyword: {topic_code: {"terms": [...], "weight": x}}
    # rule:    [{"topic_code": ..., "all":[...], "any":[...], "not":[...]}]
    # model:   序列化的朴素贝叶斯参数
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    threshold: Mapped[float] = mapped_column(Float, default=0.35)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="admin")

    classifications: Mapped[list["Classification"]] = relationship(back_populates="classifier_version")


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(32), default="web")
    customer_id: Mapped[str] = mapped_column(String(64), default="")
    # 去重指纹：标题+正文的规范化哈希，重复工单只保留首单
    dedup_hash: Mapped[str] = mapped_column(String(64), index=True)
    duplicate_of: Mapped[int | None] = mapped_column(ForeignKey("tickets.id"), nullable=True)
    status: Mapped[TicketStatus] = mapped_column(Enum(TicketStatus), default=TicketStatus.open)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # 事件实际发生时间（可能晚于入库时间，用于迟到数据判定）
    event_time: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_corrected: Mapped[bool] = mapped_column(Boolean, default=False)

    classifications: Mapped[list["Classification"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )


class Classification(Base):
    """一条工单在某个分类器版本下的分类结果。

    重分类不覆盖旧行：新版本（甚至同版本复算）产生新行并通过 job_id 关联任务，
    original/new 通过 ReclassificationJob.diffs 保留；在线迟到更新则替换同版本旧行。
    """

    __tablename__ = "classifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("classifier_versions.id"), index=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"), nullable=True, index=True)
    # 多标签：["billing","login"]；未知主题时为 ["unknown"]
    labels: Mapped[list] = mapped_column(JSON, default=list)
    scores: Mapped[dict] = mapped_column(JSON, default=dict)
    is_unknown: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    # 样本证据：命中的词、规则或模型特征
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(32), default="online")  # online/reclass/manual
    job_id: Mapped[int | None] = mapped_column(ForeignKey("reclassification_jobs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    ticket: Mapped[Ticket] = relationship(back_populates="classifications")
    classifier_version: Mapped[ClassifierVersion] = relationship(back_populates="classifications")
    topic: Mapped[Topic | None] = relationship(back_populates="classifications")


class ReclassificationJob(Base):
    """历史工单重分类任务：逐条保留 original_labels 与 new_labels。"""

    __tablename__ = "reclassification_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("classifier_versions.id"))
    filter_spec: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending/running/done/failed
    total: Mapped[int] = mapped_column(Integer, default=0)
    changed: Mapped[int] = mapped_column(Integer, default=0)
    unknown_resolved: Mapped[int] = mapped_column(Integer, default=0)
    # [{"ticket_id","ticket_no","original_labels","new_labels","changed"}]
    diffs: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(64), default="admin")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TopicLineage(Base):
    """主题合并/拆分血缘。merge: N 个 source -> 1 个 target；split 反之。"""

    __tablename__ = "topic_lineages"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[LineageType] = mapped_column(Enum(LineageType))
    source_topic_ids: Mapped[list] = mapped_column(JSON, default=list)
    target_topic_ids: Mapped[list] = mapped_column(JSON, default=list)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64), default="admin")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ManualCorrection(Base):
    """人工纠正：记录机器结果与人工标签，可回灌为模型训练样本。"""

    __tablename__ = "manual_corrections"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("classifier_versions.id"))
    original_labels: Mapped[list] = mapped_column(JSON, default=list)
    corrected_labels: Mapped[list] = mapped_column(JSON, default=list)
    reason: Mapped[str] = mapped_column(Text, default="")
    corrected_by: Mapped[str] = mapped_column(String(64), default="analyst")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class DriftSnapshot(Base):
    """每个时间窗口各主题占比的快照，用于主题漂移检测。"""

    __tablename__ = "drift_snapshots"
    __table_args__ = (
        UniqueConstraint("window_start", "window_end", "version_id", name="uq_drift_window"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime, index=True)
    window_end: Mapped[datetime] = mapped_column(DateTime)
    version_id: Mapped[int | None] = mapped_column(ForeignKey("classifier_versions.id"), nullable=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    distribution: Mapped[dict] = mapped_column(JSON, default=dict)  # {topic_code: ratio}
    # 与上一窗口相比的漂移指标
    drift_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    drifted_topics: Mapped[list] = mapped_column(JSON, default=list)


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    # drift_spike / unknown_rate / topic_volume / version_change
    metric: Mapped[str] = mapped_column(String(32), index=True)
    topic_ids: Mapped[list] = mapped_column(JSON, default=list)  # 空表示全部主题
    threshold: Mapped[float] = mapped_column(Float, default=0.2)
    level: Mapped[AlertLevel] = mapped_column(Enum(AlertLevel), default=AlertLevel.warning)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("alert_rules.id"), nullable=True)
    level: Mapped[AlertLevel] = mapped_column(Enum(AlertLevel), default=AlertLevel.warning)
    title: Mapped[str] = mapped_column(String(256))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class UnknownSample(Base):
    """未知主题样本队列，供分析师审视并沉淀为新主题/新词表。"""

    __tablename__ = "unknown_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("classifier_versions.id"))
    top_guesses: Mapped[dict] = mapped_column(JSON, default=dict)  # 最接近的候选及分数
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending/resolved/ignored
    resolved_topic_codes: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ReportSnapshot(Base):
    """报表快照：主题合并/拆分后按血缘重建，告警依赖按新主题映射。"""

    __tablename__ = "report_snapshots"
    __table_args__ = (
        UniqueConstraint("period_start", "period_end", name="uq_report_period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    period_start: Mapped[datetime] = mapped_column(DateTime, index=True)
    period_end: Mapped[datetime] = mapped_column(DateTime)
    rows: Mapped[list] = mapped_column(JSON, default=list)  # [{topic_code, count, ratio}]
    lineage_applied: Mapped[list] = mapped_column(JSON, default=list)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    """导出等敏感操作的审计日志。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64), index=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
