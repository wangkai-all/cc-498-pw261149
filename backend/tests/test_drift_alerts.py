"""漂移检测、告警评估与未知率。"""


def _ingest(client, no, title, content):
    return client.post("/api/tickets", json={"ticket_no": no, "title": title, "content": content})


def test_drift_detection_structure(seeded):
    client = seeded
    for i in range(6):
        _ingest(client, f"T-{i}", "申请退款", f"退款不到账{i}")
    snap = client.post("/api/analytics/drift/detect?window_days=7").json()
    assert snap["total"] >= 1
    assert abs(sum(snap["distribution"].values()) - 1.0) < 0.01
    assert "drifted_topics" in snap


def test_unknown_rate_alert(seeded):
    client = seeded
    # 一半工单无法识别 -> unknown_rate = 0.5
    _ingest(client, "T-1", "申请退款", "退款不到账")
    _ingest(client, "T-2", "奇怪现象", "页面按钮点不动没反应")
    client.post("/api/analytics/alert-rules", json={
        "name": "未知率", "metric": "unknown_rate", "threshold": 0.1, "level": "critical",
    })
    alerts = client.post("/api/analytics/alerts/evaluate?window_days=7").json()
    assert any(a["level"] == "critical" for a in alerts)

    # 幂等：重复评估不产生重复告警
    again = client.post("/api/analytics/alerts/evaluate?window_days=7").json()
    assert again == []

    # 可确认告警
    aid = alerts[0]["id"]
    acked = client.post(f"/api/analytics/alerts/{aid}/ack").json()
    assert acked["acknowledged"] is True


def test_drift_spike_alert(seeded):
    client = seeded
    client.post("/api/analytics/alert-rules", json={
        "name": "漂移", "metric": "drift_spike", "threshold": 0.0,
    })
    alerts = client.post("/api/analytics/alerts/evaluate").json()
    # 无历史窗口时 drift_score 为 None，不应误报
    if alerts:
        assert alerts[0]["detail"]["drift_score"] >= 0.0


def test_trend_api(seeded):
    client = seeded
    _ingest(client, "T-1", "申请退款", "退款不到账")
    trend = client.get("/api/analytics/trend?days=7").json()
    assert len(trend["days"]) == 8
    assert "billing" in trend["series"]
