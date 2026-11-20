"""三种分类引擎：词表匹配、规则表达式、朴素贝叶斯模型。

统一输出 Prediction：
- labels: 多标签主题 code 列表；无法识别时为 ["unknown"]
- scores: 每个候选主题的归一化分数 0~1
- confidence: 最高置信度
- evidence: 样本证据（命中词、命中规则、模型特征）
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from .models import ClassifierType

TOKEN_RE = re.compile(r"[a-z0-9]+|[一-鿿]")
UNKNOWN_LABEL = "unknown"


@dataclass
class Prediction:
    labels: list[str]
    scores: dict[str, float]
    confidence: float
    evidence: list[dict[str, Any]] = field(default_factory=list)
    is_unknown: bool = False


def normalize_text(title: str, content: str) -> str:
    return f"{title}\n{content}".lower()


def tokenize(text: str) -> list[str]:
    """英文按词、中文按单字切分，并补充中文二元组捕捉短语。"""
    text = text.lower()
    unigrams = TOKEN_RE.findall(text)
    cjk = [t for t in unigrams if re.match(r"[一-鿿]", t)]
    bigrams = [f"{cjk[i]}{cjk[i + 1]}" for i in range(len(cjk) - 1)]
    return unigrams + bigrams


# ---------------- 词表引擎 ----------------
def predict_keyword(text: str, config: dict, threshold: float) -> Prediction:
    scores: dict[str, float] = {}
    evidence: list[dict[str, Any]] = []
    for topic_code, spec in config.items():
        terms = spec.get("terms", []) if isinstance(spec, dict) else list(spec)
        weight = spec.get("weight", 1.0) if isinstance(spec, dict) else 1.0
        if not terms:
            continue
        hits = [t for t in terms if t.lower() in text]
        if hits:
            # 命中即给基础分，多命中加分；避免单词表被词数稀释导致永远低于阈值
            score = min(1.0, weight * (0.6 + 0.4 * (len(hits) - 1)))
            scores[topic_code] = score
            evidence.append(
                {"type": "keyword", "topic": topic_code, "matched_terms": hits, "score": round(score, 4)}
            )
    return _finalize(scores, evidence, threshold)


# ---------------- 规则引擎 ----------------
def predict_rule(text: str, config: list, threshold: float) -> Prediction:
    scores: dict[str, float] = {}
    evidence: list[dict[str, Any]] = []
    for rule in config:
        topic = rule["topic_code"]
        all_terms = rule.get("all", [])
        any_terms = rule.get("any", [])
        not_terms = rule.get("not", [])
        hit_all = [t for t in all_terms if t.lower() in text]
        hit_any = [t for t in any_terms if t.lower() in text]
        hit_not = [t for t in not_terms if t.lower() in text]
        if hit_not:
            continue
        if all_terms and len(hit_all) != len(all_terms):
            continue
        if any_terms and not hit_any:
            continue
        if not all_terms and not any_terms:
            continue
        score = float(rule.get("weight", 1.0))
        scores[topic] = max(scores.get(topic, 0.0), score)
        evidence.append(
            {
                "type": "rule",
                "topic": topic,
                "rule_name": rule.get("name", topic),
                "matched_all": hit_all,
                "matched_any": hit_any,
                "score": score,
            }
        )
    return _finalize(scores, evidence, threshold)


# ---------------- 朴素贝叶斯模型 ----------------
def train_naive_bayes(samples: list[tuple[str, list[str]]], alpha: float = 1.0) -> dict:
    """samples: [(文本, 标签列表), ...]，多标签样本对每个标签各贡献一次。"""
    vocab: dict[str, int] = {}
    token_counts: dict[str, dict[str, int]] = {}
    doc_counts: dict[str, int] = {}
    total_docs = 0

    for text, labels in samples:
        total_docs += 1
        tokens = tokenize(text)
        for label in labels:
            doc_counts[label] = doc_counts.get(label, 0) + 1
            counts = token_counts.setdefault(label, {})
            for tok in tokens:
                if tok not in vocab:
                    vocab[tok] = len(vocab)
                counts[tok] = counts.get(tok, 0) + 1

    classes = sorted(doc_counts)
    log_prior = {
        c: math.log(doc_counts[c] / total_docs) if total_docs and doc_counts.get(c) else -1e9
        for c in classes
    }
    vocab_size = max(len(vocab), 1)
    feature_log_prob: dict[str, dict[str, float]] = {}
    for c in classes:
        counts = token_counts.get(c, {})
        denom = sum(counts.values()) + alpha * vocab_size
        feature_log_prob[c] = {
            tok: math.log((counts.get(tok, 0) + alpha) / denom) for tok in vocab
        }
    return {
        "engine": "naive_bayes",
        "vocab": vocab,
        "classes": classes,
        "log_prior": log_prior,
        "feature_log_prob": feature_log_prob,
        "trained_docs": total_docs,
    }


def predict_model(text: str, config: dict, threshold: float) -> Prediction:
    classes: list[str] = config["classes"]
    if not classes:
        return Prediction([UNKNOWN_LABEL], {}, 0.0, is_unknown=True)
    tokens = tokenize(text)
    log_post: dict[str, float] = {}
    for c in classes:
        score = config["log_prior"][c]
        flp = config["feature_log_prob"][c]
        for tok in tokens:
            if tok in flp:
                score += flp[tok]
        log_post[c] = score
    # softmax 归一化为可解释概率
    max_log = max(log_post.values())
    exp_scores = {c: math.exp(v - max_log) for c, v in log_post.items()}
    total = sum(exp_scores.values()) or 1.0
    probs = {c: v / total for c, v in exp_scores.items()}

    evidence: list[dict[str, Any]] = []
    flp_lookup = {c: config["feature_log_prob"][c] for c in classes}
    for c in sorted(classes, key=lambda x: probs[x], reverse=True)[:3]:
        top_features = sorted(
            ((tok, flp_lookup[c][tok]) for tok in set(tokens) if tok in flp_lookup[c]),
            key=lambda x: x[1],
            reverse=True,
        )[:5]
        evidence.append(
            {
                "type": "model",
                "topic": c,
                "probability": round(probs[c], 4),
                "top_features": [t for t, _ in top_features],
            }
        )
    return _finalize(probs, evidence, threshold)


def _finalize(scores: dict[str, float], evidence: list, threshold: float) -> Prediction:
    if not scores:
        return Prediction([UNKNOWN_LABEL], {}, 0.0, evidence, is_unknown=True)
    confidence = max(scores.values())
    labels = sorted((c for c, s in scores.items() if s >= threshold), key=lambda c: -scores[c])
    if not labels:
        # 有候选但全部低于阈值：判未知，同时保留候选供人工参考
        return Prediction([UNKNOWN_LABEL], {c: round(s, 4) for c, s in scores.items()},
                          round(confidence, 4), evidence, is_unknown=True)
    return Prediction(
        labels,
        {c: round(s, 4) for c, s in scores.items()},
        round(confidence, 4),
        evidence,
        is_unknown=False,
    )


def predict(version_type: ClassifierType, title: str, content: str, config: dict,
            threshold: float) -> Prediction:
    text = normalize_text(title, content)
    if version_type == ClassifierType.keyword:
        return predict_keyword(text, config, threshold)
    if version_type == ClassifierType.rule:
        return predict_rule(text, config or [], threshold)
    return predict_model(text, config, threshold)
