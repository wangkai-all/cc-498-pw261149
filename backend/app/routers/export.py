"""权限导出与审计 API。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import ExportIn
from ..services import export as export_service

router = APIRouter(prefix="/api/export", tags=["export"])


@router.post("")
def export(payload: ExportIn, db: Session = Depends(get_db)):
    try:
        filename, content, audit = export_service.export_tickets(
            db, payload.start, payload.end, payload.include_content,
            payload.role, payload.actor,
        )
    except PermissionError as e:
        from fastapi import HTTPException
        raise HTTPException(403, str(e)) from e
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                 "X-Audit-Id": str(audit.id)},
    )


@router.get("/audit")
def audit_logs(db: Session = Depends(get_db)):
    return [
        {"id": a.id, "actor": a.actor, "role": a.role, "action": a.action,
         "detail": a.detail, "created_at": a.created_at}
        for a in export_service.list_audit(db)
    ]
