"""Streaming XLSX and real queue benchmark with local mock HTTP transport only."""

import argparse
import resource
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openpyxl import Workbook
from gkm.storage import Store, dumps
from gkm.excel import import_xlsx, preview
from gkm.jobs import create_job, Engine, progress
from gkm.ai import save_profile, test_connection
from tests.helpers import MemoryVault, transport


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--count", type=int, default=200000)
    p.add_argument("--engine-items", type=int, default=1000)
    p.add_argument("--output", default="docs/PERFORMANCE.json")
    args = p.parse_args()
    with tempfile.TemporaryDirectory(prefix="gkm-stress-") as directory:
        path = Path(directory) / "synthetic.xlsx"
        w = Workbook(write_only=True)
        s = w.create_sheet("知识")
        s.append(
            [
                "Article ID(文章ID)",
                "title(文章标题)",
                "content(文章正文)",
                "Language(语言)",
                "Release Status(发布状态)",
            ]
        )
        started = time.perf_counter()
        for i in range(args.count):
            s.append(
                [
                    str(i).zfill(8),
                    f"维修服务说明 {i}",
                    (
                        "旧型号维修仍有售后价值。请按照原文核实流程，不得假设已经停服。"
                        * 20
                    )
                    if i % 2 == 0
                    else (
                        "Repair support remains useful for owners. Verify the current process with the business team. "
                        * 10
                    ),
                    "zh" if i % 2 == 0 else "en",
                    "已发布" if i % 3 else "已下架",
                ]
            )
        w.save(path)
        generated = time.perf_counter() - started
        db = Store(Path(directory) / "data")
        mapping = preview(path)["mapping"]
        started = time.perf_counter()
        report = import_xlsx(db, path, mapping)
        import_seconds = time.perf_counter() - started
        assert (
            report["inserted"] == args.count
            and db.one("SELECT count(*) n FROM articles")["n"] == args.count
        )
        times = []
        for i in range(100):
            start = time.perf_counter()
            db.articles(query="维修服务说明 " + str(i * 997 % args.count), page=i % 3)
            times.append(time.perf_counter() - start)
        vault = MemoryVault()
        pid = save_profile(
            db,
            vault,
            "SIMULATED",
            "https://mock.example/v1",
            "simulated",
            "in-memory-only",
            {
                "timeout": 1,
                "context_chars": 24000,
                "max_output_tokens": 1024,
                "input_price": 0,
                "output_price": 0,
                "pricing_confirmed": True,
            },
        )
        assert test_connection(db, vault, pid, transport())["ok"]
        start = time.perf_counter()
        job = create_job(
            db, pid, 10, consent=True, concurrency=5, rpm=10000000, tpm=100000000000
        )
        queue_seconds = time.perf_counter() - start
        assert progress(db, job)["total"] == args.count
        # Fault injection at scale: only incomplete in-flight items are reset.
        db.execute(
            "UPDATE job_items SET state='inflight' WHERE job_fk=? AND id%7=0", (job,)
        )
        db.execute("UPDATE analysis_jobs SET state='running' WHERE id=?", (job,))
        engine = Engine(db, vault, transport())
        start = time.perf_counter()
        engine.recover()
        recovery_seconds = time.perf_counter() - start
        assert progress(db, job)["pending"] == args.count
        # Real workers execute N selected items; full queue remains frozen separately.
        ids = [str(i).zfill(8) for i in range(min(args.count, args.engine_items))]
        run = create_job(
            db,
            pid,
            10,
            ids=ids,
            consent=True,
            concurrency=5,
            rpm=10000000,
            tpm=100000000000,
        )
        start = time.perf_counter()
        engine.start(run)
        engine.threads[run].join()
        engine_seconds = time.perf_counter() - start
        counts = progress(db, run)
        assert counts.get("succeeded") == len(ids), counts
        elapsed = sorted(times)
        result = {
            "platform": sys.platform,
            "data": "synthetic bilingual XLSX, not real company sample",
            "count": args.count,
            "generation_seconds": generated,
            "import_seconds": import_seconds,
            "query_samples": len(times),
            "query_p95_seconds": elapsed[int(len(elapsed) * 0.95) - 1],
            "queue_creation_seconds": queue_seconds,
            "recovery_seconds": recovery_seconds,
            "queue_items_registered_and_recovered": args.count,
            "actual_worker_mock_analyzed": len(ids),
            "actual_worker_seconds": engine_seconds,
            "actual_worker_progress": counts,
            "real_model_calls": 0,
            "database_bytes": db.path.stat().st_size,
            "max_rss_kib_linux": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "all_requested_items_mock_analyzed": len(ids) == args.count,
            "notes": [
                "Linux measurements; target Windows hardware still needs verification",
                "Memory peak includes XLSX generation and import",
                "Mock outputs test orchestration, not business judgement quality",
            ],
        }
        Path(args.output).write_text(dumps(result), encoding="utf-8")
        print(dumps(result))


if __name__ == "__main__":
    main()
