"""主题合并/拆分血缘：报表重建与告警依赖级联更新。"""


def _ingest(client, no, title, content):
    return client.post("/api/tickets", json={"ticket_no": no, "title": title, "content": content})


def test_merge_topics_cascades_reports_and_alerts(seeded):
    client = seeded
    _ingest(client, "T-1", "申请退款", "退款不到账")           # billing
    _ingest(client, "T-2", "登录失败", "登录失败验证码")        # login
    _ingest(client, "T-3", "退货", "我要退货走售后")           # after_sale
    # 多标签工单：主标签 login，次标签 billing，合并后次标签也必须迁移
    _ingest(client, "T-4", "登录不了还被扣款", "登录失败而且支付扣款有问题")

    # 告警规则引用 billing 主题
    rule = client.post("/api/analytics/alert-rules", json={
        "name": "账单波动", "metric": "topic_volume",
        "topic_codes": ["billing"], "threshold": 0.2,
    }).json()

    # 先生成一张报表
    from datetime import datetime, timezone, timedelta
    start = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    end = datetime.now(timezone.utc).isoformat()
    client.post("/api/analytics/reports", json={
        "period_start": start, "period_end": end,
    })

    # 合并 billing + after_sale -> payment_general
    r = client.post("/api/topics/lineage", json={
        "type": "merge",
        "source_topic_codes": ["billing", "after_sale"],
        "target_topic_codes": ["payment_general"],
        "reason": "业务口径调整",
    })
    assert r.status_code == 200, r.text
    meta = r.json()["dependency_update"]
    assert meta["moved_classifications"] >= 3
    assert meta["remapped_alert_rules"] == 1   # 告警依赖已重映射
    assert meta["rebuilt_reports"] == 1        # 报表已重建

    # 多标签工单的次标签 billing 也被迁移，不存在残留旧标签
    detail = client.get("/api/tickets/4").json()
    latest = detail["classifications"][-1]
    assert "billing" not in latest["labels"] and "after_sale" not in latest["labels"]
    assert "payment_general" in latest["labels"]

    # 告警规则的 topic_ids 已指向新主题
    rules = client.get("/api/analytics/alert-rules").json()
    target = next(x for x in rules if x["id"] == rule["id"])
    topics = client.get("/api/topics").json()
    new_id = next(t["id"] for t in topics if t["code"] == "payment_general")
    assert target["topic_ids"] == [new_id]

    # 报表中旧主题消失、新主题出现
    report = client.get("/api/analytics/reports").json()[0]
    codes = {row["topic_code"] for row in report["rows"]}
    assert "billing" not in codes and "after_sale" not in codes
    assert "payment_general" in codes
    assert report["lineage_applied"]  # 血缘应用记录

    # 血缘可查询
    lineage = client.get("/api/topics/lineage").json()
    assert lineage[0]["type"] == "merge"


def test_split_validation(seeded):
    client = seeded
    # 拆分成两个目标
    client.post("/api/topics", json={"code": "login_pw", "name": "密码问题"})
    client.post("/api/topics", json={"code": "login_otp", "name": "验证码问题"})
    r = client.post("/api/topics/lineage", json={
        "type": "split",
        "source_topic_codes": ["login"],
        "target_topic_codes": ["login_pw", "login_otp"],
    })
    assert r.status_code == 200

    # 非法合并参数
    bad = client.post("/api/topics/lineage", json={
        "type": "merge", "source_topic_codes": ["login"],
        "target_topic_codes": ["login_pw", "login_otp"],
    })
    assert bad.status_code == 400


def test_unknown_topic_code_rejected(seeded):
    client = seeded
    r = client.post("/api/topics/lineage", json={
        "type": "merge",
        "source_topic_codes": ["billing", "not_exist"],
        "target_topic_codes": ["payment_general"],
    })
    assert r.status_code == 404
