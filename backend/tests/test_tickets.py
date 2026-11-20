"""工单入库：重复工单去重、迟到更新与在线分类。"""
from datetime import datetime, timedelta, timezone

from backend.app.models import Ticket, TicketStatus
from backend.app.services.tickets import dedup_fingerprint


def _ticket(no: str, title: str, content: str):
    return {"ticket_no": no, "title": title, "content": content}


def test_dedup_fingerprint_ignores_whitespace():
    assert dedup_fingerprint("退款 问题", "正文") == dedup_fingerprint("退款问题", " 正文")


def test_duplicate_ticket_not_reclassified(seeded):
    client = seeded
    r1 = client.post("/api/tickets", json=_ticket("T-1", "申请退款", "退款一直不到账"))
    assert r1.status_code == 201
    assert r1.json()["duplicate"] is False
    assert r1.json()["classification"]["labels"] == ["billing"]

    r2 = client.post("/api/tickets", json=_ticket("T-1-DUP", "申请退款", "退款一直不到账"))
    assert r2.json()["duplicate"] is True
    assert r2.json()["classification"] is None  # 重复工单不重复分类

    tickets = client.get("/api/tickets").json()
    assert len(tickets) == 1  # 只建了一张工单


def test_unknown_ticket_queued(seeded):
    client = seeded
    client.post("/api/tickets", json=_ticket("T-2", "页面崩溃", "按钮点了没反应"))
    unknown = client.get("/api/unknown-samples").json()
    assert len(unknown) == 1
    assert unknown[0]["status"] == "pending"


def test_late_update_records_and_reclassifies(seeded, session_factory):
    client = seeded
    client.post("/api/tickets", json=_ticket("T-3", "申请退款", "退款不到账"))
    old_event = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    r = client.post(f"/api/tickets/1/late-update", json={
        "content": "退款不到账，发票也有问题",
        "event_time": old_event,
    })
    assert r.status_code == 200
    assert r.json()["is_late"] is True
    assert r.json()["ticket"]["status"] == TicketStatus.updated.value

    # 同版本迟到更新只保留一行在线结果（覆盖），历史不膨胀
    with session_factory() as db:
        ticket = db.get(Ticket, 1)
        online_rows = [c for c in ticket.classifications if c.source == "online"]
        assert len(online_rows) == 1


def test_recent_update_is_not_late(seeded):
    client = seeded
    client.post("/api/tickets", json=_ticket("T-4", "申请退款", "退款不到账"))
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    r = client.post("/api/tickets/1/late-update", json={
        "content": "补充一点信息", "event_time": recent,
    })
    assert r.json()["is_late"] is False
