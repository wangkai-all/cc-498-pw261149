"""主题管理、合并/拆分血缘及报表与告警依赖的级联更新。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    AlertRule,
    Classification,
    Topic,
    TopicLineage,
    utcnow,
)
from ..schemas import LineageIn, TopicIn
from .reports import rebuild_reports


def create_topic(db: Session, payload: TopicIn) -> Topic:
    topic = Topic(code=payload.code, name=payload.name, description=payload.description)
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


def _codes_to_ids(db: Session, codes: list[str]) -> list[int]:
    if not codes:
        return []
    rows = db.scalars(select(Topic).where(Topic.code.in_(codes))).all()
    found = {t.code: t.id for t in rows}
    missing = [c for c in codes if c not in found]
    if missing:
        raise KeyError(f"主题不存在: {missing}")
    return [found[c] for c in codes]


def _remap_alert_rules(db: Session, mapping: dict[int, int]) -> int:
    """告警规则里引用的旧主题 id 替换为新主题 id，保证依赖不断裂。"""
    count = 0
    for rule in db.scalars(select(AlertRule)).all():
        new_ids = [mapping.get(tid, tid) for tid in rule.topic_ids]
        if new_ids != rule.topic_ids:
            rule.topic_ids = list(dict.fromkeys(new_ids))  # 去重保持顺序
            count += 1
    return count


def apply_lineage(db: Session, payload: LineageIn) -> tuple[TopicLineage, dict]:
    source_ids = _codes_to_ids(db, payload.source_topic_codes)
    target_ids = _codes_to_ids(db, payload.target_topic_codes)

    if payload.type.value == "merge":
        if len(source_ids) < 2 or len(target_ids) != 1:
            raise ValueError("合并需要 >=2 个源主题和恰好 1 个目标主题")
        mapping = {sid: target_ids[0] for sid in source_ids}
    else:
        if len(source_ids) != 1 or len(target_ids) < 2:
            raise ValueError("拆分需要恰好 1 个源主题和 >=2 个目标主题")
        # 拆分无法自动决定每条工单归属，统一迁到第一个目标，其余由人工纠正细化
        mapping = {source_ids[0]: target_ids[0]}

    lineage = TopicLineage(
        type=payload.type,
        source_topic_ids=source_ids,
        target_topic_ids=target_ids,
        reason=payload.reason,
    )
    db.add(lineage)
    db.flush()

    old_topics = db.scalars(select(Topic).where(Topic.id.in_(list(mapping.keys())))).all()
    id_to_code = {t.id: t.code for t in old_topics}
    code_mapping = {id_to_code[old_id]: db.get(Topic, new_id).code
                    for old_id, new_id in mapping.items()}

    # 全表迁移：多标签工单的次要标签也必须替换，不能只处理主 topic_id 命中的行
    all_rows = db.scalars(select(Classification)).all()
    moved = 0
    for row in all_rows:
        new_labels = [code_mapping.get(lbl, lbl) for lbl in row.labels]
        if new_labels != row.labels:
            row.labels = new_labels
            moved += 1
        if row.topic_id in mapping:
            row.topic_id = mapping[row.topic_id]

    for old_topic in old_topics:
        old_topic.active = False
        old_topic.archived_at = utcnow()

    remapped_rules = _remap_alert_rules(db, mapping)
    # 依赖级联：重建全部报表快照，使其与最新主题口径一致
    rebuilt = rebuild_reports(db, extra_lineage=lineage.id)

    db.commit()
    db.refresh(lineage)
    meta = {
        "moved_classifications": moved,
        "remapped_alert_rules": remapped_rules,
        "rebuilt_reports": rebuilt,
    }
    return lineage, meta


def list_lineage(db: Session) -> list[dict]:
    out = []
    for ln in db.scalars(select(TopicLineage).order_by(TopicLineage.created_at.desc())).all():
        def _codes(ids: list[int]) -> list[str]:
            return [t.code for t in db.scalars(select(Topic).where(Topic.id.in_(ids))).all()]
        out.append(
            {
                "id": ln.id,
                "type": ln.type.value,
                "sources": _codes(ln.source_topic_ids),
                "targets": _codes(ln.target_topic_ids),
                "reason": ln.reason,
                "created_at": ln.created_at,
            }
        )
    return out
