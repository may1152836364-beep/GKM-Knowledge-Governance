import json
import httpx
import pytest
from gkm.ai import (
    Client,
    AIError,
    endpoint,
    paragraphs,
    validate_result,
    save_profile,
    test_connection as connect_test,
    analyze,
)
from gkm.security import Vault
from gkm.storage import Store, dumps
from .helpers import MemoryVault, result, transport


def article():
    return {
        "article_id": "00001",
        "language": "zh",
        "title": "历史活动",
        "clean_content": "活动于2020年结束。\n但历史订单仍然提供售后。",
        "parse_status": "ready",
        "release_status": "已发布",
        "metadata_json": "{}",
    }


def test_evidence_removal():
    a = article()
    parts = paragraphs(a)
    value = result()
    value["recommendation"] = "review_removal"
    assert validate_result(value, a, parts)["recommendation"] == "business_verification"
    value = result(quote="完全编造的政策")
    checked = validate_result(value, a, parts)
    assert (
        not checked["evidence"]
        and checked["invalid_evidence"]
        and checked["recommendation"] == "business_verification"
    )
    v = validate_result(result(), a, parts)
    e = v["evidence"][0]
    assert (a["title"] + "\n" + a["clean_content"])[e["start"] : e["end"]] == e["quote"]
    value = result()
    value["recommendation"] = "automatic_delete"
    with pytest.raises(AIError):
        validate_result(value, a, parts)


@pytest.mark.parametrize(
    "code,retry,fatal",
    [
        (401, False, True),
        (403, False, True),
        (404, False, True),
        (429, True, False),
        (500, True, False),
    ],
)
def test_http_errors(code, retry, fatal):
    client = Client(
        {"base_url": "https://approved.example/v1", "model": "m", "options": "{}"},
        "never-print-this",
        transport(lambda req: httpx.Response(code, json={"error": "never-print-this"})),
    )
    with pytest.raises(AIError) as e:
        client.complete([])
    assert e.value.info["retryable"] == retry and e.value.info["fatal"] == fatal
    assert (
        "never-print-this" not in str(e.value) and e.value.info["http_status"] == code
    )


def test_configuration(tmp_path):
    db = Store(tmp_path)
    vault = MemoryVault()
    pid = save_profile(
        db, vault, "test", "https://approved.example/v1", "model", "secret"
    )
    assert connect_test(db, vault, pid, transport())["ok"]
    assert db.one("SELECT tested_hash FROM model_profiles")["tested_hash"]
    save_profile(
        db, vault, "test", "https://approved.example/v1", "model", "new", profile_id=pid
    )
    assert not db.one("SELECT tested_hash FROM model_profiles")["tested_hash"]
    assert b"secret" not in db.path.read_bytes()
    assert (
        endpoint("https://example.org/v1") == "https://example.org/v1/chat/completions"
    )
    with pytest.raises(ValueError):
        endpoint("https://user:key@example.org/v1")


def test_long_text_full_coverage(tmp_path):
    a = article()
    a["clean_content"] = (
        "长段落业务说明，必须逐段完整处理不能丢弃结尾。" * 150
    ) + "最后尾部关键证据"
    from gkm.ai import chunks

    p = paragraphs(a)
    segmented = list(chunks(p, 400))
    assert "".join(s["text"] for group in segmented for s in group) == "".join(
        x["text"] for x in p
    )
    assert segmented[-1][-1]["text"].endswith("最后尾部关键证据")


def test_timeout():
    def fail(req):
        raise httpx.ReadTimeout("sensitive")

    with pytest.raises(AIError) as e:
        Client(
            {"base_url": "https://example.org", "model": "m", "options": "{}"},
            "k",
            transport(fail),
        ).complete([])
    assert e.value.info["code"] == "TIMEOUT" and "sensitive" not in str(e.value)


def test_no_plaintext_fallback(tmp_path):
    import os

    if os.name == "nt":
        pytest.skip("Linux fallback test")
    with pytest.raises(RuntimeError):
        Vault(tmp_path).save("a" * 32, "secret")
    assert not list(tmp_path.rglob("*.dpapi"))


def test_all_chunks_summary_and_failure_when_context_cannot_fit():
    a = article()
    a["clean_content"] = "\n".join(
        f"段落{i}，业务文本需要完整分析，保留历史售后流程。" * 80 for i in range(14)
    )
    profile = {
        "base_url": "https://approved.example",
        "model": "m",
        "options": dumps({"context_chars": 18000, "max_output_tokens": 2048}),
    }
    client = Client(profile, "k")
    snapshot = {
        "prompt": {
            "system": "只依据输入",
            "business": "核实用途",
            "output": "JSON",
            "rules": "[]",
        },
        "cases": [],
        "analysis_date": "2026-10-16",
    }
    seen = []
    summary = []

    def call(messages, kind):
        data = json.loads(messages[-1]["content"])
        if kind == "chunk":
            seen.extend(p["text"] for p in data["paragraphs"])
            p = data["paragraphs"][-1]
            return result(quote=p["text"][:20], paragraph=p["id"])
        summary.extend(data["all_segment_results"])
        return data["all_segment_results"][0]

    value = analyze(client, a, snapshot, call)
    assert value["segment_count"] > 1 and len(summary) == value["segment_count"]
    assert "".join(seen) == "".join(p["text"] for p in paragraphs(a))
    assert "段落13" in "".join(seen)
    profile["options"] = dumps({"context_chars": 4000})
    client = Client(profile, "k")
    with pytest.raises(AIError) as e:
        analyze(client, a, snapshot, call)
    assert e.value.info["code"] == "CONTEXT_INCOMPLETE"


def test_invalid_pricing_and_context_configuration(tmp_path):
    db = Store(tmp_path)
    vault = MemoryVault()
    for options in (
        {"input_price": -1},
        {"input_price": float("nan")},
        {"timeout": 0},
        {"context_chars": 100},
    ):
        with pytest.raises(ValueError):
            save_profile(
                db, vault, "bad", "https://approved.example/v1", "m", "k", options
            )
