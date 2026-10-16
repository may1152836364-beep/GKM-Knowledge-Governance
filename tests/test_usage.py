import httpx
from gkm.jobs import Engine, create_job
from .helpers import transport
from .test_jobs import prepare, wait


def test_invalid_json_still_charged_and_diagnosed(tmp_path):
    db, vault, pid = prepare(tmp_path, 1)

    def malformed(req):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "not-json"}}],
                "usage": {"prompt_tokens": 111, "completion_tokens": 22},
            },
        )

    job = create_job(db, pid, 1, consent=True, retries=0)
    e = Engine(db, vault, transport(malformed))
    e.start(job)
    wait(e, job)
    row = db.one("SELECT * FROM analysis_jobs")
    assert (
        row["requests"] == 1
        and row["input_tokens"] == 111
        and row["output_tokens"] == 22
    )
    assert row["spent"] > 0 and row["state"] == "completed_with_errors"
    assert db.one("SELECT kind FROM call_logs")["kind"] == "error:INVALID_RESPONSE"
    assert db.one("SELECT count(*) n FROM analysis_results")["n"] == 0


def test_missing_usage_stops_queue(tmp_path):
    db, vault, pid = prepare(tmp_path, 3)

    def missing(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    job = create_job(db, pid, 1, consent=True, concurrency=1)
    e = Engine(db, vault, transport(missing))
    e.start(job)
    wait(e, job)
    row = db.one("SELECT * FROM analysis_jobs")
    assert (
        row["state"] == "paused"
        and row["requests"] == 1
        and "MISSING_USAGE" in row["error"]
    )
