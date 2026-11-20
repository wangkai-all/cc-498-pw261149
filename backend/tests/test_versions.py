"""版本管理、重分类双结果保留与版本差异。"""


def _ingest(client, no, title, content):
    return client.post("/api/tickets", json={"ticket_no": no, "title": title, "content": content})


def test_reclassification_keeps_original_and_new(seeded):
    client = seeded
    _ingest(client, "T-1", "申请退款", "退款一直不到账")          # kw: billing
    _ingest(client, "T-2", "登录失败", "登录失败验证码收不到")      # kw: login
    _ingest(client, "T-3", "按钮没反应", "页面按钮点了没反应")      # unknown

    job = client.post("/api/versions/reclassify", json={
        "version": "rule-v1", "created_by": "analyst",
    }).json()
    assert job["status"] == "done"
    assert job["total"] == 3
    # 每条 diff 都保留 original 与 new
    diff = job["diffs"][0]
    assert set(diff) >= {"ticket_id", "ticket_no", "original_labels", "new_labels", "changed"}

    detail = client.get("/api/tickets/1").json()
    sources = [(c["version_name"], c["source"]) for c in detail["classifications"]]
    assert ("kw-v1", "online") in sources       # 原结果保留
    assert ("rule-v1", "reclass") in sources    # 新结果追加
    assert all(c["job_id"] is not None for c in detail["classifications"] if c["source"] == "reclass")


def test_unknown_only_reclassification(seeded):
    client = seeded
    _ingest(client, "T-1", "申请退款", "退款不到账")
    _ingest(client, "T-2", "奇怪问题", "页面按钮点了没反应")
    job = client.post("/api/versions/reclassify",
                      json={"version": "rule-v1", "include_unknown_only": True}).json()
    # 只有 unknown 工单进入任务
    assert job["total"] == 2  # total 是候选工单总数
    assert len(job["diffs"]) == 1
    assert job["diffs"][0]["ticket_no"] == "T-2"


def test_version_diff(seeded):
    client = seeded
    _ingest(client, "T-1", "申请退款", "退款不到账")
    client.post("/api/versions/reclassify", json={"version": "rule-v1"})
    d = client.get("/api/versions/diff?a=kw-v1&b=rule-v1").json()
    assert d["compared"] >= 1
    assert "changes" in d and "changed_count" in d


def test_activation_switch_and_alert(seeded):
    client = seeded
    r = client.post("/api/versions/activate", json={"version": "rule-v1"})
    assert r.status_code == 200
    assert r.json()["is_active"] is True
    versions = client.get("/api/versions").json()
    assert sum(1 for v in versions if v["is_active"]) == 1  # 同时只有一个激活版本
    # 版本切换产生告警
    alerts = client.get("/api/analytics/alerts").json()
    assert any("版本切换" in a["title"] for a in alerts)


def test_model_training_from_corrections(seeded):
    client = seeded
    # 至少两条纠正样本
    for i, (title, content, label) in enumerate([
        ("退款问题", "退款扣费发票账单支付", "billing"),
        ("退款咨询", "支付扣费退款", "billing"),
        ("登录问题", "登录密码验证码账号锁定", "login"),
        ("登不上", "账号锁定登录失败验证码", "login"),
    ]):
        _ingest(client, f"T-{i}", title, content)
        client.post(f"/api/tickets/{i+1}/correction",
                    json={"corrected_labels": [label], "corrected_by": "analyst"})
    r = client.post("/api/versions/train",
                    json={"version": "model-v2", "description": "纠正样本训练"})
    assert r.status_code == 201, r.text
    assert r.json()["type"] == "model"
    assert r.json()["config"]["trained_docs"] == 4

    client.post("/api/versions/activate", json={"version": "model-v2"})
    out = _ingest(client, "T-NEW", "退款扣费", "我要退款，扣费有问题").json()
    assert "billing" in out["classification"]["labels"]
