import json
import pytest
from openpyxl import load_workbook
from gkm.learning import (
    save_case,
    approve_case,
    activate_case,
    review,
    save_prompt,
    publish_prompt,
    retrieve,
)
from gkm.jobs import Engine, create_job
from gkm.excel import export_results
from gkm.evaluation import evaluate
from .helpers import transport
from .test_jobs import prepare, wait


def case():
    return {
        "language": "zh",
        "scenario": "活动",
        "excerpt": "活动于2020年结束。历史订单履约仍有价值。",
        "correct_recommendation": "keep",
        "reason": "历史用途",
        "boundaries": "仅相同业务",
        "author": "运营",
    }


def test_review_case_snapshot_export_backup(tmp_path):
    db, vault, pid = prepare(tmp_path, 2)
    cid = save_case(db, case())
    assert db.one("SELECT enabled FROM cases")["enabled"] == 0
    create_job(db, pid, 10, consent=True)
    assert not json.loads(db.one("SELECT snapshot FROM analysis_jobs")["snapshot"])[
        "cases"
    ]
    approve_case(db, cid, "审核员")
    assert not db.one("SELECT enabled FROM cases WHERE id=?", (cid,))["enabled"]
    with pytest.raises(ValueError):
        activate_case(db, cid, {})
    activate_case(
        db,
        cid,
        {
            "approved": True,
            "count": 500,
            "reviewer": "测试人员",
            "missing": 0,
            "invalid_evidence_rate": 0,
            "f1": 1,
            "threshold": 0.9,
            "baseline_f1": 1,
            "case_versions": [{"id": cid, "version": 1}],
        },
    )
    second = create_job(db, pid, 10, consent=True)
    snap = json.loads(
        db.one("SELECT snapshot FROM analysis_jobs WHERE id=?", (second,))["snapshot"]
    )
    assert len(snap["cases"]) == 1 and retrieve(snap["cases"], db.article_version(1))
    save_case(db, {**case(), "reason": "修订理由"}, cid)
    assert db.one("SELECT status FROM cases")["status"] == "pending"
    assert len(snap["cases"]) == 1 and snap["cases"][0]["version"] == 1
    e = Engine(db, vault, transport())
    e.start(second)
    wait(e, second)
    row = db.one("SELECT * FROM analysis_results LIMIT 1")
    original = row["result"]
    new_case = review(db, row["id"], "keep", "仍有售后用途", "复核员", True)
    assert (
        db.one("SELECT status FROM cases WHERE id=?", (new_case,))["status"]
        == "pending"
    )
    assert (
        db.one("SELECT result FROM analysis_results WHERE id=?", (row["id"],))["result"]
        == original
    )
    db.execute("UPDATE article_versions SET title='=HYPERLINK(\"bad\")' WHERE id=1")
    path = tmp_path / "results.xlsx"
    export_results(db, path)
    w = load_workbook(path)
    rows = list(w.active.values)
    w.close()
    assert len(rows) == 3 and rows[1][1].startswith("'=")
    db.backup(tmp_path / "copy.sqlite3")
    db.execute("DELETE FROM reviews")
    db.restore(tmp_path / "copy.sqlite3")
    assert db.one("SELECT count(*) n FROM reviews")["n"] == 1


def test_prompt_gate_and_evaluation(tmp_path):
    db, _, _ = prepare(tmp_path)
    p = save_prompt(db, "系统", "业务", "输出", [], "修订")
    with pytest.raises(ValueError):
        publish_prompt(db, p, {"approved": True})
    gold = [
        {"article_id": str(i), "validity_status": "confirmed_issue"} for i in range(500)
    ]
    pred = [{**r, "evidence": []} for r in gold]
    report = evaluate(gold, pred, "审核员", 0.9)
    assert report["approved"] and report["f1"] == 1
    report.update(baseline_f1=1, prompt_id=p)
    publish_prompt(db, p, report)
    assert db.one("SELECT id FROM prompt_versions WHERE published=1")["id"] == p
    assert not evaluate(gold, [], "审核员")["approved"]


def test_bulk_review_and_ab_real_orchestration(tmp_path):
    from gkm.workflows import bulk_review, compare_prompts, evaluate_job

    db, vault, pid = prepare(tmp_path, 2)
    e = Engine(db, vault, transport())
    prompt = db.one("SELECT id FROM prompt_versions")["id"]
    outputs = compare_prompts(db, e, pid, ["0", "1"], prompt, prompt, 10, True)
    assert len(outputs) == 2 and all(x["progress"]["succeeded"] == 2 for x in outputs)
    ids = [r["id"] for r in db.rows("SELECT id FROM analysis_results")]
    with pytest.raises(ValueError):
        bulk_review(db, ids, "keep", "统一理由", "人员")
    with pytest.raises(ValueError):
        bulk_review(db, ids, "review_removal", "统一理由", "人员", True)
    bulk_review(db, ids, "keep", "已核实售后用途", "人员", True)
    assert db.one("SELECT count(*) n FROM reviews")["n"] == 4
    gold = [
        {"article_id": "0", "validity_status": "suspected_issue"},
        {"article_id": "1", "validity_status": "suspected_issue"},
    ]
    report = evaluate_job(db, outputs[0]["job"], gold, "人员", 0.9)
    assert (
        report["count"] == 2
        and not report["approved"]
        and report["prompt_id"] == prompt
    )


def test_candidate_cases_only_in_evaluation_and_do_not_skip_production(tmp_path):
    db, vault, pid = prepare(tmp_path, 1)
    cid = save_case(db, case())
    approve_case(db, cid, "审核员")
    job = create_job(db, pid, 10, consent=True, candidate_case_ids=[cid])
    snapshot = json.loads(
        db.one("SELECT snapshot FROM analysis_jobs WHERE id=?", (job,))["snapshot"]
    )
    assert snapshot["evaluation_only"] and snapshot["cases"][0]["id"] == cid
    e = Engine(db, vault, transport())
    e.start(job)
    wait(e, job)
    production = create_job(db, pid, 10, consent=True, changed=True)
    snapshot = json.loads(
        db.one("SELECT snapshot FROM analysis_jobs WHERE id=?", (production,))[
            "snapshot"
        ]
    )
    assert not snapshot["evaluation_only"] and not snapshot["cases"]


def test_evaluation_does_not_merge_bilingual_ids():
    gold = [
        {"article_id": "01", "language": "zh", "validity_status": "confirmed_issue"},
        {"article_id": "01", "language": "en", "validity_status": "no_issue_found"},
    ]
    report = evaluate(gold, gold, "审核员")
    assert (
        report["tp"] == 1
        and report["tn"] == 1
        and report["language_distribution"] == {"zh": 1, "en": 1}
    )
    with pytest.raises(ValueError):
        evaluate([{"article_id": "01", "validity_status": "confirmed_issue"}], gold)


def test_duplicate_cases_cannot_bias_retrieval(tmp_path):
    db, _, _ = prepare(tmp_path, 1)
    cid = save_case(db, case())
    with pytest.raises(ValueError):
        save_case(db, case())
    save_case(db, {**case(), "reason": "修订理由"}, cid)
    assert db.one("SELECT version FROM cases")["version"] == 2


def test_review_to_case_preserves_single_lineage(tmp_path):
    db, vault, pid = prepare(tmp_path, 1)
    job = create_job(db, pid, 10, consent=True)
    e = Engine(db, vault, transport())
    e.start(job)
    wait(e, job)
    rid = db.one("SELECT id FROM analysis_results")["id"]
    first = review(db, rid, "keep", "售后仍需保留", "甲", True)
    second = review(db, rid, "revise", "修正适用范围", "乙", True)
    assert first == second and db.one("SELECT version FROM cases")["version"] == 2
    assert db.one("SELECT count(*) n FROM case_history")["n"] == 2
    assert db.one("SELECT status FROM cases")["status"] == "pending"
