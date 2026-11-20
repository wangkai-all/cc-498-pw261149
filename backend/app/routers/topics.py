"""主题与血缘 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Topic
from ..schemas import LineageIn, TopicIn, TopicOut
from ..services import topics as topic_service

router = APIRouter(prefix="/api/topics", tags=["topics"])


@router.get("", response_model=list[TopicOut])
def list_topics(db: Session = Depends(get_db)):
    return list(db.scalars(select(Topic).order_by(Topic.id)).all())


@router.post("", response_model=TopicOut, status_code=201)
def create_topic(payload: TopicIn, db: Session = Depends(get_db)):
    if db.scalar(select(Topic).where(Topic.code == payload.code)):
        raise HTTPException(409, f"主题 code 已存在: {payload.code}")
    return topic_service.create_topic(db, payload)


@router.post("/lineage")
def apply_lineage(payload: LineageIn, db: Session = Depends(get_db)):
    try:
        lineage, meta = topic_service.apply_lineage(db, payload)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {
        "lineage_id": lineage.id,
        "type": lineage.type.value,
        "source_topic_ids": lineage.source_topic_ids,
        "target_topic_ids": lineage.target_topic_ids,
        "dependency_update": meta,
    }


@router.get("/lineage")
def list_lineage(db: Session = Depends(get_db)):
    return topic_service.list_lineage(db)
