"""全店客户机会巡检。复用原有查询能力，不新增外部接口。"""

from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..")))

from capabilities.boss_daily_patrol.rank import (
    apply_antispam,
    compare_history,
    render_report,
    score_buyer,
    select_top,
    snapshot_from_top,
)
from capabilities.customer_reception_advice.service import customer_reception_advice
from capabilities.list_cluster_buyer_detail.service import list_cluster_buyer_detail
from capabilities.list_customer_cluster.service import list_customer_cluster
from capabilities.list_customer_details.service import list_customer_details


TAG_BY_CROWD = {
    "流失买家": "流失风险",
    "周期采购": "周期采购",
    "老客促活": "老客促活",
    "询盘未成交": "询盘未成交",
}

SUPPLEMENT_SOURCES = [
    {"crowd_type": "询盘未成交", "label": "询盘未成交"},
    {"crowd_type": "周期采购", "label": "周期采购"},
    {"crowd_type": "老客促活", "label": "老客促活"},
    {"crowd_type": "流失买家", "label": "流失风险"},
    {"gmv_1m_level": "高", "label": "高价值老客"},
    {"ord_cnt_1m_level": "高", "label": "高价值老客"},
    {"user_label_list": ["B类买家"], "label": "高价值老客"},
]


def _login(item: dict) -> str:
    return str(item.get("buyer_login_id") or item.get("login_id") or item.get("nick") or "").strip()


def _ensure(pool: dict, login_id: str) -> dict:
    if login_id not in pool:
        pool[login_id] = {"login_id": login_id, "tags": [], "sources": []}
    return pool[login_id]


def _merge_item(pool: dict, item: dict, tag: str, source: str):
    login_id = _login(item)
    if not login_id:
        return
    row = _ensure(pool, login_id)
    if tag and tag not in row["tags"]:
        row["tags"].append(tag)
    if source not in row["sources"]:
        row["sources"].append(source)
    for key in (
        "lst_inq_time",
        "ord_cnt_1m_level",
        "gmv_1m_level",
        "inq_relation",
        "if_ka",
        "buyer_credit_level",
        "procurement_mode",
        "nick",
    ):
        if item.get(key) and not row.get(key):
            row[key] = item.get(key)


def _cluster_tag(cluster: dict) -> str:
    text = " ".join(str(cluster.get(k) or "") for k in ("cluster_name", "feature", "plan_reason", "cluster_main_tag"))
    for raw, tag in TAG_BY_CROWD.items():
        if raw in text or tag in text:
            return tag
    if "复购" in text or "周期" in text:
        return "周期采购"
    if "流失" in text:
        return "流失风险"
    if "促活" in text or "沉默" in text:
        return "老客促活"
    if "询盘" in text:
        return "询盘未成交"
    return "高价值老客"


def _safe(errors: list, name: str, func):
    try:
        return func()
    except Exception as exc:
        errors.append(f"{name}：{exc}")
        return None


def collect_buyers(date_type: str = "RECENT_30", max_clusters: int = 20, page_size: int = 50) -> dict:
    errors = []
    pool = {}
    clusters = []
    cluster_data = _safe(errors, "客群列表", list_customer_cluster) or {}
    cluster_list = cluster_data.get("list") or []
    for cluster in cluster_list[:max_clusters]:
        plan_id = cluster.get("plan_id")
        if not plan_id:
            continue
        clusters.append({"name": cluster.get("cluster_name") or "", "plan_id": plan_id})
        detail = _safe(errors, f"客群买家{cluster.get('cluster_name') or ''}", lambda pid=plan_id: list_cluster_buyer_detail(pid))
        buyers = (detail or {}).get("list") or []
        tag = _cluster_tag(cluster)
        for buyer in buyers:
            _merge_item(pool, buyer, tag, cluster.get("cluster_name") or "AI客群")

    # 客群覆盖不足，或为了避免只看第一个客群，始终用原有老客筛选再补一轮。
    for source in SUPPLEMENT_SOURCES:
        label = source["label"]
        kwargs = {"date_type": date_type, "page_no": 1, "page_size": page_size}
        kwargs.update({k: v for k, v in source.items() if k != "label"})
        data = _safe(errors, label, lambda kw=kwargs: list_customer_details(**kw))
        for item in (data or {}).get("list") or []:
            _merge_item(pool, item, label, label)

    return {
        "pool": pool,
        "clusters": clusters,
        "cluster_count": len(cluster_list),
        "errors": errors,
    }


def enrich_advice(pool: dict, login_ids: list, errors: list) -> int:
    if not login_ids:
        return 0
    payload = [{"login_id": login_id} for login_id in login_ids]
    try:
        result = customer_reception_advice(payload)
    except Exception as exc:
        errors.append(f"画像建议：{exc}")
        return 0
    count = 0
    for item in result.get("results") or []:
        login_id = _login(item)
        if login_id not in pool:
            continue
        if item.get("follow_suggestion"):
            pool[login_id]["follow_suggestion"] = item.get("follow_suggestion")
        if item.get("buyer_profile"):
            pool[login_id]["buyer_profile"] = item.get("buyer_profile")
        count += 1
    return count


def build_patrol(pool: dict, previous: dict = None, today: datetime = None, top_n: int = 10,
                 coverage: dict = None, errors: list = None) -> dict:
    today = today or datetime.now()
    previous_map = {item.get("login_id"): item for item in (previous or {}).get("top") or []}
    scored = []
    for row in pool.values():
        buyer = score_buyer(row, today)
        buyer = apply_antispam(buyer, previous_map.get(buyer.get("login_id")))
        scored.append(buyer)
    top, extra = select_top(scored, top_n)
    history = compare_history(
        [{"login_id": b.get("login_id"), "level": b.get("level"), "tags": b.get("tags")} for b in top],
        previous,
    )
    date_text = today.strftime("%Y-%m-%d")
    markdown = render_report(date_text, top, extra, coverage or {}, history, errors or [])
    return {
        "date": date_text,
        "top": top,
        "extra_count": extra,
        "history": history,
        "markdown": markdown,
        "snapshot": snapshot_from_top(date_text, top),
        "errors": errors or [],
    }
