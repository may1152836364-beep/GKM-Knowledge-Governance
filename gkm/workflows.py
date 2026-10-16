"""Review/evaluation operations shared by GUI and automation."""

import json
import time
from .storage import dumps
from .learning import RECOMMENDATIONS
from .jobs import create_job, progress
from .evaluation import evaluate


def bulk_review(
    store, result_ids, recommendation, reason, reviewer, conditions_confirmed=False
):
    if not conditions_confirmed or not result_ids or len(result_ids) > 500:
        raise ValueError("批量复核必须明示筛选条件、确认所选记录且每次不超过 500 条")
    if recommendation == "review_removal":
        raise ValueError("下架评估需逐篇核对充分证据与历史用途，不能批量下架评估")
    if (
        recommendation not in RECOMMENDATIONS
        or not reason.strip()
        or not reviewer.strip()
    ):
        raise ValueError("批量复核结论、理由与人员必填")
    with store.connect() as c:
        for rid in result_ids:
            if not c.execute(
                "SELECT 1 FROM analysis_results WHERE id=?", (rid,)
            ).fetchone():
                raise ValueError("所选结果已不存在，未执行批量复核")
        c.executemany(
            "INSERT INTO reviews(result_fk,recommendation,reason,reviewer,created_at) VALUES(?,?,?,?,?)",
            [
                (rid, recommendation, reason, reviewer, time.time())
                for rid in set(result_ids)
            ],
        )
    store.audit(
        "bulk_review", "", f"已按明确条件复核 {len(set(result_ids))} 条，原 AI 结论保留"
    )


def compare_prompts(
    store,
    engine,
    profile_id,
    article_ids,
    prompt_a,
    prompt_b,
    budget,
    consent,
    candidate_case_ids=None,
):
    if not 1 <= len(article_ids) <= 10:
        raise ValueError("单次 A/B 对比请选择 1–10 篇文章")
    # Preparation is atomic at job level; jobs never silently start without consent.
    jobs = [
        create_job(
            store,
            profile_id,
            budget,
            ids=article_ids,
            prompt_id=p,
            consent=consent,
            candidate_case_ids=candidate_case_ids if index else None,
            evaluation_only=True,
        )
        for index, p in enumerate((prompt_a, prompt_b))
    ]
    for job in jobs:
        engine.start(job)
        engine.threads[job].join()
    output = []
    for job in jobs:
        output.append(
            {
                "job": job,
                "progress": progress(store, job),
                "results": [
                    json.loads(r["result"])
                    for r in store.rows(
                        "SELECT r.result FROM analysis_results r JOIN job_items i ON i.id=r.job_item_fk WHERE i.job_fk=?",
                        (job,),
                    )
                ],
                "errors": store.rows(
                    "SELECT id,error FROM job_items WHERE job_fk=? AND error IS NOT NULL",
                    (job,),
                ),
            }
        )
    return output


def evaluate_job(store, job, gold, reviewer, threshold, baseline_job=None):
    results = [
        json.loads(r["result"])
        for r in store.rows(
            "SELECT result FROM analysis_results r JOIN job_items i ON i.id=r.job_item_fk WHERE i.job_fk=?",
            (job,),
        )
    ]
    report = evaluate(gold, results, reviewer, threshold)
    row = store.one("SELECT snapshot FROM analysis_jobs WHERE id=?", (job,))
    if not row:
        raise ValueError("任务不存在")
    snapshot = json.loads(row["snapshot"])
    report["job_id"] = job
    report["prompt_id"] = snapshot["prompt"]["id"]
    report["case_versions"] = [
        {"id": c["id"], "version": c["version"]} for c in snapshot["cases"]
    ]
    if baseline_job:
        baseline = [
            json.loads(r["result"])
            for r in store.rows(
                "SELECT result FROM analysis_results r JOIN job_items i ON i.id=r.job_item_fk WHERE i.job_fk=?",
                (baseline_job,),
            )
        ]
        base_report = evaluate(gold, baseline, reviewer, threshold)
        report["baseline_job"] = baseline_job
        report["baseline_f1"] = base_report["f1"]
        report["approved"] = (
            report["approved"]
            and not base_report["missing"]
            and report["f1"] >= base_report["f1"]
        )
    store.audit("evaluation", job, dumps(report))
    return report
