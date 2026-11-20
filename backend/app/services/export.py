"""基于角色的工单导出与审计。

角色权限：
- admin:   可导出完整工单内容
- analyst: 可导出分类结果，工单内容中的客户标识做脱敏
- viewer:  仅可导出聚合统计，禁止导出台账
"""
from __future__ import annotations

import csv
import io
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuditLog, Classification, Ticket
from .tickets import _latest_classification  # noqa: PLC2701

ROLE_PERMISSIONS = {
    "admin": {"export_detail": True, "raw_content": True},
    "analyst": {"export_detail": True, "raw_content": False},
    "viewer": {"export_detail": False, "raw_content": False},
}

# 顺序敏感：身份证（18/15 位）必须先于手机号，否则号段会被手机号规则抢先匹配
PII_PATTERNS = [
    (re.compile(r"\b\d{17}[0-9Xx]\b|\b\d{15}\b"), "<ID_CARD>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "<EMAIL>"),
    (re.compile(r"1[3-9]\d{9}"), "<PHONE>"),
]


def mask_pii(text: str) -> str:
    for pattern, repl in PII_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def export_tickets(db: Session, start, end, include_content: bool, role: str,
                   actor: str) -> tuple[str, str, AuditLog]:
    perms = ROLE_PERMISSIONS.get(role)
    if perms is None:
        raise PermissionError(f"未知角色: {role}")

    stmt = select(Ticket).order_by(Ticket.id)
    if start:
        stmt = stmt.where(Ticket.event_time >= start)
    if end:
        stmt = stmt.where(Ticket.event_time <= end)
    tickets = list(db.scalars(stmt).all())

    buf = io.StringIO()
    if not perms["export_detail"]:
        # viewer 只能拿到聚合数据
        writer = csv.writer(buf)
        writer.writerow(["topic_label", "ticket_count"])
        counts: dict[str, int] = {}
        for t in tickets:
            clf = _latest_classification(db, t.id)
            for lbl in (clf.labels if clf else ["unclassified"]):
                counts[lbl] = counts.get(lbl, 0) + 1
        for lbl, n in sorted(counts.items()):
            writer.writerow([lbl, n])
        denied_reason = "viewer 角色仅允许聚合导出"
    else:
        writer = csv.writer(buf)
        cols = ["ticket_no", "channel", "event_time", "labels", "confidence", "is_unknown", "is_corrected"]
        if include_content and perms["raw_content"]:
            cols += ["title", "content"]
        elif include_content:
            cols += ["title_masked", "content_masked"]
        writer.writerow(cols)
        for t in tickets:
            clf = _latest_classification(db, t.id)
            row = [
                t.ticket_no, t.channel, t.event_time.isoformat(),
                "|".join(clf.labels) if clf else "",
                clf.confidence if clf else "",
                clf.is_unknown if clf else "",
                t.is_corrected,
            ]
            if include_content:
                if perms["raw_content"]:
                    row += [t.title, t.content]
                else:
                    row += [mask_pii(t.title), mask_pii(t.content)]
            writer.writerow(row)
        denied_reason = "" if perms["raw_content"] or not include_content else "内容已脱敏"

    audit = AuditLog(
        actor=actor,
        role=role,
        action="export_tickets",
        detail={
            "rows": len(tickets),
            "include_content": include_content,
            "masked": include_content and not perms["raw_content"],
            "aggregation_only": not perms["export_detail"],
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "note": denied_reason,
        },
    )
    db.add(audit)
    db.commit()
    db.refresh(audit)
    filename = f"tickets_export_{role}.csv"
    return filename, buf.getvalue(), audit


def list_audit(db: Session) -> list[AuditLog]:
    return list(db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc())).all())
