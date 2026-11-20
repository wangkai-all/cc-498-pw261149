"""pytest 公共夹具：每个测试用独立临时数据库 + TestClient。"""
from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def db_env(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("TICKET_DATABASE_URL", f"sqlite:///{tmp.name}")
    monkeypatch.setenv("TICKET_DB_PATH", tmp.name)

    from backend.app import database
    engine = create_engine(
        f"sqlite:///{tmp.name}",
        connect_args={"check_same_thread": False},
    )
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False,
                                expire_on_commit=False)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", TestSession)
    database.init_db()

    from backend.app import main as main_module
    main_module.init_db = database.init_db
    with TestClient(main_module.app) as client:
        yield client, TestSession

    os.unlink(tmp.name)


@pytest.fixture()
def client(db_env):
    return db_env[0]


@pytest.fixture()
def session_factory(db_env):
    return db_env[1]


KEYWORD_CONFIG = {
    "billing": {"terms": ["退款", "扣费", "发票", "账单", "支付"], "weight": 1.0},
    "login": {"terms": ["登录", "密码", "账号锁定", "验证码"], "weight": 1.0},
    "delivery": {"terms": ["物流", "发货", "配送", "快递", "还没收到"], "weight": 1.0},
    "after_sale": {"terms": ["退货", "换货", "维修", "投诉", "售后"], "weight": 1.0},
}

RULE_CONFIG = [
    {"name": "退款规则", "topic_code": "billing", "all": ["退款"], "any": [], "not": ["物流"], "weight": 1.0},
    {"name": "登录故障", "topic_code": "login", "all": ["登录"], "any": ["失败", "不了", "验证码"], "not": [], "weight": 1.0},
    {"name": "物流催办", "topic_code": "delivery", "all": [], "any": ["还没收到", "物流", "快递"], "not": ["退款"], "weight": 1.0},
    {"name": "退换货", "topic_code": "after_sale", "all": [], "any": ["退货", "换货", "维修"], "not": [], "weight": 1.0},
]


@pytest.fixture()
def seeded(client):
    """建好主题与词表版本并激活，返回 client。"""
    for code, name in [("billing", "账单"), ("login", "登录"), ("delivery", "物流"),
                       ("after_sale", "售后"), ("payment_general", "综合支付")]:
        r = client.post("/api/topics", json={"code": code, "name": name})
        assert r.status_code == 201, r.text
    r = client.post("/api/versions", json={
        "version": "kw-v1", "type": "keyword",
        "description": "测试词表", "config": KEYWORD_CONFIG, "threshold": 0.35,
    })
    assert r.status_code == 201, r.text
    r = client.post("/api/versions", json={
        "version": "rule-v1", "type": "rule",
        "description": "测试规则", "config": RULE_CONFIG, "threshold": 0.35,
    })
    assert r.status_code == 201, r.text
    r = client.post("/api/versions/activate", json={"version": "kw-v1"})
    assert r.status_code == 200, r.text
    return client
