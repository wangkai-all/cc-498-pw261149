"""人工纠正、未知样本与训练样本回灌 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import CorrectionIn, CorrectionOut, ResolveUnknownIn, UnknownSampleOut
from ..services import correction as correction_service

router = APIRouter(prefix="/api", tags=["corrections"])


@router.post("/tickets/{ticket_id}/correction", response_model=CorrectionOut, status_code=201)
def add_correction(ticket_id: int, payload: CorrectionIn, db: Session = Depends(get_db)):
    try:
        return correction_service.add_correction(db, ticket_id, payload)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/corrections", response_model=list[CorrectionOut])
def list_corrections(db: Session = Depends(get_db)):
    return correction_service.list_corrections(db)


@router.get("/unknown-samples", response_model=list[UnknownSampleOut])
def list_unknown(status: str | None = None, db: Session = Depends(get_db)):
    return correction_service.list_unknown(db, status)


@router.post("/unknown-samples/{sample_id}/resolve", response_model=UnknownSampleOut)
def resolve_unknown(sample_id: int, payload: ResolveUnknownIn, db: Session = Depends(get_db)):
    try:
        return correction_service.resolve_unknown(db, sample_id, payload)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/training-samples")
def training_samples(db: Session = Depends(get_db)):
    return correction_service.training_samples(db)
