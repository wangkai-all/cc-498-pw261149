"""分类器版本管理与历史工单重分类。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..classifiers import UNKNOWN_LABEL
from ..models import (
    Alert,
    Classification,
    ClassifierVersion,
    ReclassificationJob,
    Ticket,
    Topic,
    utcnow,
)
from ..schemas import ReclassifyIn, VersionIn
from .tickets import classify_with_version, get_active_version, _latest_classification


def create_version(db: Session, payload: VersionIn) -> ClassifierVersion:
    version = ClassifierVersion(
        version=payload.version,
        type=payload.type,
        description=payload.description,
        config=payload.config,
        threshold=payload.threshold,
        created_by=payload.created_by,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def activate_version(db: Session, version_str: str) -> ClassifierVersion:
    target = db.scalar(select(ClassifierVersion).where(ClassifierVersion.version == version_str))
    if target is None:
        raise KeyError(f"版本不存在: {version_str}")
    for v in db.scalars(select(ClassifierVersion)).all():
        v.is_active = False
        v.activated_at = None
    target.is_active = True
    target.activated_at = utcnow()
    db.add(
        Alert(
            title=f"分类器版本切换为 {target.version}",
            detail={"version": target.version, "type": target.type.value},
        )
    )
    db.commit()
    db.refresh(target)
    return target


def run_reclassification(db: Session, payload: ReclassifyIn) -> ReclassificationJob:
    """对历史工单用指定版本重分类。

    - 旧 Classification 行保留不动，新行写入并关联 job；
    - 逐条记录 original_labels -> new_labels，统计 changed / unknown_resolved。
    """
    version = (
        db.scalar(select(ClassifierVersion).where(ClassifierVersion.version == payload.version))
        if payload.version
        else get_active_version(db)
    )
    if version is None:
        raise KeyError("未指定重分类版本，且当前没有激活版本")

    stmt = select(Ticket)
    if payload.start:
        stmt = stmt.where(Ticket.event_time >= payload.start)
    if payload.end:
        stmt = stmt.where(Ticket.event_time <= payload.end)
    if payload.topic_codes:
        topic_ids = [
            t.id for t in db.scalars(select(Topic).where(Topic.code.in_(payload.topic_codes))).all()
        ]
        matched_ticket_ids = select(Classification.ticket_id).where(
            Classification.topic_id.in_(topic_ids)
        )
        stmt = stmt.where(Ticket.id.in_(matched_ticket_ids))
    tickets = list(db.scalars(stmt.order_by(Ticket.id)).all())

    job = ReclassificationJob(
        version_id=version.id,
        filter_spec=payload.model_dump(mode="json"),
        status="running",
        total=len(tickets),
        created_by=payload.created_by,
    )
    db.add(job)
    db.flush()

    diffs: list[dict] = []
    changed = 0
    unknown_resolved = 0
    for ticket in tickets:
        if payload.include_unknown_only:
            latest = _latest_classification(db, ticket.id)
            if latest is None or not latest.is_unknown:
                continue
        old = _latest_classification(db, ticket.id)
        old_labels = list(old.labels) if old else []
        was_unknown = bool(old and old.is_unknown)

        new_clf = classify_with_version(db, ticket, version, source="reclass", job_id=job.id)
        labels_changed = old_labels != new_clf.labels
        if labels_changed:
            changed += 1
        if was_unknown and not new_clf.is_unknown:
            unknown_resolved += 1
        diffs.append(
            {
                "ticket_id": ticket.id,
                "ticket_no": ticket.ticket_no,
                "original_labels": old_labels,
                "new_labels": list(new_clf.labels),
                "changed": labels_changed,
            }
        )

    job.status = "done"
    job.changed = changed
    job.unknown_resolved = unknown_resolved
    job.diffs = diffs
    job.finished_at = utcnow()
    db.commit()
    db.refresh(job)
    return job


def version_diff(db: Session, version_a: str, version_b: str) -> dict:
    """两个版本在同一批工单上的结果差异（基于各版本最近一次分类）。"""
    va = db.scalar(select(ClassifierVersion).where(ClassifierVersion.version == version_a))
    vb = db.scalar(select(ClassifierVersion).where(ClassifierVersion.version == version_b))
    if not va or not vb:
        raise KeyError("版本不存在")

    def results(version_id: int) -> dict[int, list[str]]:
        out: dict[int, list[str]] = {}
        for clf in db.scalars(
            select(Classification).where(Classification.version_id == version_id)
        ).all():
            # 同工单同版本只可能有一行（uq_ticket_version）
            out[clf.ticket_id] = list(clf.labels)
        return out

    ra, rb = results(va.id), results(vb.id)
    common = sorted(set(ra) & set(rb))
    changed = [
        {"ticket_id": tid, "labels_a": ra[tid], "labels_b": rb[tid]}
        for tid in common
        if ra[tid] != rb[tid]
    ]
    return {
        "version_a": version_a,
        "version_b": version_b,
        "compared": len(common),
        "changed_count": len(changed),
        "only_in_a": sorted(set(ra) - set(rb)),
        "only_in_b": sorted(set(rb) - set(ra)),
        "changes": changed[:200],
    }
