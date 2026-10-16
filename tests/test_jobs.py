import json
import threading
import httpx
import pytest
from gkm.storage import Store
from gkm.ai import save_profile, test_connection as connect_test
from gkm.jobs import Engine, create_job, progress
from .helpers import MemoryVault, seed, transport


def prepare(tmp_path, count=6, options=None):
    db = Store(tmp_path)
    vault = MemoryVault()
    seed(db, count)
    opts = {
        "timeout": 1,
        "context_chars": 24000,
        "max_output_tokens": 1024,
        "input_price": 1,
        "output_price": 1,
        "pricing_confirmed": True,
        **(options or {}),
    }
    pid = save_profile(
        db, vault, "Mock", "https://approved.example/v1", "mock", "secret", opts
    )
    assert connect_test(db, vault, pid, transport())["ok"]
    return db, vault, pid


def wait(engine, job):
    engine.threads[job].join(15)
    assert not engine.threads[job].is_alive()


def test_full_and_restart(tmp_path):
    db, vault, pid = prepare(tmp_path)
    job = create_job(db, pid, 10, consent=True, rpm=1000)
    engine = Engine(db, vault, transport())
    engine.start(job)
    wait(engine, job)
    assert progress(db, job)["succeeded"] == 6
    before = db.one("SELECT requests FROM analysis_jobs")["requests"]
    engine.recover()
    assert progress(db, job)["succeeded"] == 6
    assert db.one("SELECT requests FROM analysis_jobs")["requests"] == before
    assert db.one("SELECT count(*) n FROM analysis_results")["n"] == 6


def test_pause_resume_cancel(tmp_path):
    db, vault, pid = prepare(tmp_path, 100)
    entered = threading.Event()
    release = threading.Event()
    calls = []
    base = transport()

    def slow(req):
        calls.append(1)
        entered.set()
        release.wait(3)
        return base.handle_request(req)

    job = create_job(db, pid, 10, concurrency=2, rpm=1000, tpm=10000000, consent=True)
    engine = Engine(db, vault, transport(slow))
    engine.start(job)
    assert entered.wait(2)
    engine.pause(job)
    assert db.one("SELECT state FROM analysis_jobs")["state"] == "pausing"
    # Requests registered at the dispatch boundary include requests currently
    # connecting. Arrival at the mock server is not the dispatch timestamp.
    count = db.one("SELECT requests FROM analysis_jobs")["requests"]
    release.set()
    wait(engine, job)
    assert (
        db.one("SELECT state FROM analysis_jobs")["state"] == "paused"
        and len(calls) == count
        and db.one("SELECT requests FROM analysis_jobs")["requests"] == count
    )
    done = progress(db, job).get("succeeded", 0)
    engine.start(job)
    wait(engine, job)
    assert progress(db, job)["succeeded"] == 100 and done > 0
    assert db.one("SELECT count(*) n FROM analysis_results")["n"] == 100
    job2 = create_job(db, pid, 10, consent=True)
    engine.cancel(job2)
    assert progress(db, job2)["cancelled"] == 100


def test_fatal_budget_retry_recovery(tmp_path):
    db, vault, pid = prepare(tmp_path)
    job = create_job(db, pid, 10, concurrency=1, consent=True)
    engine = Engine(db, vault, transport(lambda req: httpx.Response(401)))
    engine.start(job)
    wait(engine, job)
    assert db.one("SELECT state FROM analysis_jobs")["state"] == "paused"
    assert progress(db, job)["failed"] == 1
    assert "401" in db.one("SELECT error FROM analysis_jobs")["error"]
    engine.retry_failed(job)
    engine.transport = transport()
    engine.start(job)
    wait(engine, job)
    assert progress(db, job)["succeeded"] == 6
    job2 = create_job(db, pid, 0.000001, consent=True)
    engine.start(job2)
    wait(engine, job2)
    assert (
        db.one("SELECT state,error FROM analysis_jobs WHERE id=?", (job2,))["state"]
        == "paused"
    )
    assert progress(db, job2).get("succeeded", 0) == 0
    db.execute("UPDATE job_items SET state='inflight' WHERE job_fk=?", (job2,))
    db.execute("UPDATE analysis_jobs SET state='running' WHERE id=?", (job2,))
    engine.recover()
    assert progress(db, job2)["pending"] == 6


def test_consent_and_connection_required(tmp_path):
    db, vault, pid = prepare(tmp_path)
    with pytest.raises(ValueError):
        create_job(db, pid, 10)
    db.execute("UPDATE model_profiles SET tested_hash=NULL")
    with pytest.raises(ValueError):
        create_job(db, pid, 10, consent=True)


def test_pause_stops_next_chunk_and_reuses_completed_chunk(tmp_path):
    db, vault, pid = prepare(tmp_path, 1, {"context_chars": 18000})
    db.execute(
        "UPDATE article_versions SET clean_content=?",
        (
            "\n".join(
                "正文段落" + str(i) + "：历史维修知识不得仅因型号旧而下架。" * 120
                for i in range(20)
            ),
        ),
    )
    job = create_job(db, pid, 10, concurrency=1, rpm=10000, tpm=10000000, consent=True)
    entered = threading.Event()
    release = threading.Event()
    calls = []
    base = transport()

    def slow(req):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            release.wait(3)
        return base.handle_request(req)

    engine = Engine(db, vault, transport(slow))
    engine.start(job)
    assert entered.wait(2)
    engine.pause(job)
    release.set()
    wait(engine, job)
    assert len(calls) == 1 and db.one("SELECT count(*) n FROM chunk_cache")["n"] == 1
    engine.transport = transport()
    engine.start(job)
    wait(engine, job)
    assert progress(db, job).get("succeeded") == 1
    assert db.one("SELECT requests FROM analysis_jobs")["requests"] > 1


def test_transient_retry_and_single_permanent_error(tmp_path):
    db, vault, pid = prepare(tmp_path, 3)
    calls = []
    base = transport()

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return base.handle_request(req)

    job = create_job(db, pid, 10, concurrency=1, rpm=10000, tpm=10000000, consent=True)
    e = Engine(db, vault, transport(handler))
    e.start(job)
    wait(e, job)
    assert progress(db, job)["succeeded"] == 3 and len(calls) == 4
    attempts = db.one(
        "SELECT attempts FROM job_items WHERE job_fk=? ORDER BY id LIMIT 1", (job,)
    )["attempts"]
    assert attempts == 2


def test_cancel_restart_finishes_cancellation(tmp_path):
    db, vault, pid = prepare(tmp_path, 10)
    job = create_job(db, pid, 10, consent=True)
    db.execute("UPDATE analysis_jobs SET state='cancelling' WHERE id=?", (job,))
    db.execute(
        "UPDATE job_items SET state='inflight' WHERE job_fk=? AND id%2=0", (job,)
    )
    Engine(db, vault, transport()).recover()
    assert (
        progress(db, job)["cancelled"] == 10
        and db.one("SELECT state FROM analysis_jobs")["state"] == "cancelled"
    )


def test_fix_credentials_on_paused_frozen_job(tmp_path):
    from gkm.jobs import refresh_credentials

    db, vault, pid = prepare(tmp_path, 1)
    job = create_job(db, pid, 10, consent=True)
    old = json.loads(db.one("SELECT snapshot FROM analysis_jobs")["snapshot"])
    profile = db.one("SELECT * FROM model_profiles")
    save_profile(
        db,
        vault,
        "Mock",
        profile["base_url"],
        profile["model"],
        "replacement-secret",
        json.loads(profile["options"]),
        pid,
    )
    assert connect_test(db, vault, pid, transport())["ok"]
    refresh_credentials(db, vault, job)
    new = json.loads(db.one("SELECT snapshot FROM analysis_jobs")["snapshot"])
    assert (
        new["profile"]["key_ref"] != old["profile"]["key_ref"]
        and new["prompt"] == old["prompt"]
        and new["cases"] == old["cases"]
    )


def test_single_call_budget_limit(tmp_path):
    db, vault, pid = prepare(tmp_path, 2)
    job = create_job(db, pid, 100, consent=True, max_call_cost=0.000001, concurrency=1)
    e = Engine(db, vault, transport())
    e.start(job)
    wait(e, job)
    row = db.one("SELECT * FROM analysis_jobs WHERE id=?", (job,))
    assert (
        row["state"] == "paused"
        and "CALL_BUDGET_LIMIT" in row["error"]
        and row["requests"] == 0
    )


def test_imported_long_article_does_not_duplicate_body_in_metadata(tmp_path):
    from .test_data import fixture
    from gkm.excel import import_xlsx, preview
    from gkm.storage import Store

    db = Store(tmp_path / "db")
    vault = MemoryVault()
    path = tmp_path / "long.xlsx"
    fixture(
        path,
        "\n".join(
            ("第" + str(i) + "节：维修流程仍有历史用途，不能仅因型号较旧而下架。") * 80
            for i in range(12)
        ),
    )
    import_xlsx(db, path, preview(path)["mapping"])
    pid = save_profile(
        db,
        vault,
        "mock",
        "https://approved.example/v1",
        "m",
        "k",
        {
            "context_chars": 18000,
            "timeout": 1,
            "max_output_tokens": 2048,
            "input_price": 0,
            "output_price": 0,
            "pricing_confirmed": True,
        },
    )
    assert connect_test(db, vault, pid, transport())["ok"]
    original_metadata = db.article_version(1)["metadata_json"]
    assert "content(文章正文)" in original_metadata
    job = create_job(
        db, pid, 10, ids=["00001"], concurrency=1, rpm=10000, tpm=10000000, consent=True
    )
    e = Engine(db, vault, transport())
    e.start(job)
    wait(e, job)
    assert progress(db, job).get("succeeded") == 1, db.rows(
        "SELECT error FROM job_items"
    )
    value = json.loads(db.one("SELECT result FROM analysis_results")["result"])
    assert value["segment_count"] > 1
    assert db.article_version(1)["metadata_json"] == original_metadata


def test_custom_body_mapping_preserves_unrelated_metadata(tmp_path):
    from openpyxl import Workbook
    from gkm.excel import import_xlsx
    from gkm.storage import Store

    db = Store(tmp_path / "db")
    vault = MemoryVault()
    path = tmp_path / "custom.xlsx"
    w = Workbook()
    s = w.active
    s.append(["编号", "标题", "完整业务文档", "语言", "content"])
    s.append(
        [
            "001",
            "业务说明",
            "历史维修流程仍有用途。请先联系服务团队核实当前业务适用范围，不得直接下架。",
            "zh",
            "不应丢弃的辅助说明",
        ]
    )
    w.save(path)
    import_xlsx(
        db,
        path,
        {
            "article_id": "编号",
            "title": "标题",
            "raw_content": "完整业务文档",
            "language": "语言",
        },
    )
    pid = save_profile(
        db,
        vault,
        "mock",
        "https://approved.example/v1",
        "m",
        "k",
        {
            "context_chars": 18000,
            "timeout": 1,
            "max_output_tokens": 2048,
            "input_price": 0,
            "output_price": 0,
            "pricing_confirmed": True,
        },
    )
    assert connect_test(db, vault, pid, transport())["ok"]
    base = transport()
    seen = []

    def capture(req):
        data = json.loads(json.loads(req.content)["messages"][-1]["content"])
        seen.append(data["metadata"])
        return base.handle_request(req)

    job = create_job(db, pid, 1, consent=True)
    e = Engine(db, vault, transport(capture))
    e.start(job)
    wait(e, job)
    assert progress(db, job)["succeeded"] == 1
    assert seen[0]["content"] == "不应丢弃的辅助说明" and "完整业务文档" not in seen[0]
