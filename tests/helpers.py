import json
import httpx
from gkm.storage import dumps


class MemoryVault:
    def __init__(self):
        self.values = {}

    def save(self, ref, key):
        self.values[ref] = key

    def get(self, ref):
        return self.values[ref]

    def has(self, ref):
        return ref in self.values


def result(
    article_id="00001", language="zh", quote="活动于2020年结束。", paragraph="p2"
):
    return {
        "article_id": article_id,
        "language": language,
        "validity_status": "suspected_issue",
        "affected_scope": "partial",
        "risk_types": ["time_expiration"],
        "recommendation": "business_verification",
        "risk_priority": "medium",
        "reason_zh": "期限已过，需核实历史用途",
        "evidence": [
            {
                "paragraph_id": paragraph,
                "quote": quote,
                "evidence_level": "A",
                "issue": "期限",
            }
        ],
        "missing_information": ["历史用途"],
        "suggested_revision_zh": "核实历史履约用途",
        "reference_case_ids": [],
        "next_review_date": None,
        "removal_justification": {
            "cannot_fix_by_revision": False,
            "no_historical_use_confirmed": False,
            "basis": "",
        },
    }


def transport(handler=None):
    def default(req):
        data = json.loads(req.content)
        if "连接测试" in data["messages"][-1]["content"]:
            value = {"ok": True}
        else:
            user = json.loads(data["messages"][-1]["content"])
            para = user.get("paragraphs", [{"id": "p2", "text": "活动于2020年结束。"}])[
                -1
            ]
            value = result(
                user["article_id"], user["language"], para["text"], para["id"]
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": dumps(value)}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 40},
            },
        )

    return httpx.MockTransport(handler or default)


def seed(store, count=5):
    import time

    with store.connect() as c:
        for i in range(count):
            aid = c.execute(
                "INSERT INTO articles(article_id,language,source_scope,category,release_status) VALUES(?,?,?,?,?)",
                (str(i), "zh", "test", "活动", "已发布"),
            ).lastrowid
            vid = c.execute(
                "INSERT INTO article_versions(article_fk,title,raw_content,clean_content,content_hash,metadata_json,parse_status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    aid,
                    "活动说明",
                    "活动于2020年结束。请查询历史订单并联系售后团队处理履约问题。",
                    "活动于2020年结束。请查询历史订单并联系售后团队处理履约问题。",
                    str(i),
                    "{}",
                    "ready",
                    time.time(),
                ),
            ).lastrowid
            c.execute("UPDATE articles SET current_version=? WHERE id=?", (vid, aid))
