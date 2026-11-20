"""初始化演示数据：主题、词表/规则/模型版本、工单与告警规则。

运行：.venv/bin/python -m backend.scripts.seed
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from backend.app.database import SessionLocal, init_db
from backend.app.models import (
    AlertLevel,
    AlertRule,
    ClassifierType,
    ClassifierVersion,
    Ticket,
    Topic,
    utcnow,
)
from backend.app.schemas import TicketIn
from backend.app.services.tickets import ingest_ticket

TOPICS = [
    ("billing", "账单与支付", "扣费、退款、发票相关"),
    ("login", "登录与账号", "登录失败、密码、账号锁定"),
    ("delivery", "物流配送", "发货、配送时效、丢件"),
    ("product", "商品咨询", "库存、规格、使用方法"),
    ("after_sale", "售后服务", "退换货、维修、投诉"),
]

KEYWORD_CONFIG = {
    "billing": {"terms": ["退款", "扣费", "发票", "账单", "支付", "重复扣款"], "weight": 1.0},
    "login": {"terms": ["登录", "密码", "账号锁定", "验证码", "无法登陆"], "weight": 1.0},
    "delivery": {"terms": ["物流", "发货", "配送", "快递", "丢件", "还没收到"], "weight": 1.0},
    "product": {"terms": ["库存", "规格", "尺寸", "怎么用", "使用方法"], "weight": 1.0},
    "after_sale": {"terms": ["退货", "换货", "维修", "投诉", "售后"], "weight": 1.0},
}

RULE_CONFIG = [
    {"name": "退款规则", "topic_code": "billing", "all": ["退款"], "any": [], "not": ["物流"], "weight": 1.0},
    {"name": "重复扣款", "topic_code": "billing", "all": [], "any": ["重复扣款", "扣费两次"], "not": [], "weight": 1.0},
    {"name": "登录故障", "topic_code": "login", "all": ["登录"], "any": ["失败", "不了", "验证码"], "not": [], "weight": 1.0},
    {"name": "物流催办", "topic_code": "delivery", "all": [], "any": ["还没收到", "物流", "快递"], "not": ["退款"], "weight": 1.0},
    {"name": "退换货", "topic_code": "after_sale", "all": [], "any": ["退货", "换货", "维修"], "not": [], "weight": 1.0},
]

TICKETS = [
    ("T-001", "申请退款一直不到账", "我上周申请的退款到现在还没到账，银行卡也没收到，麻烦尽快处理，订单号 A123。"),
    ("T-002", "被重复扣款了", "今天支付时出现重复扣款，一笔订单扣了两次钱，请核实并退款。"),
    ("T-003", "登录失败验证码收不到", "手机 App 登录失败，验证码一直收不到，账号锁定了怎么办？"),
    ("T-004", "物流三天没更新还没收到货", "快递物流信息三天没更新，到现在还没收到货，比较着急。"),
    ("T-005", "商品尺寸规格咨询", "想咨询一下这件衣服的尺寸规格，还有面料怎么保养。"),
    ("T-006", "退货流程咨询", "商品不合适想退货，请问退货流程是什么，售后多久能处理完？"),
    ("T-007", "发票开具问题", "账单里的发票信息有误，需要重新开具增值税发票。"),
    ("T-008", "配送员态度差要投诉", "配送员态度非常差，我要投诉，同时这件商品申请换货。"),
    ("T-009", "页面按钮没反应", "打开活动页面按钮点了没反应，也没有任何提示。"),  # 未知主题
    ("T-010", "会员积分怎么没了", "我的会员积分突然清零了，找不到积分明细。"),          # 未知主题
    ("T-011", "支付密码忘记", "支付密码忘记了，登录后没法付款，怎么重置密码？"),         # 多标签倾向 login/billing
    ("T-012", "发货太慢同时想退款", "下单一周还没发货，物流完全没动静，我要退款并且投诉售后。"),  # 多标签
    ("T-002-DUP", "申请退款一直不到账", "我上周申请的退款到现在还没到账，银行卡也没收到，麻烦尽快处理，订单号 A123。"),  # 重复工单
]


def run() -> None:
    init_db()
    db = SessionLocal()
    try:
        if db.scalar(select(Topic).limit(1)):
            print("种子数据已存在，跳过")
            return

        for code, name, desc in TOPICS:
            db.add(Topic(code=code, name=name, description=desc))
        db.flush()

        v1 = ClassifierVersion(
            version="kw-v1", type=ClassifierType.keyword,
            description="首版关键词表", config=KEYWORD_CONFIG, threshold=0.35,
            is_active=True, activated_at=utcnow(),
        )
        v2 = ClassifierVersion(
            version="rule-v1", type=ClassifierType.rule,
            description="规则表达式版本", config=RULE_CONFIG, threshold=0.35,
        )
        v3 = ClassifierVersion(
            version="model-v1", type=ClassifierType.model,
            description="占位模型版本（用纠正样本训练后替换）", config={"classes": []}, threshold=0.35,
        )
        db.add_all([v1, v2, v3])
        db.flush()

        db.add_all([
            AlertRule(name="主题漂移告警", metric="drift_spike", threshold=0.1,
                      level=AlertLevel.warning),
            AlertRule(name="未知主题比例告警", metric="unknown_rate", threshold=0.1,
                      level=AlertLevel.critical),
            AlertRule(name="主题量波动告警", metric="topic_volume", threshold=0.15,
                      level=AlertLevel.info),
        ])

        now = utcnow()
        for i, (no, title, content) in enumerate(TICKETS):
            payload = TicketIn(
                ticket_no=no, title=title, content=content,
                event_time=now - timedelta(days=i % 5, hours=i),
            )
            _, _, duplicate = ingest_ticket(db, payload)
            if duplicate:
                print(f"  重复工单已登记: {no}")

        # 给最早一张工单制造迟到更新（事件时间在 3 天前，超过 24h 窗口）
        first = db.scalar(select(Ticket).where(Ticket.ticket_no == "T-001"))
        from backend.app.services.tickets import apply_late_update
        apply_late_update(
            db, first,
            new_content="我上周申请的退款到现在还没到账，银行卡也没收到。补充：已经收到退款了，但发票还没开。",
            event_time=now - timedelta(days=3),
        )
        print(f"种子数据完成：{len(TOPICS)} 个主题，3 个分类器版本，{len(TICKETS)} 条工单")
    finally:
        db.close()


if __name__ == "__main__":
    run()
