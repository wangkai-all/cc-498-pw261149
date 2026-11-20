"""分类器版本与重分类 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ClassifierVersion
from ..schemas import ActivateIn, JobOut, ReclassifyIn, VersionIn, VersionOut
from ..services import versions as version_service
from ..services.training import train_model_version
from pydantic import BaseModel


class TrainIn(BaseModel):
    version: str
    description: str = "基于人工纠正样本训练"
    created_by: str = "admin"
    threshold: float = 0.35


router = APIRouter(prefix="/api/versions", tags=["versions"])


@router.get("", response_model=list[VersionOut])
def list_versions(db: Session = Depends(get_db)):
    return list(db.scalars(select(ClassifierVersion).order_by(ClassifierVersion.id)).all())


@router.post("", response_model=VersionOut, status_code=201)
def create_version(payload: VersionIn, db: Session = Depends(get_db)):
    if db.scalar(select(ClassifierVersion).where(ClassifierVersion.version == payload.version)):
        raise HTTPException(409, f"版本号已存在: {payload.version}")
    return version_service.create_version(db, payload)


@router.post("/activate", response_model=VersionOut)
def activate_version(payload: ActivateIn, db: Session = Depends(get_db)):
    try:
        return version_service.activate_version(db, payload.version)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/train", response_model=VersionOut, status_code=201)
def train_from_corrections(payload: TrainIn, db: Session = Depends(get_db)):
    try:
        return train_model_version(
            db, payload.version, payload.description, payload.created_by, payload.threshold
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except KeyError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/reclassify", response_model=JobOut)
def reclassify(payload: ReclassifyIn, db: Session = Depends(get_db)):
    try:
        return version_service.run_reclassification(db, payload)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/reclassify/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db)):
    from ..models import ReclassificationJob
    job = db.get(ReclassificationJob, job_id)
    if job is None:
        raise HTTPException(404, "重分类任务不存在")
    return job


@router.get("/diff")
def version_diff(a: str, b: str, db: Session = Depends(get_db)):
    try:
        return version_service.version_diff(db, a, b)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
