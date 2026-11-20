"""人工纠正与未知样本处理。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..classifiers import UNKNOWN_LABEL
from ..models import (
    Classification,
    ManualCorrection,
    Ticket,
    Topic,
    UnknownSample,
    utcnow,
)
from ..schemas import CorrectionIn, ResolveUnknownIn
from .tickets import _latest_classification  # noqa: PLC2701


def add_correction(db: Session, ticket_id: int, payload: CorrectionIn) -> ManualCorrection:
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise KeyError("工单不存在")
    latest = _latest_classification(db, ticket_id)
    if latest is None:
        raise ValueError("该工单尚无分类结果，无法纠正")

    valid_codes = {t.code for t in db.scalars(select(Topic).where(Topic.active.is_(True))).all()}
    bad = [c for c in payload.corrected_labels if c not in valid_codes]
    if bad:
        raise ValueError(f"无效主题标签: {bad}")

    correction = ManualCorrection(
        ticket_id=ticket_id,
        version_id=latest.version_id,
        original_labels=list(latest.labels),
        corrected_labels=payload.corrected_labels,
        reason=payload.reason,
        corrected_by=payload.corrected_by,
    )
    db.add(correction)

    # 人工结果作为新的分类行写入（source=manual），机器结果保留
    topic = db.scalar(select(Topic).where(Topic.code == payload.corrected_labels[0]))
    manual_clf = Classification(
        ticket_id=ticket_id,
        version_id=latest.version_id,
        topic_id=topic.id if topic else None,
        labels=payload.corrected_labels,
        scores={c: 1.0 for c in payload.corrected_labels},
        is_unknown=False,
        confidence=1.0,
        evidence=[{"type": "manual", "reason": payload.reason, "by": payload.corrected_by}],
        source="manual",
    )
    db.add(manual_clf)
    ticket.is_corrected = True
    db.commit()
    db.refresh(correction)
    return correction


def list_corrections(db: Session) -> list[ManualCorrection]:
    return list(db.scalars(select(ManualCorrection).order_by(ManualCorrection.created_at.desc())).all())


def list_unknown(db: Session, status: str | None = None) -> list[UnknownSample]:
    stmt = select(UnknownSample).order_by(UnknownSample.created_at.desc())
    if status:
        stmt = stmt.where(UnknownSample.status == status)
    return list(db.scalars(stmt).all())


def resolve_unknown(db: Session, sample_id: int, payload: ResolveUnknownIn) -> UnknownSample:
    sample = db.get(UnknownSample, sample_id)
    if sample is None:
        raise KeyError("未知样本不存在")
    if not payload.ignore:
        valid = {t.code for t in db.scalars(select(Topic)).all()}
        bad = [c for c in payload.topic_codes if c not in valid]
        if bad:
            raise ValueError(f"无效主题标签: {bad}")
    sample.status = "ignored" if payload.ignore else "resolved"
    sample.resolved_topic_codes = [] if payload.ignore else payload.topic_codes
    sample.resolved_at = utcnow()
    db.commit()
    db.refresh(sample)
    return sample


def training_samples(db: Session) -> list[dict]:
    """导出人工纠正后的样本，可直接回灌朴素贝叶斯训练。"""
    out = []
    for c in db.scalars(select(ManualCorrection)).all():
        ticket = db.get(Ticket, c.ticket_id)
        out.append(
            {
                "ticket_no": ticket.ticket_no,
                "text": f"{ticket.title}\n{ticket.content}",
                "labels": c.corrected_labels,
            }
        )
    return out
