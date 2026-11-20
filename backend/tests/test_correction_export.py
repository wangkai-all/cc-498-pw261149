"""人工纠正、未知样本处理与权限导出。"""
import io
import csv


def _ingest(client, no, title, content):
    return client.post("/api/tickets", json={"ticket_no": no, "title": title, "content": content})


def test_manual_correction_preserves_machine_result(seeded):
    client = seeded
    _ingest(client, "T-1", "申请退款", "退款不到账")
    r = client.post("/api/tickets/1/correction",
                    json={"corrected_labels": ["after_sale"], "reason": "实际是售后退款",
                          "corrected_by": "alice"})
    assert r.status_code == 201, r.text
    assert r.json()["original_labels"] == ["billing"]
    assert r.json()["corrected_labels"] == ["after_sale"]

    detail = client.get("/api/tickets/1").json()
    sources = [c["source"] for c in detail["classifications"]]
    assert "online" in sources and "manual" in sources  # 机器结果保留
    assert detail["is_corrected"] is True

    # 证据接口包含纠正记录
    ev = client.get("/api/tickets/1/evidence").json()
    assert ev["corrections"][0]["by"] == "alice"


def test_correction_rejects_invalid_label(seeded):
    client = seeded
    _ingest(client, "T-1", "申请退款", "退款不到账")
    r = client.post("/api/tickets/1/correction",
                    json={"corrected_labels": ["not_a_topic"]})
    assert r.status_code == 400


def test_resolve_unknown_sample(seeded):
    client = seeded
    _ingest(client, "T-1", "奇怪问题", "页面按钮点不动")
    sample = client.get("/api/unknown-samples?status=pending").json()[0]
    r = client.post(f"/api/unknown-samples/{sample['id']}/resolve",
                    json={"topic_codes": ["login"]})
    assert r.status_code == 200
    assert r.json()["status"] == "resolved"
    assert r.json()["resolved_topic_codes"] == ["login"]


def test_export_role_permissions_and_audit(seeded):
    client = seeded
    _ingest(client, "T-1", "联系我",
            "退款请发邮件 test@example.com 或致电 13800138000，身份证 11010119900307777X")

    # admin 可导出原始内容
    admin = client.post("/api/export", json={
        "include_content": True, "role": "admin", "actor": "boss",
    })
    assert admin.status_code == 200
    assert "test@example.com" in admin.text  # 原文未脱敏

    # analyst 导出内容需脱敏：身份证整体替换，不会被手机号规则截断
    analyst = client.post("/api/export", json={
        "include_content": True, "role": "analyst", "actor": "bob",
    })
    assert "<EMAIL>" in analyst.text and "<PHONE>" in analyst.text
    assert "<ID_CARD>" in analyst.text
    assert "test@example.com" not in analyst.text
    assert "11010119900307777X" not in analyst.text
    assert "110101" not in analyst.text  # 身份证未被部分匹配残留

    # viewer 只能拿聚合，且无内容列
    viewer = client.post("/api/export", json={
        "include_content": True, "role": "viewer", "actor": "carl",
    })
    rows = list(csv.reader(io.StringIO(viewer.text)))
    assert rows[0] == ["topic_label", "ticket_count"]

    # 非法角色
    denied = client.post("/api/export", json={"role": "hacker", "actor": "x"})
    assert denied.status_code == 403

    # 每次导出都有审计日志
    audit = client.get("/api/export/audit").json()
    assert len(audit) == 3
    assert {a["role"] for a in audit} == {"admin", "analyst", "viewer"}
    assert all(a["action"] == "export_tickets" for a in audit)


def test_training_samples_feed(seeded):
    client = seeded
    _ingest(client, "T-1", "申请退款", "退款不到账")
    client.post("/api/tickets/1/correction",
                json={"corrected_labels": ["after_sale"], "corrected_by": "alice"})
    samples = client.get("/api/training-samples").json()
    assert samples[0]["labels"] == ["after_sale"]
    assert "退款" in samples[0]["text"]
