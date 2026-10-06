def test_no_snapshot_file_means_no_trend_section():
    """没有快照文件时，日报不得出现任何趋势措辞。"""
    report = render_report(
        "2026-10-06",
        [{"login_id": "a", "level": "高", "tags": ["询盘未成交"],
          "reason": "最近询盘", "lst_inq_time": "2026-10-05"}],
        0,
        {"cluster_count": 1, "unique_buyers": 1, "advice_count": 0},
        {"available": False},
        [],
    )
    banned = ["相比昨天", "上涨", "下降", "新增重点客户", "连续"]
    for word in banned:
        assert word not in report, f"无快照时不应出现趋势措辞: {word}"
    assert "当前暂无上一期可比数据" in report
