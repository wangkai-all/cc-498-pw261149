"""工单入库、去重、迟到更新与在线分类。"""
from __future__ import annotations

import hashlib
import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..classifiers import UNKNOWN_LABEL, predict
from ..models import (
    Classification,
    ClassifierVersion,
    Ticket,
    TicketStatus,
    UnknownSample,
    utcnow,
)
from ..schemas import TicketIn

# 事件时间晚于入库时间超过该窗口，视为迟到数据
LATE_WINDOW = timedelta(hours=24)


def dedup_fingerprint(title: str, content: str) -> str:
    norm = re.sub(r"\s+", "", f"{title}{content}").lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def get_active_version(db: Session) -> ClassifierVersion | None:
    return db.scalar(select(ClassifierVersion).where(ClassifierVersion.is_active.is_(True)))


def _store_classification(
    db: Session, ticket: Ticket, version: ClassifierVersion, source: str,
    job_id: int | None = None, allow_replace: bool = False,
) -> Classification:
    from ..models import Topic

    pred = predict(version.type, ticket.title, ticket.content, version.config, version.threshold)
    topic = None
    if not pred.is_unknown and pred.labels:
        topic = db.scalar(select(Topic).where(Topic.code == pred.labels[0]))

    # 在线迟到更新（allow_replace）：同工单同版本覆盖旧行；
    # 重分类（allow_replace=False）：始终追加新行，旧结果完整保留。
    if allow_replace:
        existing = db.scalar(
            select(Classification).where(
                Classification.ticket_id == ticket.id,
                Classification.version_id == version.id,
                Classification.job_id.is_(None),
            )
        )
        if existing is not None:
            db.delete(existing)
            db.flush()

    clf = Classification(
        ticket_id=ticket.id,
        version_id=version.id,
        topic_id=topic.id if topic else None,
        labels=pred.labels,
        scores=pred.scores,
        is_unknown=pred.is_unknown,
        confidence=pred.confidence,
        evidence=pred.evidence,
        source=source,
        job_id=job_id,
    )
    db.add(clf)
    db.flush()
    if pred.is_unknown:
        db.add(
            UnknownSample(
                ticket_id=ticket.id,
                version_id=version.id,
                top_guesses=pred.scores,
            )
        )
    return clf


def ingest_ticket(db: Session, payload: TicketIn) -> tuple[Ticket, Classification | None, bool]:
    """返回 (工单, 分类结果或None, 是否重复)。重复工单只登记引用，不重复分类。"""
    fp = dedup_fingerprint(payload.title, payload.content)
    existing = db.scalar(select(Ticket).where(Ticket.dedup_hash == fp))
    if existing:
        existing.duplicate_of = existing.duplicate_of or existing.id
        return existing, None, True

    now = utcnow()
    event_time = payload.event_time or now
    ticket = Ticket(
        ticket_no=payload.ticket_no,
        title=payload.title,
        content=payload.content,
        channel=payload.channel,
        customer_id=payload.customer_id,
        dedup_hash=fp,
        event_time=event_time,
        created_at=now,
        updated_at=now,
    )
    db.add(ticket)
    db.flush()

    clf = None
    version = get_active_version(db)
    if version:
        clf = _store_classification(db, ticket, version, source="online")
    db.commit()
    db.refresh(ticket)
    return ticket, clf, False


def apply_late_update(db: Session, ticket: Ticket, new_title: str | None = None,
                      new_content: str | None = None, event_time=None) -> tuple[Ticket, bool, Classification | None]:
    """迟到更新：事件时间落在 LATE_WINDOW 之外才认定为迟到；用当前激活版本重新分类。

    历史分类结果保留，新结果追加为新行。
    """
    now = utcnow()
    is_late = event_time is not None and now - event_time > LATE_WINDOW
    if new_title:
        ticket.title = new_title
    if new_content:
        ticket.content = new_content
    ticket.event_time = event_time or ticket.event_time
    ticket.updated_at = now
    if is_late:
        ticket.status = TicketStatus.updated

    clf = None
    version = get_active_version(db)
    if version:
        clf = _store_classification(db, ticket, version, source="online", allow_replace=True)
    db.commit()
    db.refresh(ticket)
    return ticket, is_late, clf


def classify_with_version(db: Session, ticket: Ticket, version: ClassifierVersion,
                          source: str = "reclass", job_id: int | None = None) -> Classification:
    return _store_classification(db, ticket, version, source, job_id)


def _latest_classification(db: Session, ticket_id: int) -> Classification | None:
    """工单最近一次分类结果（重分类/人工纠正后以最新行为准）。"""
    return db.scalar(
        select(Classification)
        .where(Classification.ticket_id == ticket_id)
        .order_by(Classification.created_at.desc(), Classification.id.desc())
    )
