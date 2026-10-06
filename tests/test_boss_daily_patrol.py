import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from capabilities.boss_daily_patrol.rank import (  # noqa: E402
    apply_antispam,
    build_buyer_view,
    compare_history,
    render_report,
    score_buyer,
    select_top,
)
from capabilities.boss_daily_patrol.service import build_patrol  # noqa: E402


TODAY = datetime(2026, 10, 6)


def test_recent_inquiry_ranks_above_silent_high_gmv():
    recent = score_buyer({
        "login_id": "recent_buyer",
        "tags": ["询盘未成交"],
        "lst_inq_time": "2026-10-05",
        "inq_relation": "有询盘",
    }, TODAY)
    silent = score_buyer({
        "login_id": "silent_rich",
        "tags": ["高价值老客"],
        "gmv_1m_level": "高",
    }, TODAY)
    assert recent["level"] == "高"
    assert silent["level"] != "高"
    top, _ = select_top([silent, recent], 10)
    assert top[0]["login_id"] == "recent_buyer"


def test_low_value_not_padded_into_top():
    weak = score_buyer({"login_id": "weak", "tags": ["高价值老客"]}, TODAY)
    assert weak["level"] == "低"
    top, extra = select_top([weak], 10)
    assert top == []
    assert extra == 0


def test_dedupe_tags_and_private_channel_reminder():
    buyer = score_buyer({
        "login_id": "same_buyer",
        "tags": ["周期采购", "询盘未成交"],
        "lst_inq_time": "2026-10-04",
        "gmv_1m_level": "高",
        "if_ka": "是",
    }, TODAY)
    view = build_buyer_view(buyer)
    assert view["type"] == "询盘未成交"
    assert "周期采购" in view["tags"]
    assert view["private_reminder"] is True
    assert "负责人：当前数据源未提供负责人信息" in view["confirmed"]
    assert "张三" not in view["action"]


def test_antispam_does_not_repeat_contact():
    buyer = score_buyer({
        "login_id": "repeat",
        "tags": ["老客促活"],
        "gmv_1m_level": "高",
    }, TODAY)
    previous = {"login_id": "repeat", "level": buyer["level"], "tags": buyer["tags"], "follow_time": "今天"}
    cooled = apply_antispam(buyer, previous)
    view = build_buyer_view(cooled)
    assert cooled["antispam"] is True
    assert view["follow_time"] == "暂不联系"
    assert "昨日已建议联系" in view["reason"]


def test_no_history_does_not_invent_trend():
    report = render_report("2026-10-06", [], 0, {"cluster_count": 0, "unique_buyers": 0, "advice_count": 0}, {"available": False}, [])
    assert "当前暂无上一期可比数据" in report
    assert "相比昨天上涨" not in report
    assert "plan_id" not in report


def test_history_compare_only_with_snapshot():
    history = compare_history(
        [{"login_id": "a", "level": "高"}],
        {"date": "2026-10-05", "top": [{"login_id": "b", "level": "中"}]},
    )
    assert history["available"] is True
    assert history["new"] == ["a"]
    assert history["left"] == ["b"]


def test_patrol_report_from_pool():
    pool = {
        "buyer_a": {"login_id": "buyer_a", "tags": ["询盘未成交"], "lst_inq_time": "2026-10-06", "inq_relation": "咨询规格"},
        "buyer_b": {"login_id": "buyer_b", "tags": ["流失风险"], "gmv_1m_level": "高"},
    }
    report = build_patrol(pool, previous=None, today=TODAY, top_n=10, coverage={"cluster_count": 2, "unique_buyers": 2, "advice_count": 0})
    assert "buyer_a" in report["markdown"]
    assert "1688平台暂未看到成交记录" in report["markdown"]
    assert report["history"]["available"] is False


def test_cli_registers_command_without_breaking_usage():
    proc = subprocess.run([sys.executable, str(ROOT / "cli.py")], capture_output=True, text=True)
    payload = json.loads(proc.stdout)
    assert "boss_daily_patrol" in payload["markdown"]
    assert "list_customer_cluster" in payload["markdown"]
    assert "list_customer_details" in payload["markdown"]


def test_cli_missing_ak_does_not_fabricate():
    env = dict(**{k: v for k, v in __import__("os").environ.items() if k != "OPENCLAW_CONFIG_DIR"})
    proc = subprocess.run(
        [sys.executable, str(ROOT / "cli.py"), "boss_daily_patrol"],
        capture_output=True,
        text=True,
        env=env,
    )
    payload = json.loads(proc.stdout)
    assert payload["success"] is False
    assert "买家账号" not in payload["markdown"] or "AK" in payload["markdown"]
