"""工单、迟到更新与样本证据 API。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Classification, ClassifierVersion, Ticket
from ..schemas import TicketIn, TicketOut
from ..services import tickets as ticket_service

router = APIRouter(prefix="/api/tickets", tags=["tickets"])


class LateUpdateIn(BaseModel):
    title: str | None = None
    content: str | None = None
    event_time: datetime


@router.post("", status_code=201)
def ingest(payload: TicketIn, db: Session = Depends(get_db)):
    ticket, clf, duplicate = ticket_service.ingest_ticket(db, payload)
    return {
        "ticket": TicketOut.model_validate(ticket),
        "duplicate": duplicate,
        "classification": _clf_dict(clf, db) if clf else None,
    }


@router.get("", response_model=list[TicketOut])
def list_tickets(
    status: str | None = None,
    corrected: bool | None = None,
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
):
    stmt = select(Ticket).order_by(Ticket.id.desc()).limit(limit)
    tickets = list(db.scalars(stmt).all())
    if status:
        tickets = [t for t in tickets if t.status.value == status]
    if corrected is not None:
        tickets = [t for t in tickets if t.is_corrected == corrected]
    return tickets


@router.get("/{ticket_id}")
def get_ticket(ticket_id: int, db: Session = Depends(get_db)):
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(404, "工单不存在")
    base = TicketOut.model_validate(ticket).model_dump()
    base["classifications"] = [_clf_dict(c, db) for c in
                               sorted(ticket.classifications, key=lambda c: c.id)]
    return base


@router.post("/{ticket_id}/late-update")
def late_update(ticket_id: int, payload: LateUpdateIn, db: Session = Depends(get_db)):
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(404, "工单不存在")
    ticket, is_late, clf = ticket_service.apply_late_update(
        db, ticket, payload.title, payload.content, payload.event_time
    )
    return {
        "ticket": TicketOut.model_validate(ticket),
        "is_late": is_late,
        "classification": _clf_dict(clf, db) if clf else None,
    }


@router.get("/{ticket_id}/evidence")
def evidence(ticket_id: int, db: Session = Depends(get_db)):
    """样本证据：该工单所有版本下的命中词/规则/模型特征，含人工纠正记录。"""
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(404, "工单不存在")
    rows = []
    for clf in sorted(ticket.classifications, key=lambda c: c.id):
        version = db.get(ClassifierVersion, clf.version_id)
        rows.append(
            {
                "classification_id": clf.id,
                "version": version.version,
                "type": version.type.value,
                "source": clf.source,
                "labels": clf.labels,
                "scores": clf.scores,
                "confidence": clf.confidence,
                "is_unknown": clf.is_unknown,
                "evidence": clf.evidence,
                "created_at": clf.created_at,
            }
        )
    from ..models import ManualCorrection
    corrections = list(db.scalars(
        select(ManualCorrection).where(ManualCorrection.ticket_id == ticket_id)
    ).all())
    return {"ticket_no": ticket.ticket_no, "classifications": rows,
            "corrections": [{"original": c.original_labels, "corrected": c.corrected_labels,
                             "reason": c.reason, "by": c.corrected_by,
                             "created_at": c.created_at} for c in corrections]}


def _clf_dict(clf: Classification, db: Session) -> dict:
    version = db.get(ClassifierVersion, clf.version_id)
    return {
        "id": clf.id,
        "ticket_id": clf.ticket_id,
        "version_id": clf.version_id,
        "version_name": version.version if version else "",
        "topic_id": clf.topic_id,
        "labels": clf.labels,
        "scores": clf.scores,
        "is_unknown": clf.is_unknown,
        "confidence": clf.confidence,
        "evidence": clf.evidence,
        "source": clf.source,
        "job_id": clf.job_id,
        "created_at": clf.created_at,
    }
