"""Reproducible offline evaluation; never invent a gold dataset or quality scores."""

import hashlib
from collections import Counter
from .storage import dumps


def evaluate(gold, predictions, reviewer="", threshold=0.9):
    if not gold:
        raise ValueError("固定标注集为空")
    identity_fields = ["article_id"]
    for field in ("language", "source_scope"):
        if all(field in row for row in gold):
            identity_fields.append(field)

    def identity(row):
        return tuple(str(row.get(field, "")) for field in identity_fields)

    if len({identity(row) for row in gold}) != len(gold):
        raise ValueError(
            "标注身份重复；跨语言/来源相同 ID 请补齐 language/source_scope"
        )
    relevant = {identity(row) for row in gold}
    selected = [p for p in predictions if identity(p) in relevant]
    if len({identity(p) for p in selected}) != len(selected):
        raise ValueError("分析身份不唯一，请在固定集中明确语言和来源范围")
    allowed = {
        "confirmed_issue",
        "suspected_issue",
        "no_issue_found",
        "insufficient_information",
    }
    by_id = {identity(p): p for p in selected}
    confusion = Counter()
    language_distribution = Counter()
    tp = fp = fn = tn = missing = invalid_quotes = quotes = 0
    mismatches = []
    for row in gold:
        if row["validity_status"] not in allowed:
            raise ValueError("标注集状态非法")
        p = by_id.get(identity(row))
        if p is None:
            missing += 1
            continue
        confusion[row["validity_status"] + " → " + p["validity_status"]] += 1
        language_distribution[row.get("language", p.get("language", "unknown"))] += 1
        actual = row["validity_status"] in ("confirmed_issue", "suspected_issue")
        predicted = p["validity_status"] in ("confirmed_issue", "suspected_issue")
        tp += actual and predicted
        fp += not actual and predicted
        fn += actual and not predicted
        tn += not actual and not predicted
        invalid_quotes += len(p.get("invalid_evidence", []))
        quotes += len(p.get("evidence", [])) + len(p.get("invalid_evidence", []))
        if p["validity_status"] != row["validity_status"]:
            mismatches.append(
                {
                    "article_id": row["article_id"],
                    "gold": row["validity_status"],
                    "predicted": p["validity_status"],
                }
            )
    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
    return {
        "count": len(gold),
        "dataset_sha256": hashlib.sha256(dumps(gold).encode()).hexdigest(),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "missing": missing,
        "invalid_evidence_rate": invalid_quotes / quotes if quotes else 0,
        "mismatches": mismatches,
        "confusion_matrix": dict(confusion),
        "language_distribution": dict(language_distribution),
        "positive_definition": "confirmed_issue 或 suspected_issue 视为风险阳性",
        "threshold": threshold,
        "reviewer": reviewer,
        "approved": bool(reviewer)
        and not missing
        and not invalid_quotes
        and f1 >= threshold
        and len(gold) >= 500,
    }
