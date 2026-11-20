"""词表/规则/模型三种引擎、多标签、未知主题与样本证据测试。"""
from backend.app.classifiers import (
    predict_keyword,
    predict_model,
    predict_rule,
    train_naive_bayes,
)
from backend.app.models import ClassifierType
from backend.app.classifiers import predict


def test_keyword_single_and_multi_label():
    config = {
        "billing": {"terms": ["退款", "发票"], "weight": 1.0},
        "delivery": {"terms": ["物流", "快递"], "weight": 1.0},
    }
    p = predict_keyword("申请退款但物流一直没更新".lower(), config, 0.35)
    assert not p.is_unknown
    assert set(p.labels) == {"billing", "delivery"}  # 多标签
    assert all(e["type"] == "keyword" for e in p.evidence)
    assert p.confidence >= 0.35


def test_keyword_unknown_when_no_hit():
    p = predict_keyword("页面按钮点不动", {"billing": {"terms": ["退款"]}}, 0.35)
    assert p.is_unknown
    assert p.labels == ["unknown"]


def test_keyword_below_threshold_keeps_guesses():
    config = {"billing": {"terms": ["退款", "发票", "账单", "支付", "扣费"], "weight": 0.3}}
    p = predict_keyword("我要退款", config, 0.5)
    assert p.is_unknown  # 低于阈值判未知
    assert "billing" in p.scores  # 候选保留供人工参考


def test_rule_all_any_not_logic():
    config = [
        {"name": "r1", "topic_code": "login", "all": ["登录"], "any": ["失败", "验证码"], "not": [], "weight": 1.0},
        {"name": "r2", "topic_code": "billing", "all": ["退款"], "any": [], "not": ["物流"], "weight": 1.0},
    ]
    assert predict_rule("登录失败收不到验证码", config, 0.35).labels == ["login"]
    # not 命中则排除
    p = predict_rule("退款但物流卡住", config, 0.35)
    assert "billing" not in p.labels
    # all 未满足不命中
    assert predict_rule("只是失败了", config, 0.35).is_unknown


def test_naive_bayes_train_and_predict():
    samples = [
        ("我要退款 扣费不对", ["billing"]),
        ("退款什么时候到账 发票", ["billing"]),
        ("登录失败 验证码收不到", ["login"]),
        ("账号锁定 无法登录", ["login"]),
        ("物流快递还没收到 发货慢", ["delivery"]),
        ("快递丢件 配送", ["delivery"]),
    ]
    model = train_naive_bayes(samples)
    p = predict_model("退款和发票问题", model, 0.35)
    assert "billing" in p.labels
    assert p.evidence and p.evidence[0]["type"] == "model"
    assert p.evidence[0]["top_features"]  # 模型证据可解释


def test_empty_model_predicts_unknown():
    p = predict_model("任意文本", {"classes": []}, 0.35)
    assert p.is_unknown


def test_predict_dispatch_by_type():
    kw = predict(ClassifierType.keyword, "退款", "", {"billing": {"terms": ["退款"]}}, 0.35)
    assert kw.labels == ["billing"]
    rl = predict(ClassifierType.rule, "登录失败", "",
                 [{"topic_code": "login", "all": ["登录"], "any": ["失败"], "not": []}], 0.35)
    assert rl.labels == ["login"]
