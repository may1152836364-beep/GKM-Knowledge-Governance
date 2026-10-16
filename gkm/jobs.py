"""Bounded queue, persistent state machine and explicit request boundaries."""

import datetime as dt
import json
import random
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from .ai import AIError, Client, analyze, fingerprint
from .storage import dumps, uid


class Interrupted(Exception):
    pass


def estimate(store, profile_id, batch=None):
    where = "v.parse_status='ready'"
    params = []
    if batch:
        where += " AND v.import_batch_id=?"
        params.append(batch)
    row = store.one(
        "SELECT count(*) n,coalesce(sum(length(v.clean_content)+length(v.title)),0) chars FROM articles a JOIN article_versions v ON v.id=a.current_version WHERE "
        + where,
        params,
    )
    profile = store.one("SELECT options FROM model_profiles WHERE id=?", (profile_id,))
    options = json.loads(profile["options"])
    # Deliberately rough, not a promise; long texts and prompt/cases add calls.
    return {
        **row,
        "estimated_input_tokens": row["chars"],
        "estimated_cost": (
            row["chars"] * float(options.get("input_price", 0))
            + row["n"]
            * int(options.get("max_output_tokens", 2048))
            * float(options.get("output_price", 0))
        )
        / 1_000_000,
        "warning": "字符近似估算，不含全部提示词/分段汇总；价格为用户配置，非供应商报价",
    }


def create_job(
    store,
    profile_id,
    budget,
    concurrency=3,
    rpm=60,
    tpm=200000,
    retries=3,
    batch=None,
    ids=None,
    changed=False,
    consent=False,
    prompt_id=None,
    candidate_case_ids=None,
    max_call_cost=None,
    evaluation_only=False,
):
    if not consent:
        raise ValueError(
            "请先确认只向所选企业批准的 API 发送标题、正文、元数据和审核案例"
        )
    if not 1 <= concurrency <= 10 or rpm < 1 or tpm < 1 or retries < 0 or budget <= 0:
        raise ValueError("并发 1–10，RPM/TPM/预算必须为正数，重试次数不能为负")
    if max_call_cost is not None and max_call_cost <= 0:
        raise ValueError("单次调用费用上限必须为正数")
    profile = store.one("SELECT * FROM model_profiles WHERE id=?", (profile_id,))
    if not profile or profile["tested_hash"] != fingerprint(profile):
        raise ValueError("模型配置尚未通过真实在线 JSON 测试，请先测试")
    if not json.loads(profile["options"]).get("pricing_confirmed"):
        raise ValueError("须明确填写并确认模型输入/输出价格；免费模型也需确认 0 价格")
    prompt = (
        store.one("SELECT * FROM prompt_versions WHERE id=?", (prompt_id,))
        if prompt_id
        else store.one(
            "SELECT * FROM prompt_versions WHERE published=1 ORDER BY id DESC LIMIT 1"
        )
    )
    if not prompt:
        raise ValueError("提示词版本不存在")
    cases = [
        {
            "id": r["id"],
            "version": r["version"],
            "created_at": r["created_at"],
            "data": json.loads(r["content"]),
        }
        for r in store.rows("SELECT * FROM cases WHERE status='approved' AND enabled=1")
    ]
    snapshot = {
        "evaluation_only": bool(evaluation_only or not prompt["published"]),
        "profile": profile,
        "prompt": prompt,
        "cases": cases,
        "analysis_date": dt.date.today().isoformat(),
        "concurrency": concurrency,
        "rpm": rpm,
        "tpm": tpm,
        "retries": retries,
        "consent_at": time.time(),
        "max_call_cost": max_call_cost if max_call_cost is not None else budget,
        "scope": {"batch": batch, "ids": ids, "changed": changed},
    }
    if candidate_case_ids:
        for cid in candidate_case_ids:
            row = store.one(
                "SELECT * FROM cases WHERE id=? AND status='approved'", (cid,)
            )
            if not row:
                raise ValueError("对照候选案例必须已人工审核：" + cid)
            if not any(case["id"] == cid for case in cases):
                cases.append(
                    {
                        "id": row["id"],
                        "version": row["version"],
                        "created_at": row["created_at"],
                        "data": json.loads(row["content"]),
                    }
                )
        snapshot["cases"] = cases
        snapshot["evaluation_only"] = True
    job = uid()
    with store.connect() as c:
        c.execute(
            "INSERT INTO analysis_jobs(id,created_at,state,snapshot,budget) VALUES(?,?,?,?,?)",
            (job, time.time(), "queued", dumps(snapshot), budget),
        )
        where = "v.parse_status='ready'"
        params = [job]
        if batch:
            where += " AND v.import_batch_id=?"
            params.append(batch)
        if ids:
            c.execute(
                "CREATE TEMP TABLE IF NOT EXISTS selected_ids(id TEXT PRIMARY KEY)"
            )
            c.execute("DELETE FROM selected_ids")
            c.executemany(
                "INSERT OR IGNORE INTO selected_ids VALUES(?)", [(str(i),) for i in ids]
            )
            where += " AND a.article_id IN (SELECT id FROM selected_ids)"
        if changed:
            where += " AND NOT EXISTS(SELECT 1 FROM job_items old JOIN analysis_results r ON r.job_item_fk=old.id JOIN analysis_jobs oj ON oj.id=old.job_fk WHERE old.article_version_fk=v.id AND coalesce(json_extract(oj.snapshot,'$.evaluation_only'),0)=0)"
        c.execute(
            "INSERT INTO job_items(job_fk,article_version_fk,state) SELECT ?,v.id,'pending' FROM articles a JOIN article_versions v ON v.id=a.current_version WHERE "
            + where,
            params,
        )
        if not c.execute("SELECT 1 FROM job_items WHERE job_fk=?", (job,)).fetchone():
            raise ValueError("当前范围没有可分析文章；标题/正文不足的记录请先处理")
    store.audit(
        "job_create", job, "已冻结模型、提示词、规则、案例和文章版本；不按风险初筛"
    )
    return job


def progress(store, job):
    counts = {
        r["state"]: r["n"]
        for r in store.rows(
            "SELECT state,count(*) n FROM job_items WHERE job_fk=? GROUP BY state",
            (job,),
        )
    }
    return {"total": sum(counts.values()), **counts}


class Engine:
    def __init__(self, store, vault, transport=None):
        self.store = store
        self.vault = vault
        self.transport = transport
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.threads = {}
        self.reserved = {}
        self.rate = {}
        self.rate_tokens = {}

    def recover(self):
        # Called once only after acquiring the application's single-instance lock.
        with self.store.connect() as c:
            c.execute(
                "UPDATE job_items SET state='cancelled' WHERE state IN ('pending','retry','inflight') AND job_fk IN (SELECT id FROM analysis_jobs WHERE state='cancelling')"
            )
            c.execute(
                "UPDATE analysis_jobs SET state='cancelled' WHERE state='cancelling'"
            )
            c.execute("UPDATE job_items SET state='pending' WHERE state='inflight'")
            c.execute(
                "UPDATE analysis_jobs SET state='paused',error=? WHERE state IN ('running','pausing','cancelling')",
                (
                    dumps(
                        {
                            "code": "RECOVERED",
                            "message": "上次非正常退出，未提交的在途请求可能已经计费；请核查后继续",
                        }
                    ),
                ),
            )
        self.store.audit(
            "recover",
            "",
            "已成功提交项不重复派发；未提交的外部请求无法保证供应商 exactly-once",
        )

    def start(self, job):
        with self.lock:
            if job in self.threads and self.threads[job].is_alive():
                raise ValueError("任务仍在运行或暂停收尾中")
            row = self.store.one("SELECT state FROM analysis_jobs WHERE id=?", (job,))
            if not row or row["state"] not in (
                "queued",
                "paused",
                "completed_with_errors",
            ):
                raise ValueError("当前状态不可启动")
            self.store.execute(
                "UPDATE analysis_jobs SET state='running',error=NULL WHERE id=?", (job,)
            )
            thread = threading.Thread(target=self._run, args=(job,), daemon=True)
            self.threads[job] = thread
            thread.start()

    def pause(self, job):
        with self.lock:
            self.store.execute(
                "UPDATE analysis_jobs SET state='pausing' WHERE id=? AND state='running'",
                (job,),
            )

    def cancel(self, job):
        with self.lock:
            self.store.execute(
                "UPDATE analysis_jobs SET state='cancelling' WHERE id=? AND state IN ('running','pausing')",
                (job,),
            )
            state = self.store.one(
                "SELECT state FROM analysis_jobs WHERE id=?", (job,)
            )["state"]
            if state in ("queued", "paused", "completed_with_errors"):
                with self.store.connect() as c:
                    c.execute(
                        "UPDATE job_items SET state='cancelled' WHERE job_fk=? AND state IN ('pending','retry','failed')",
                        (job,),
                    )
                    c.execute(
                        "UPDATE analysis_jobs SET state='cancelled' WHERE id=?", (job,)
                    )

    def retry_failed(self, job):
        with self.lock, self.store.connect() as c:
            state = c.execute(
                "SELECT state FROM analysis_jobs WHERE id=?", (job,)
            ).fetchone()["state"]
            if state not in ("paused", "completed_with_errors", "completed"):
                raise ValueError("请先暂停并等待在途完成")
            c.execute(
                "UPDATE job_items SET state='pending',attempts=0,error=NULL,retry_at=0 WHERE job_fk=? AND state='failed'",
                (job,),
            )
            c.execute("UPDATE analysis_jobs SET state='paused' WHERE id=?", (job,))

    def _state(self, job):
        return self.store.one("SELECT state FROM analysis_jobs WHERE id=?", (job,))[
            "state"
        ]

    def _pause_error(self, job, info):
        self.store.execute(
            "UPDATE analysis_jobs SET state='pausing',error=? WHERE id=? AND state='running'",
            (dumps(info), job),
        )

    def _run(self, job):
        try:
            snapshot = json.loads(
                self.store.one("SELECT snapshot FROM analysis_jobs WHERE id=?", (job,))[
                    "snapshot"
                ]
            )
            with ThreadPoolExecutor(max_workers=snapshot["concurrency"]) as pool:
                futures = [
                    pool.submit(self._worker, job, snapshot)
                    for _ in range(snapshot["concurrency"])
                ]
                for future in futures:
                    future.result()
        except Exception as exc:
            self._pause_error(
                job,
                {
                    "code": "WORKER_FAILURE",
                    "message": "后台任务异常："
                    + type(exc).__name__
                    + "；请导出诊断并重试",
                },
            )
        finally:
            with self.lock, self.store.connect() as c:
                state = c.execute(
                    "SELECT state FROM analysis_jobs WHERE id=?", (job,)
                ).fetchone()["state"]
                if state == "cancelling":
                    c.execute(
                        "UPDATE job_items SET state='cancelled' WHERE job_fk=? AND state IN ('pending','retry')",
                        (job,),
                    )
                    final = "cancelled"
                elif state == "pausing" or self.stop_event.is_set():
                    final = "paused"
                else:
                    failed = c.execute(
                        "SELECT 1 FROM job_items WHERE job_fk=? AND state IN ('failed','pending','inflight','retry') LIMIT 1",
                        (job,),
                    ).fetchone()
                    final = "completed_with_errors" if failed else "completed"
                c.execute("UPDATE analysis_jobs SET state=? WHERE id=?", (final, job))

    def _claim(self, job):
        with self.lock, self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            state = c.execute(
                "SELECT state FROM analysis_jobs WHERE id=?", (job,)
            ).fetchone()["state"]
            if state != "running" or self.stop_event.is_set():
                return None
            # Separate indexed lookups avoid sorting the entire 200k-item queue on every claim.
            pending = c.execute(
                "SELECT * FROM job_items WHERE job_fk=? AND state='pending' ORDER BY id LIMIT 1",
                (job,),
            ).fetchone()
            retry = c.execute(
                "SELECT * FROM job_items WHERE job_fk=? AND state='retry' AND retry_at<=? ORDER BY retry_at,id LIMIT 1",
                (job, time.time()),
            ).fetchone()
            row = min(
                (r for r in (pending, retry) if r is not None),
                key=lambda r: r["id"],
                default=None,
            )
            if row:
                c.execute(
                    "UPDATE job_items SET state='inflight',attempts=attempts+1 WHERE id=?",
                    (row["id"],),
                )
                return dict(row)

    def _worker(self, job, snapshot):
        try:
            self._worker_impl(job, snapshot)
        finally:
            self.store.close_connection()

    def _worker_impl(self, job, snapshot):
        try:
            client = Client(
                snapshot["profile"],
                self.vault.get(snapshot["profile"]["key_ref"]),
                self.transport,
            )
        except Exception:
            self._pause_error(
                job,
                {
                    "code": "KEY_UNAVAILABLE",
                    "message": "无法读取密钥，请确认 Windows 用户、密钥文件及模型设置",
                },
            )
            return
        while not self.stop_event.is_set() and self._state(job) == "running":
            item = self._claim(job)
            if item is None:
                pending = self.store.one(
                    "SELECT 1 n FROM job_items WHERE job_fk=? AND state IN ('pending','retry') LIMIT 1",
                    (job,),
                )
                if pending:
                    self.stop_event.wait(0.1)
                    continue
                return
            try:
                article = self.store.article_version(item["article_version_fk"])

                def call(messages, kind):
                    return self._call(job, item["id"], snapshot, client, messages, kind)

                def cached(key):
                    row = self.store.one(
                        "SELECT result FROM chunk_cache WHERE item_fk=? AND chunk_hash=?",
                        (item["id"], key),
                    )
                    return json.loads(row["result"]) if row else None

                def cache_save(key, value):
                    self.store.execute(
                        "INSERT OR REPLACE INTO chunk_cache VALUES(?,?,?)",
                        (item["id"], key, dumps(value)),
                    )

                result = analyze(client, article, snapshot, call, cached, cache_save)
                with self.store.connect() as c:
                    c.execute(
                        "INSERT OR IGNORE INTO analysis_results(job_item_fk,result,created_at) VALUES(?,?,?)",
                        (item["id"], dumps(result), time.time()),
                    )
                    result_id = c.execute(
                        "SELECT id FROM analysis_results WHERE job_item_fk=?",
                        (item["id"],),
                    ).fetchone()["id"]
                    c.executemany(
                        "INSERT OR IGNORE INTO case_usage(result_fk,case_id) VALUES(?,?)",
                        [
                            (result_id, case["id"])
                            for case in result.get("case_matches", [])
                        ],
                    )
                    c.execute(
                        "UPDATE job_items SET state='succeeded',error=NULL WHERE id=?",
                        (item["id"],),
                    )
            except Interrupted:
                self.store.execute(
                    "UPDATE job_items SET state='pending' WHERE id=?", (item["id"],)
                )
            except AIError as exc:
                info = exc.info
                attempt = item["attempts"] + 1
                retry = info["retryable"] and attempt <= snapshot["retries"]
                state = "retry" if retry else "failed"
                delay = max(
                    info.get("retry_after", 0),
                    min(300, 2 ** min(attempt, 8) + random.random()),
                )
                self.store.execute(
                    "UPDATE job_items SET state=?,error=?,retry_at=? WHERE id=?",
                    (state, dumps(info), time.time() + delay, item["id"]),
                )
                if info["fatal"]:
                    self._pause_error(job, info)
            except Exception as exc:
                self.store.execute(
                    "UPDATE job_items SET state='failed',error=? WHERE id=?",
                    (
                        dumps(
                            {
                                "code": "ITEM_FAILURE",
                                "message": "单篇处理异常："
                                + type(exc).__name__
                                + "；可重试并查看诊断",
                                "retryable": False,
                            }
                        ),
                        item["id"],
                    ),
                )

    def _call(self, job, item_id, snapshot, client, messages, kind):
        # Reserve a conservative byte-based upper bound before each call.
        options = client.options
        upper_in = len(dumps(messages).encode("utf-8")) + 512
        upper_out = int(options.get("max_output_tokens", 2048))
        reserve = (
            upper_in * float(options.get("input_price", 0))
            + upper_out * float(options.get("output_price", 0))
        ) / 1_000_000
        if reserve > snapshot.get("max_call_cost", float("inf")):
            raise AIError(
                "CALL_BUDGET_LIMIT",
                "单次请求保守费用超过单次上限，请调整分块或单次费用上限",
                False,
                True,
            )
        if upper_in + upper_out > snapshot["tpm"]:
            raise AIError(
                "TPM_REQUEST_TOO_LARGE",
                "单次请求保守 Token 上限超过 TPM，请提高配额或减小分块",
                False,
                True,
            )
        while True:
            with self.lock:
                if self.stop_event.is_set() or self._state(job) != "running":
                    raise Interrupted()
                row = self.store.one(
                    "SELECT spent,budget FROM analysis_jobs WHERE id=?", (job,)
                )
                if row["spent"] + self.reserved.get(job, 0) + reserve > row["budget"]:
                    self._pause_error(
                        job,
                        {
                            "code": "BUDGET_LIMIT",
                            "message": "剩余预算不足以覆盖下一请求的保守上限，已安全暂停；请调整预算后继续",
                        },
                    )
                    raise Interrupted()
                now = time.monotonic()
                history = self.rate.setdefault(job, deque())
                total_tokens = self.rate_tokens.get(job, 0)
                while history and now - history[0][0] >= 60:
                    total_tokens -= history.popleft()[1]
                self.rate_tokens[job] = total_tokens
                if (
                    len(history) < snapshot["rpm"]
                    and total_tokens + upper_in + upper_out <= snapshot["tpm"]
                ):
                    history.append((now, upper_in + upper_out))
                    self.rate_tokens[job] = total_tokens + upper_in + upper_out
                    self.reserved[job] = self.reserved.get(job, 0) + reserve
                    # Register dispatch atomically with the pause gate. A request
                    # may still be connecting when pause is clicked, but is then
                    # already visible as in-flight and counted, never a new dispatch.
                    self.store.execute(
                        "UPDATE analysis_jobs SET requests=requests+1 WHERE id=?",
                        (job,),
                    )
                    break
            self.stop_event.wait(0.1)
        try:
            try:
                result, usage = client.complete(messages)
            except AIError as error:
                usage = error.info.pop("usage", None)
                with self.store.connect() as c:
                    if usage:
                        c.execute(
                            "UPDATE analysis_jobs SET spent=spent+?,input_tokens=input_tokens+?,output_tokens=output_tokens+? WHERE id=?",
                            (
                                usage["cost"],
                                usage["input_tokens"],
                                usage["output_tokens"],
                                job,
                            ),
                        )
                    c.execute(
                        "INSERT INTO call_logs(job_fk,item_fk,kind,input_tokens,output_tokens,cost,created_at) VALUES(?,?,?,?,?,?,?)",
                        (
                            job,
                            item_id,
                            "error:" + error.info["code"],
                            usage["input_tokens"] if usage else None,
                            usage["output_tokens"] if usage else None,
                            usage["cost"] if usage else None,
                            time.time(),
                        ),
                    )
                raise
            with self.lock, self.store.connect() as c:
                c.execute(
                    "UPDATE analysis_jobs SET spent=spent+?,input_tokens=input_tokens+?,output_tokens=output_tokens+? WHERE id=?",
                    (usage["cost"], usage["input_tokens"], usage["output_tokens"], job),
                )
                c.execute(
                    "INSERT INTO call_logs(job_fk,item_fk,kind,input_tokens,output_tokens,cost,created_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        job,
                        item_id,
                        kind,
                        usage["input_tokens"],
                        usage["output_tokens"],
                        usage["cost"],
                        time.time(),
                    ),
                )
            return result
        finally:
            with self.lock:
                self.reserved[job] = max(0, self.reserved.get(job, 0) - reserve)

    def close(self, timeout=0):
        self.stop_event.set()
        for job, thread in list(self.threads.items()):
            if thread.is_alive():
                self.pause(job)
        if timeout:
            deadline = time.monotonic() + timeout
            for thread in self.threads.values():
                thread.join(max(0, deadline - time.monotonic()))


def refresh_credentials(store, vault, job):
    """Explicitly rebind only credentials; business/model snapshots stay immutable."""
    with store.connect() as c:
        row = c.execute("SELECT * FROM analysis_jobs WHERE id=?", (job,)).fetchone()
        if not row or row["state"] not in ("paused", "queued", "completed_with_errors"):
            raise ValueError("请先暂停并等待所有在途调用完成")
        snapshot = json.loads(row["snapshot"])
        old = snapshot["profile"]
        profile = c.execute(
            "SELECT * FROM model_profiles WHERE id=?", (old["id"],)
        ).fetchone()
        if (
            not profile
            or profile["tested_hash"] != fingerprint(profile)
            or not vault.has(profile["key_ref"])
        ):
            raise ValueError("请先在模型设置保存密钥并通过在线测试")
        if any(profile[k] != old[k] for k in ("base_url", "model", "options")):
            raise ValueError(
                "模型地址/名称/参数已变；为保留冻结版本，请新建剩余范围任务"
            )
        snapshot["profile"] = dict(profile)
        c.execute(
            "UPDATE analysis_jobs SET snapshot=? WHERE id=?", (dumps(snapshot), job)
        )
    store.audit(
        "job_credentials_refresh",
        job,
        "用户明确更新凭据引用，业务/提示词/案例/文章版本不变",
    )
