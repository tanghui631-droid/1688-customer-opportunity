import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..")))

from _auth import get_ak_from_env
from _output import print_output, print_error
from capabilities.boss_daily_patrol.service import build_patrol, collect_buyers, enrich_advice

COMMAND_NAME = "boss_daily_patrol"
COMMAND_DESC = "全店客户机会巡检，自动汇总去重并排序，输出老板日报"


def default_state_dir() -> Path:
    base = Path(os.environ.get("OPENCLAW_CONFIG_DIR", Path.home() / ".openclaw"))
    return base / "1688-customer-opportunity" / "patrol"


def load_previous(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_snapshot(path: Path, snapshot: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    ak_id, _ = get_ak_from_env()
    if not ak_id:
        print_output(False, "❌ AK 未配置，请运行：`python cli.py configure YOUR_AK`", {"data": {}})
        return

    parser = argparse.ArgumentParser(description=COMMAND_DESC)
    parser.add_argument("--top", type=int, default=10, help="重点客户数量，默认 10")
    parser.add_argument("--date-type", default="RECENT_30", choices=["RECENT_1", "RECENT_7", "RECENT_30"])
    parser.add_argument("--max-clusters", type=int, default=20)
    parser.add_argument("--skip-advice", action="store_true", help="跳过画像建议，只做名单汇总")
    parser.add_argument("--state-dir", default="", help="日报快照目录，默认 ~/.openclaw/1688-customer-opportunity/patrol")
    args = parser.parse_args()

    state_dir = Path(args.state_dir) if args.state_dir else default_state_dir()
    previous = load_previous(state_dir / "latest.json")
    try:
        collected = collect_buyers(date_type=args.date_type, max_clusters=args.max_clusters)
        pool = collected["pool"]
        errors = collected["errors"]
        ranked_ids = list(pool.keys())
        advice_count = 0
        if ranked_ids and not args.skip_advice:
            advice_count = enrich_advice(pool, ranked_ids[:40], errors)
        coverage = {
            "cluster_count": collected["cluster_count"],
            "unique_buyers": len(pool),
            "advice_count": advice_count,
        }
        report = build_patrol(pool, previous=previous, today=datetime.now(), top_n=args.top, coverage=coverage, errors=errors)
        save_snapshot(state_dir / "latest.json", report["snapshot"])
        save_snapshot(state_dir / f"{report['date']}.json", report["snapshot"])
        print_output(True, report["markdown"], {
            "data": {
                "date": report["date"],
                "top_count": len(report["top"]),
                "extra_count": report["extra_count"],
                "coverage": coverage,
                "errors": errors,
                "history_available": bool(report["history"].get("available")),
                "buyers": [
                    {
                        "login_id": b.get("login_id"),
                        "level": b.get("level"),
                        "tags": b.get("tags"),
                        "reason": b.get("reason"),
                    }
                    for b in report["top"]
                ],
            }
        })
    except Exception as exc:
        print_error(exc, {"data": {}})


if __name__ == "__main__":
    main()
