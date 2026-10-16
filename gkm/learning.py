import json
import re
import time
from .storage import dumps, uid

RECOMMENDATIONS = {"keep", "revise", "review_removal", "business_verification"}


def save_case(store, content, case_id=None):
    if not isinstance(content, dict):
        raise ValueError("案例必须为 JSON 对象")
    required = (
        "language",
        "scenario",
        "excerpt",
        "correct_recommendation",
        "reason",
        "boundaries",
        "author",
    )
    if (
        any(
            not isinstance(content.get(k), str) or not content.get(k, "").strip()
            for k in required
        )
        or content["correct_recommendation"] not in RECOMMENDATIONS
    ):
        raise ValueError("案例语言、场景、片段、结论、理由、边界和录入人均为必填")
    case_id = case_id or uid()
    with store.connect() as c:
        duplicate = c.execute(
            "SELECT id FROM cases WHERE id<>? AND json_extract(content,'$.excerpt')=? AND json_extract(content,'$.language')=? AND json_extract(content,'$.scenario')=?",
            (case_id, content["excerpt"], content["language"], content["scenario"]),
        ).fetchone()
        if duplicate:
            raise ValueError(
                "相同语言/场景/片段已有案例：" + duplicate["id"] + "；请编辑已有案例"
            )
        old = c.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        version = (old["version"] if old else 0) + 1
        # Every edit invalidates approval.
        c.execute(
            "INSERT OR REPLACE INTO cases VALUES(?,?,?,?,?,?)",
            (case_id, version, dumps(content), "pending", 0, time.time()),
        )
        c.execute(
            "INSERT INTO case_history VALUES(?,?,?,?,?)",
            (case_id, version, dumps(content), "pending", time.time()),
        )
    store.audit("case_edit", case_id, "案例已保存为待审核")
    return case_id


def approve_case(store, case_id, reviewer):
    if not reviewer.strip():
        raise ValueError("必须填写审核人")
    with store.connect() as c:
        row = c.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        if row is None:
            raise ValueError("案例不存在")
        content = json.loads(row["content"])
        content.update(reviewer=reviewer, reviewed_at=time.time())
        c.execute(
            "UPDATE cases SET content=?,status='approved',enabled=0 WHERE id=?",
            (dumps(content), case_id),
        )
        c.execute(
            "UPDATE case_history SET content=?,status='approved' WHERE case_id=? AND version=?",
            (dumps(content), case_id, row["version"]),
        )
    store.audit("case_approve", case_id, "案例人工审核通过，待固定集回归后启用")


def retrieve(cases, article, limit=4):
    def terms(text):
        words = set(re.findall(r"[a-z0-9]{2,}", text.lower()))
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]", text))
        return words | {chinese[i : i + 2] for i in range(len(chinese) - 1)}

    query = terms(article["title"] + " " + article["clean_content"])
    ranked = []
    for case in cases:
        content = case["data"]
        tokens = terms(content["excerpt"] + " " + content["scenario"])
        overlap = len(query & tokens)
        if not overlap:
            continue
        language = content["language"] == article["language"]
        scenario = content["scenario"] == article.get("category")
        score = (
            overlap / max(1, len(tokens)) + int(language) * 0.3 + int(scenario) * 0.2
        )
        ranked.append(
            (
                score,
                case.get("created_at", 0),
                {
                    "id": case["id"],
                    "version": case["version"],
                    **content,
                    "match_reason": f"文本重合 {overlap} 项；语言匹配 {language}；场景匹配 {scenario}",
                },
            )
        )
    return [
        r[2] for r in sorted(ranked, key=lambda r: (r[0], r[1]), reverse=True)[:limit]
    ]


def review(store, result_id, recommendation, reason, reviewer, make_case=False):
    if (
        recommendation not in RECOMMENDATIONS
        or not reason.strip()
        or not reviewer.strip()
    ):
        raise ValueError("请选择合法结论，并填写复核理由和复核人")
    with store.connect() as c:
        row = c.execute(
            """SELECT r.result,v.clean_content,a.language,a.category FROM analysis_results r JOIN job_items i ON i.id=r.job_item_fk JOIN article_versions v ON v.id=i.article_version_fk JOIN articles a ON a.id=v.article_fk WHERE r.id=?""",
            (result_id,),
        ).fetchone()
        if not row:
            raise ValueError("结果不存在")
        review_id = c.execute(
            "INSERT INTO reviews(result_fk,recommendation,reason,reviewer,created_at) VALUES(?,?,?,?,?)",
            (result_id, recommendation, reason, reviewer, time.time()),
        ).lastrowid
    store.audit("review", result_id, "人工复核已保存，AI 原判断保留")
    if make_case:
        existing_case = store.one(
            "SELECT id FROM cases WHERE json_extract(content,'$.excerpt')=? AND json_extract(content,'$.language')=? AND json_extract(content,'$.scenario')=?",
            (row["clean_content"], row["language"], row["category"] or "通用"),
        )
        return save_case(
            store,
            {
                "language": row["language"],
                "scenario": row["category"] or "通用",
                "excerpt": row["clean_content"],
                "correct_recommendation": recommendation,
                "reason": reason,
                "boundaries": "仅适用于相同业务上下文，禁止推断外部事实",
                "author": reviewer,
                "source_result_id": result_id,
                "original_ai": json.loads(row["result"]),
            },
            case_id=existing_case["id"] if existing_case else None,
        )
    return review_id


def save_prompt(
    store,
    system,
    business,
    output,
    rules,
    note,
    case_prompt="案例仅在相同事实与适用边界内参考",
):
    if not all((system.strip(), business.strip(), output.strip(), note.strip())):
        raise ValueError("提示词及变更说明不能为空")
    if not isinstance(rules, list) or not all(
        isinstance(r, dict) and all(r.get(k) for k in ("id", "name", "instruction"))
        for r in rules
    ):
        raise ValueError("规则须为包含 id、name、instruction 的 JSON 数组")
    return store.execute(
        "INSERT INTO prompt_versions(created_at,system,business,output,rules,note,case_prompt) VALUES(?,?,?,?,?,?,?)",
        (time.time(), system, business, output, dumps(rules), note, case_prompt),
    )


def publish_prompt(store, prompt_id, regression_report):
    # Explicit evidence required. No fabricated quality gate.
    if not (
        regression_report.get("approved")
        and regression_report.get("reviewer")
        and regression_report.get("count", 0) >= 500
        and regression_report.get("f1", 0) >= regression_report.get("threshold", 1)
        and regression_report.get("baseline_f1") is not None
        and regression_report["f1"] >= regression_report["baseline_f1"]
        and regression_report.get("prompt_id") == prompt_id
        and regression_report.get("missing", 1) == 0
        and regression_report.get("invalid_evidence_rate", 1) == 0
    ):
        raise ValueError(
            "发布需至少 500 篇固定标注集回归、达标阈值及人工批准；可保存草稿并做单篇对比"
        )
    with store.connect() as c:
        if not c.execute(
            "SELECT 1 FROM prompt_versions WHERE id=?", (prompt_id,)
        ).fetchone():
            raise ValueError("版本不存在")
        c.execute("UPDATE prompt_versions SET published=0")
        c.execute("UPDATE prompt_versions SET published=1 WHERE id=?", (prompt_id,))
    store.audit("prompt_publish", prompt_id, dumps(regression_report))


def activate_case(store, case_id, regression_report):
    case = store.one("SELECT * FROM cases WHERE id=?", (case_id,))
    if not case or case["status"] != "approved":
        raise ValueError("必须先人工审核案例")
    gate = regression_report
    matching = any(
        c.get("id") == case_id and c.get("version") == case["version"]
        for c in gate.get("case_versions", [])
    )
    if not (
        matching
        and gate.get("approved")
        and gate.get("reviewer")
        and gate.get("count", 0) >= 500
        and gate.get("missing", 1) == 0
        and gate.get("invalid_evidence_rate", 1) == 0
        and gate.get("f1", 0) >= gate.get("threshold", 1)
        and gate.get("baseline_f1") is not None
        and gate["f1"] >= gate["baseline_f1"]
    ):
        raise ValueError(
            "启用需包含此案例版本的至少 500 篇固定集前后对照回归、无缺失/无效证据、达到阈值且不低于基线，并由人员批准"
        )
    store.execute("UPDATE cases SET enabled=1 WHERE id=?", (case_id,))
    store.audit("case_activate", case_id, dumps(gate))
