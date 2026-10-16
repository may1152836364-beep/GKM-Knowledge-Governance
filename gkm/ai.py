import hashlib
import json
import math
import re
import time
from urllib.parse import urlparse
import httpx
from jsonschema import Draft202012Validator, FormatChecker
from .storage import dumps, uid

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "article_id",
        "language",
        "validity_status",
        "affected_scope",
        "risk_types",
        "recommendation",
        "risk_priority",
        "reason_zh",
        "evidence",
        "missing_information",
        "suggested_revision_zh",
        "reference_case_ids",
        "next_review_date",
        "removal_justification",
    ],
    "properties": {
        "article_id": {"type": "string"},
        "language": {"type": "string"},
        "validity_status": {
            "enum": [
                "confirmed_issue",
                "suspected_issue",
                "no_issue_found",
                "insufficient_information",
            ]
        },
        "affected_scope": {"enum": ["full", "partial", "not_applicable", "unknown"]},
        "risk_types": {
            "type": "array",
            "items": {
                "enum": [
                    "time_expiration",
                    "product",
                    "function",
                    "process",
                    "policy",
                    "version",
                    "contradiction",
                ]
            },
        },
        "recommendation": {
            "enum": ["keep", "revise", "review_removal", "business_verification"]
        },
        "risk_priority": {"enum": ["high", "medium", "low"]},
        "reason_zh": {"type": "string", "minLength": 1},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["paragraph_id", "quote", "evidence_level", "issue"],
                "properties": {
                    "paragraph_id": {"type": "string"},
                    "quote": {"type": "string", "minLength": 1},
                    "evidence_level": {"enum": ["A", "B", "C"]},
                    "issue": {"type": "string"},
                },
            },
        },
        "missing_information": {"type": "array", "items": {"type": "string"}},
        "suggested_revision_zh": {"type": "string"},
        "reference_case_ids": {"type": "array", "items": {"type": "string"}},
        "next_review_date": {"type": ["string", "null"], "format": "date"},
        "removal_justification": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "cannot_fix_by_revision",
                "no_historical_use_confirmed",
                "basis",
            ],
            "properties": {
                "cannot_fix_by_revision": {"type": "boolean"},
                "no_historical_use_confirmed": {"type": "boolean"},
                "basis": {"type": "string"},
            },
        },
    },
}


class AIError(Exception):
    def __init__(
        self, code, message, retryable=False, fatal=False, status=None, retry_after=0
    ):
        self.info = {
            "code": code,
            "message": message,
            "retryable": retryable,
            "fatal": fatal,
            "http_status": status,
            "retry_after": retry_after,
        }
        super().__init__(message)


def endpoint(base):
    parsed = urlparse(base)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("地址不能包含凭据、查询参数或片段")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")
    ):
        raise ValueError("远程 API 必须使用 HTTPS；仅本机模拟服务允许 HTTP")
    if not parsed.hostname:
        raise ValueError("API 地址缺少主机名")
    return (
        base.rstrip("/")
        if base.rstrip("/").endswith("/chat/completions")
        else base.rstrip("/") + "/chat/completions"
    )


def fingerprint(profile):
    return hashlib.sha256(
        dumps(
            [
                profile["base_url"],
                profile["model"],
                profile["options"],
                profile["key_ref"],
            ]
        ).encode()
    ).hexdigest()


def save_profile(
    store, vault, name, base_url, model, key="", options=None, profile_id=None
):
    endpoint(base_url)
    if not model.strip():
        raise ValueError("模型名称不能为空")
    pid = profile_id or uid()
    old = store.one("SELECT * FROM model_profiles WHERE id=?", (pid,))
    ref = old["key_ref"] if old else uid()
    if key:
        # New ref makes a previous test invalid even if only credential changes.
        ref = uid()
        vault.save(ref, key)
    elif not old or not vault.has(ref):
        raise ValueError("请配置 API Key")
    options = options or {
        "timeout": 60,
        "max_output_tokens": 2048,
        "context_chars": 24000,
        "input_price": 0,
        "output_price": 0,
    }
    for name, default, low, high in [
        ("timeout", 60, 1, 300),
        ("context_chars", 24000, 4000, 1000000),
        ("max_output_tokens", 2048, 256, 32000),
        ("input_price", 0, 0, 1000000),
        ("output_price", 0, 0, 1000000),
    ]:
        try:
            value = float(options.get(name, default))
        except (TypeError, ValueError):
            raise ValueError("模型参数不是数字：" + name) from None
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError("模型参数超出范围：" + name)
    profile = {
        "base_url": base_url.strip(),
        "model": model.strip(),
        "options": dumps(options),
        "key_ref": ref,
    }
    keep_test = old and fingerprint(old) == fingerprint(profile)
    store.execute(
        "INSERT OR REPLACE INTO model_profiles VALUES(?,?,?,?,?,?,?,?)",
        (
            pid,
            name,
            profile["base_url"],
            profile["model"],
            ref,
            profile["options"],
            old["tested_hash"] if keep_test else None,
            old["test_report"] if keep_test else None,
        ),
    )
    store.audit("model_save", pid, "模型配置已保存；密钥不回显")
    return pid


class Client:
    def __init__(self, profile, key, transport=None):
        self.profile = profile
        self.key = key
        self.options = json.loads(profile["options"])
        self.transport = transport

    def complete(self, messages):
        try:
            with httpx.Client(
                timeout=float(self.options.get("timeout", 60)),
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = client.post(
                    endpoint(self.profile["base_url"]),
                    headers={"Authorization": "Bearer " + self.key},
                    json={
                        "model": self.profile["model"],
                        "messages": messages,
                        "temperature": 0,
                        "max_tokens": int(self.options.get("max_output_tokens", 2048)),
                        "response_format": {"type": "json_object"},
                    },
                )
            if response.status_code != 200:
                code = response.status_code
                # Do not expose arbitrary provider bodies: they can echo keys/content.
                message = {
                    401: "鉴权失败，请重新保存有效密钥并测试",
                    403: "无访问权限，请确认企业授权",
                    404: "端点或模型不存在，请检查地址和模型 ID",
                    400: "请求参数不受支持，请检查 JSON 输出兼容性、模型参数或上下文上限",
                    402: "供应商余额或付款状态异常，请检查账户配额",
                    429: "供应商限流，请降低并发或 RPM/TPM",
                }.get(code, "供应商错误，请检查服务状态与配置")
                try:
                    retry_after = min(
                        300, max(0, float(response.headers.get("Retry-After", "0")))
                    )
                except ValueError:
                    retry_after = 0
                vendor_code = None
                try:
                    raw = response.json().get("error", {}).get("code")
                    if raw in {
                        "invalid_api_key",
                        "insufficient_quota",
                        "rate_limit_exceeded",
                        "model_not_found",
                        "invalid_request_error",
                        "context_length_exceeded",
                        "content_filter",
                    }:
                        vendor_code = raw
                except (ValueError, AttributeError, TypeError):
                    pass
                if vendor_code:
                    message += "（供应商代码：" + vendor_code + "）"
                raise AIError(
                    "HTTP_" + str(code),
                    message,
                    (code in (408, 429) or code >= 500)
                    and vendor_code != "insufficient_quota",
                    code in (401, 402, 403, 404) or vendor_code == "insufficient_quota",
                    code,
                    retry_after,
                )
            payload = response.json()
            usage = payload.get("usage", {})
            tokens_in = int(usage.get("prompt_tokens", 0))
            tokens_out = int(usage.get("completion_tokens", 0))
            if not usage or tokens_in < 0 or tokens_out < 0:
                raise AIError(
                    "MISSING_USAGE",
                    "接口未返回有效 Token 用量，无法可靠控制预算；请调整兼容接口并核对供应商账单",
                    fatal=True,
                )
            usage_info = {
                "input_tokens": tokens_in,
                "output_tokens": tokens_out,
                "cost": (
                    tokens_in * float(self.options.get("input_price", 0))
                    + tokens_out * float(self.options.get("output_price", 0))
                )
                / 1_000_000,
            }
            try:
                if payload["choices"][0].get("finish_reason") == "length":
                    raise AIError(
                        "OUTPUT_TRUNCATED",
                        "模型输出被截断，请增加输出上限或减小分块",
                        True,
                    )
                content = payload["choices"][0]["message"]["content"]
                content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
                result = json.loads(content)
            except AIError as error:
                error.info["usage"] = usage_info
                raise
            except (ValueError, KeyError, TypeError, IndexError, AttributeError):
                error = AIError(
                    "INVALID_RESPONSE",
                    "模型输出 JSON 无效；已记录本次 Token 用量，可受控重试",
                    True,
                )
                error.info["usage"] = usage_info
                raise error from None
            return result, usage_info
        except AIError:
            raise
        except httpx.TimeoutException:
            raise AIError("TIMEOUT", "请求超时，请检查网络或增加超时", True) from None
        except httpx.HTTPError:
            raise AIError(
                "NETWORK_TLS_PROXY",
                "连接、代理或证书校验失败，请检查网络和受信任证书；不会关闭 TLS 验证",
                True,
            ) from None
        except (ValueError, KeyError, TypeError, IndexError):
            raise AIError(
                "INVALID_RESPONSE",
                "接口响应不是有效 Chat Completions JSON，请检查模型或响应格式",
                True,
            ) from None


def test_connection(store, vault, pid, transport=None):
    profile = store.one("SELECT * FROM model_profiles WHERE id=?", (pid,))
    start = time.monotonic()
    try:
        result, usage = Client(
            profile, vault.get(profile["key_ref"]), transport
        ).complete(
            [
                {"role": "system", "content": '仅返回 JSON 对象 {"ok":true}。'},
                {"role": "user", "content": "连接测试；不包含公司文章。"},
            ]
        )
        if result != {"ok": True}:
            raise AIError("JSON_CONTRACT", "模型未遵守最小 JSON 格式")
        report = {
            "ok": True,
            "latency_ms": round((time.monotonic() - start) * 1000),
            "usage": usage,
            "tested_at": time.time(),
        }
        store.execute(
            "UPDATE model_profiles SET tested_hash=?,test_report=? WHERE id=?",
            (fingerprint(profile), dumps(report), pid),
        )
    except AIError as e:
        report = {"ok": False, **e.info, "tested_at": time.time()}
        store.execute(
            "UPDATE model_profiles SET tested_hash=NULL,test_report=? WHERE id=?",
            (dumps(report), pid),
        )
    return report


def paragraphs(article):
    # Includes title, and never discards trailing text. Stable character intervals.
    raw = article["title"] + "\n" + article["clean_content"]
    result = []
    start = 0
    for index, line in enumerate(raw.splitlines(keepends=True), 1):
        value = line.rstrip("\r\n")
        if value.strip():
            result.append({"id": "p" + str(index), "text": value, "start": start})
        start += len(line)
    return result


def validate_result(value, article, parts, case_ids=()):
    errors = list(
        Draft202012Validator(SCHEMA, format_checker=FormatChecker()).iter_errors(value)
    )
    if errors:
        raise AIError("RESULT_SCHEMA", "输出不符合固定结构或枚举，需受控重试", True)
    if (
        value["article_id"] != article["article_id"]
        or value["language"] != article["language"]
    ):
        raise AIError("RESULT_IDENTITY", "模型输出文章 ID/语言不匹配", True)
    if not re.search(r"[\u4e00-\u9fff]", value["reason_zh"]):
        raise AIError("RESULT_LANGUAGE", "模型原因须使用中文，请检查提示词并重试", True)
    by_id = {p["id"]: p for p in parts}
    checked = []
    invalid = []
    for ev in value["evidence"]:
        p = by_id.get(ev["paragraph_id"])
        quote = ev["quote"]
        offset = p["text"].find(quote) if p else -1
        if offset < 0:
            invalid.append({**ev, "valid": False})
        else:
            checked.append(
                {
                    **ev,
                    "valid": True,
                    "start": p["start"] + offset,
                    "end": p["start"] + offset + len(quote),
                }
            )
    value = {**value, "evidence": checked, "invalid_evidence": invalid}
    value["reference_case_ids"] = [
        i for i in value["reference_case_ids"] if i in case_ids
    ]
    value["guards"] = []
    value["source_scope"] = article.get("source_scope", "default")
    strong = any(e["evidence_level"] == "A" for e in checked)
    if invalid or (value["validity_status"] == "confirmed_issue" and not strong):
        value["validity_status"] = "insufficient_information"
        value["recommendation"] = "business_verification"
        value["guards"].append("存在无效证据或缺少直接证据，已降级为待核实")
    removal = value["removal_justification"]
    # Conservative: basis itself must be supported verbatim in this source.
    basis = removal["basis"]
    valid_basis = bool(basis.strip()) and any(basis in p["text"] for p in parts)
    if value["recommendation"] == "review_removal" and not (
        strong
        and value["validity_status"] == "confirmed_issue"
        and value["affected_scope"] == "full"
        and removal["cannot_fix_by_revision"]
        and removal["no_historical_use_confirmed"]
        and valid_basis
        and not value["missing_information"]
    ):
        value["recommendation"] = "business_verification"
        value["guards"].append("下架保护门槛未满足，需核实整体适用性、修改与历史用途")
    if article["parse_status"] != "ready":
        value["validity_status"] = "insufficient_information"
        value["recommendation"] = "business_verification"
        value["guards"].append("正文或标题不足，不计入正常 AI 成功")
    return value


def chunks(parts, limit):
    # Split long individual paragraphs retaining the original ID and offsets.
    current = []
    size = 0
    for part in parts:
        for offset in range(0, len(part["text"]), max(1, limit)):
            segment = {
                **part,
                "text": part["text"][offset : offset + limit],
                "start": part["start"] + offset,
            }
            if current and size + len(segment["text"]) > limit:
                yield current
                current = []
                size = 0
            current.append(segment)
            size += len(segment["text"])
    if current:
        yield current


def analyze(client, article, snapshot, call, cached=None, cache_save=None):
    parts = paragraphs(article)
    options = client.options
    context = int(options.get("context_chars", 24000))
    if context < 4000:
        raise AIError(
            "CONTEXT_CONFIG", "上下文字符预算至少为 4000；请按供应商窗口保守设置"
        )
    prompt = snapshot["prompt"]
    cases = snapshot.get("cases", [])
    # lexical retrieval only, approved snapshot; document provenance is supplied.
    from .learning import retrieve

    cases = retrieve(cases, article)
    rules = prompt["rules"]
    system = (
        prompt["system"]
        + "\n"
        + prompt["business"]
        + "\n"
        + prompt["output"]
        + "\n"
        + prompt.get("case_prompt", "案例仅在相同事实和边界内参考")
        + "\n固定 JSON Schema："
        + dumps(SCHEMA)
    )
    # Keep original Excel values in storage, but never duplicate the entire raw
    # body in every segment's metadata. Honor custom user column mappings too.
    import_mapping = json.loads(article.get("import_mapping") or "{}")
    body_column = import_mapping.get("raw_content")
    excluded = (
        {body_column}
        if body_column
        else {
            "raw_content",
            "clean_content",
            "content(文章正文)",
            "文章正文",
            "content",
            "正文",
        }
    )
    analysis_metadata = {
        key: value
        for key, value in json.loads(article["metadata_json"]).items()
        if key not in excluded
    }
    metadata = {
        "article_id": article["article_id"],
        "language": article["language"],
        "release_status": article["release_status"],
        "title": article["title"],
        "metadata": analysis_metadata,
        "analysis_date": snapshot["analysis_date"],
        "rules": rules,
        "approved_cases": cases,
    }
    overhead = len(system) + len(dumps(metadata))
    available = context - overhead - 2500
    if available < 500:
        raise AIError(
            "CONTEXT_INCOMPLETE",
            "提示词/元数据/案例超出上下文预算，未截断正文；请扩大上下文或缩短配置",
        )
    segments = list(chunks(parts, available))
    summaries = []
    for segment in segments:
        data = {
            **metadata,
            "paragraphs": segment,
            "stage": "分段候选判断；不能把分段结论视为整篇结论",
        }
        digest = hashlib.sha256(dumps(data).encode()).hexdigest()
        result = cached(digest) if cached else None
        if result is None:
            result = call(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": dumps(data)},
                ],
                "chunk",
            )
            # Validate and preserve raw schema result for summary; invalid quotations fail closed.
            validate_result(result, article, parts, [c["id"] for c in cases])
            if cache_save:
                cache_save(digest, result)
        summaries.append(result)
    if len(summaries) == 1:
        final = summaries[0]
    else:
        final_data = {
            **metadata,
            "all_segment_results": summaries,
            "stage": "整篇汇总：必须考虑全部分段，保留原段落编号与逐字证据",
        }
        if len(system) + len(dumps(final_data)) + 2500 > context:
            raise AIError(
                "CONTEXT_INCOMPLETE",
                "全部分段摘要超出汇总窗口，分析不完整；请扩大窗口或调整提示词，不会丢弃末尾",
            )
        final = call(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": dumps(final_data)},
            ],
            "summary",
        )
    validated = validate_result(final, article, parts, [c["id"] for c in cases])
    validated["segment_count"] = len(segments)
    validated["case_matches"] = [
        {"id": c["id"], "match_reason": c["match_reason"]} for c in cases
    ]
    return validated
