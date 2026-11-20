"""告警规则与评估。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Alert, AlertRule, Topic
from ..schemas import AlertRuleIn
from .reports import detect_drift, unknown_rate

# 主题合并/拆分后报表/告警依赖的更新由 services.topics 负责


def create_rule(db: Session, payload: AlertRuleIn) -> AlertRule:
    topic_ids: list[int] = []
    if payload.topic_codes:
        topic_ids = [
            t.id for t in db.scalars(select(Topic).where(Topic.code.in_(payload.topic_codes))).all()
        ]
    rule = AlertRule(
        name=payload.name,
        metric=payload.metric,
        topic_ids=topic_ids,
        threshold=payload.threshold,
        level=payload.level,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def list_rules(db: Session) -> list[AlertRule]:
    return list(db.scalars(select(AlertRule).order_by(AlertRule.id)).all())


def evaluate_alerts(db: Session, window_days: int = 7) -> list[Alert]:
    """根据启用的规则评估当前窗口，命中则产生告警（幂等：同规则同窗口只产生一条）。"""
    rules = [r for r in list_rules(db) if r.enabled]
    if not rules:
        return []
    snap = detect_drift(db, window_days=window_days)
    rate = unknown_rate(db, days=window_days)
    created: list[Alert] = []

    for rule in rules:
        if rule.metric == "drift_spike" and snap.drift_score is not None \
                and snap.drift_score >= rule.threshold:
            detail = {"drift_score": snap.drift_score, "snapshot_id": snap.id,
                      "drifted_topics": snap.drifted_topics}
        elif rule.metric == "unknown_rate" and rate["unknown_rate"] >= rule.threshold:
            detail = rate
        elif rule.metric == "topic_volume":
            hits = [d for d in snap.drifted_topics
                    if abs(d["delta"]) >= rule.threshold
                    and (not rule.topic_ids or _topic_in_rule(db, d["topic"], rule.topic_ids))]
            if not hits:
                continue
            detail = {"topics": hits, "snapshot_id": snap.id}
        else:
            continue
        if _already_alerted(db, rule.id, detail):
            continue
        alert = Alert(
            rule_id=rule.id,
            level=rule.level,
            title=f"{rule.name} 触发",
            detail=detail,
        )
        db.add(alert)
        created.append(alert)
    db.commit()
    for a in created:
        db.refresh(a)
    return created


def _topic_in_rule(db: Session, code: str, topic_ids: list[int]) -> bool:
    topic = db.scalar(select(Topic).where(Topic.code == code))
    return bool(topic and topic.id in topic_ids)


def _already_alerted(db: Session, rule_id: int, detail: dict) -> bool:
    last = db.scalar(
        select(Alert).where(Alert.rule_id == rule_id).order_by(Alert.created_at.desc())
    )
    return bool(last and last.detail == detail)


def list_alerts(db: Session, include_acked: bool = True) -> list[Alert]:
    stmt = select(Alert).order_by(Alert.created_at.desc())
    if not include_acked:
        stmt = stmt.where(Alert.acknowledged.is_(False))
    return list(db.scalars(stmt).all())


def acknowledge(db: Session, alert_id: int) -> Alert | None:
    alert = db.get(Alert, alert_id)
    if alert:
        alert.acknowledged = True
        db.commit()
        db.refresh(alert)
    return alert
