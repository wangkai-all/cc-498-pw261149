"""趋势、漂移、报表与告警 API。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import AlertOut, AlertRuleIn, ReportOut, ReportQuery
from ..services import alerts as alert_service
from ..services import reports as report_service

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/trend")
def trend(days: int = Query(14, ge=1, le=90), db: Session = Depends(get_db)):
    return report_service.topic_trend(db, days=days)


@router.post("/drift/detect")
def detect_drift(window_days: int = 7, db: Session = Depends(get_db)):
    snap = report_service.detect_drift(db, window_days=window_days)
    return _snap_dict(snap)


@router.get("/drift")
def list_drift(limit: int = 20, db: Session = Depends(get_db)):
    return [_snap_dict(s) for s in report_service.list_drift(db, limit)]


@router.get("/unknown-rate")
def unknown_rate(days: int = 7, db: Session = Depends(get_db)):
    return report_service.unknown_rate(db, days=days)


@router.post("/reports")
def generate_report(query: ReportQuery, db: Session = Depends(get_db)):
    snap = report_service.generate_report(db, query.period_start, query.period_end)
    return ReportOut.model_validate(snap)


@router.get("/reports")
def list_reports(db: Session = Depends(get_db)):
    from ..models import ReportSnapshot
    from sqlalchemy import select
    return [ReportOut.model_validate(s) for s in
            db.scalars(select(ReportSnapshot).order_by(ReportSnapshot.period_end.desc())).all()]


@router.post("/alert-rules", status_code=201)
def create_alert_rule(payload: AlertRuleIn, db: Session = Depends(get_db)):
    rule = alert_service.create_rule(db, payload)
    return {"id": rule.id, "name": rule.name, "metric": rule.metric,
            "threshold": rule.threshold, "level": rule.level.value,
            "topic_ids": rule.topic_ids, "enabled": rule.enabled}


@router.get("/alert-rules")
def list_alert_rules(db: Session = Depends(get_db)):
    return [
        {"id": r.id, "name": r.name, "metric": r.metric, "topic_ids": r.topic_ids,
         "threshold": r.threshold, "level": r.level.value, "enabled": r.enabled}
        for r in alert_service.list_rules(db)
    ]


@router.post("/alerts/evaluate", response_model=list[AlertOut])
def evaluate(window_days: int = 7, db: Session = Depends(get_db)):
    return alert_service.evaluate_alerts(db, window_days=window_days)


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(include_acked: bool = True, db: Session = Depends(get_db)):
    return alert_service.list_alerts(db, include_acked)


@router.post("/alerts/{alert_id}/ack", response_model=AlertOut)
def ack(alert_id: int, db: Session = Depends(get_db)):
    alert = alert_service.acknowledge(db, alert_id)
    if alert is None:
        from fastapi import HTTPException
        raise HTTPException(404, "告警不存在")
    return alert


def _snap_dict(snap) -> dict:
    return {
        "id": snap.id,
        "window_start": snap.window_start,
        "window_end": snap.window_end,
        "total": snap.total,
        "distribution": snap.distribution,
        "drift_score": snap.drift_score,
        "drifted_topics": snap.drifted_topics,
    }
