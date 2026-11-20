"""基于人工纠正样本训练新版本模型。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..classifiers import train_naive_bayes
from ..models import ClassifierType, ClassifierVersion, ManualCorrection, Ticket
from .tickets import get_active_version


def train_model_version(db: Session, version: str, description: str,
                        created_by: str = "admin", threshold: float = 0.35) -> ClassifierVersion:
    samples: list[tuple[str, list[str]]] = []
    for corr in db.scalars(select(ManualCorrection)).all():
        ticket = db.get(Ticket, corr.ticket_id)
        if ticket:
            samples.append((f"{ticket.title}\n{ticket.content}", corr.corrected_labels))
    if len(samples) < 2:
        raise ValueError("训练样本不足：至少需要 2 条人工纠正数据")
    config = train_naive_bayes(samples)
    if db.scalar(select(ClassifierVersion).where(ClassifierVersion.version == version)):
        raise KeyError(f"版本号已存在: {version}")
    cv = ClassifierVersion(
        version=version,
        type=ClassifierType.model,
        description=description,
        config=config,
        threshold=threshold,
        created_by=created_by,
    )
    db.add(cv)
    db.commit()
    db.refresh(cv)
    return cv


def current_threshold(db: Session) -> float:
    v = get_active_version(db)
    return v.threshold if v else 0.35
