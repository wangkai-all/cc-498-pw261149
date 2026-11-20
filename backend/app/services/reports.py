"""主题趋势、漂移检测与报表快照（支持按血缘口径重建）。"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..classifiers import UNKNOWN_LABEL
from ..models import (
    Alert,
    Classification,
    DriftSnapshot,
    ReportSnapshot,
    Ticket,
    TopicLineage,
    utcnow,
)


def _latest_classifications(db: Session, start: datetime | None, end: datetime | None) -> list[Classification]:
    """取窗口内每张工单的最新分类（重分类后以新结果为准，旧结果仍保留在表中）。"""
    stmt = select(Classification).join(Ticket, Ticket.id == Classification.ticket_id)
    if start:
        stmt = stmt.where(Ticket.event_time >= start)
    if end:
        stmt = stmt.where(Ticket.event_time <= end)
    latest: dict[int, Classification] = {}
    for clf in db.scalars(stmt).all():
        cur = latest.get(clf.ticket_id)
        if cur is None or (clf.created_at, clf.id) > (cur.created_at, cur.id):
            latest[clf.ticket_id] = clf
    return list(latest.values())


def topic_trend(db: Session, days: int = 14) -> dict:
    """按天输出主题量趋势，含 unknown 系列。"""
    end = utcnow()
    start = end - timedelta(days=days)
    clfs = _latest_classifications(db, start, end)
    buckets: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for clf in clfs:
        ticket = db.get(Ticket, clf.ticket_id)
        day = ticket.event_time.strftime("%Y-%m-%d")
        for label in clf.labels:
            buckets[day][label] += 1
    series: dict[str, list[int]] = defaultdict(list)
    labels = sorted({lbl for day in buckets.values() for lbl in day})
    days_axis = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days + 1)]
    for day in days_axis:
        for lbl in labels:
            series[lbl].append(buckets.get(day, {}).get(lbl, 0))
    return {"days": days_axis, "labels": labels, "series": series}


def _distribution(clfs: list[Classification]) -> tuple[dict[str, float], int]:
    total = len(clfs)
    counts: dict[str, int] = defaultdict(int)
    for clf in clfs:
        if clf.is_unknown or not clf.labels:
            counts[UNKNOWN_LABEL] += 1
        else:
            for lbl in clf.labels:
                counts[lbl] += 1
    if not total:
        return {}, 0
    return {k: round(v / total, 4) for k, v in sorted(counts.items())}, total


def _js_divergence(p: dict[str, float], q: dict[str, float]) -> float:
    """Jensen-Shannon 散度（0~ln2，归一化到 0~1），衡量两个主题分布的漂移。"""
    keys = set(p) | set(q)
    if not keys:
        return 0.0
    m = {k: (p.get(k, 0.0) + q.get(k, 0.0)) / 2 for k in keys}

    def kl(a: dict[str, float], b: dict[str, float]) -> float:
        return sum(a.get(k, 0.0) * math.log((a.get(k, 0.0) + 1e-12) / (b.get(k, 0.0) + 1e-12))
                   for k in keys if a.get(k, 0.0) > 0)

    js = 0.5 * kl(p, m) + 0.5 * kl(q, m)
    return round(js / math.log(2), 4) if math.log(2) else 0.0


def detect_drift(db: Session, window_days: int = 7, drift_threshold: float = 0.15,
                 topic_change_threshold: float = 0.1) -> DriftSnapshot:
    """对比最近窗口与上一窗口的主题分布，输出漂移分数与发生显著变化的主题。"""
    end = utcnow()
    cur_start = end - timedelta(days=window_days)
    prev_start = cur_start - timedelta(days=window_days)

    cur = _latest_classifications(db, cur_start, end)
    prev = _latest_classifications(db, prev_start, cur_start)
    cur_dist, total = _distribution(cur)
    prev_dist, _ = _distribution(prev)

    drift_score = _js_divergence(prev_dist, cur_dist) if prev_dist else None
    drifted = [
        {
            "topic": k,
            "previous_ratio": prev_dist.get(k, 0.0),
            "current_ratio": v,
            "delta": round(v - prev_dist.get(k, 0.0), 4),
        }
        for k, v in cur_dist.items()
        if abs(v - prev_dist.get(k, 0.0)) >= topic_change_threshold
    ]

    snap = DriftSnapshot(
        window_start=cur_start,
        window_end=end,
        version_id=None,
        total=total,
        distribution=cur_dist,
        drift_score=drift_score,
        drifted_topics=drifted,
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


def list_drift(db: Session, limit: int = 20) -> list[DriftSnapshot]:
    return list(db.scalars(
        select(DriftSnapshot).order_by(DriftSnapshot.window_end.desc()).limit(limit)
    ).all())


def rebuild_reports(db: Session, extra_lineage: int | None = None) -> int:
    """按当前最新分类结果重建全部报表快照。

    主题合并/拆分后调用：分类结果已迁移到新主题，重建后报表口径自动更新，
    lineage_applied 记录应用过的血缘 id，告警依赖在 topics 服务中同步重映射。
    """
    existing = list(db.scalars(select(ReportSnapshot)).all())
    lineage_ids = [ln.id for ln in db.scalars(select(TopicLineage)).all()]
    if extra_lineage and extra_lineage not in lineage_ids:
        lineage_ids.append(extra_lineage)
    rebuilt = 0
    for snap in existing:
        clfs = _latest_classifications(db, snap.period_start, snap.period_end)
        dist, _ = _distribution(clfs)
        counts = defaultdict(int)
        for clf in clfs:
            for lbl in clf.labels:
                counts[lbl] += 1
        total = len(clfs)
        snap.rows = [
            {
                "topic_code": k,
                "count": counts[k],
                "ratio": round(counts[k] / total, 4) if total else 0.0,
            }
            for k in sorted(counts)
        ]
        snap.lineage_applied = lineage_ids
        snap.generated_at = utcnow()
        rebuilt += 1
    db.commit()
    return rebuilt


def generate_report(db: Session, period_start: datetime, period_end: datetime) -> ReportSnapshot:
    existing = db.scalar(
        select(ReportSnapshot).where(
            ReportSnapshot.period_start == period_start,
            ReportSnapshot.period_end == period_end,
        )
    )
    clfs = _latest_classifications(db, period_start, period_end)
    counts: dict[str, int] = defaultdict(int)
    for clf in clfs:
        for lbl in clf.labels:
            counts[lbl] += 1
    total = len(clfs)
    rows = [
        {"topic_code": k, "count": counts[k], "ratio": round(counts[k] / total, 4) if total else 0.0}
        for k in sorted(counts)
    ]
    lineage_ids = [ln.id for ln in db.scalars(select(TopicLineage)).all()]
    if existing:
        existing.rows = rows
        existing.lineage_applied = lineage_ids
        existing.generated_at = utcnow()
        snap = existing
    else:
        snap = ReportSnapshot(
            period_start=period_start, period_end=period_end,
            rows=rows, lineage_applied=lineage_ids,
        )
        db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


def unknown_rate(db: Session, days: int = 7) -> dict:
    end = utcnow()
    start = end - timedelta(days=days)
    clfs = _latest_classifications(db, start, end)
    total = len(clfs)
    unknown = sum(1 for c in clfs if c.is_unknown)
    return {
        "window_days": days,
        "total": total,
        "unknown": unknown,
        "unknown_rate": round(unknown / total, 4) if total else 0.0,
    }
