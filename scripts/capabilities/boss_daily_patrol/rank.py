"""全店客户机会排序与日报渲染。只使用已确认字段，不推断负责人或私域进度。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional


LEVEL_RANK = {"高": 3, "中": 2, "低": 1}

HIGH_WORDS = ("高", "较高", "高价值")
RECENT_INQ_DAYS = 7


def _text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _is_high(value) -> bool:
    text = _text(value)
    return any(word in text for word in HIGH_WORDS)


def _parse_date(value) -> Optional[datetime]:
    text = _text(value)
    if not text or text in {"-", "—", "暂无", "无"}:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text[: len(fmt)], fmt)
        except ValueError:
            continue
    match = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return None


def _days_since(value, today: datetime) -> Optional[int]:
    parsed = _parse_date(value)
    if not parsed:
        return None
    return (today.date() - parsed.date()).days


def primary_type(tags: list) -> str:
    order = ["询盘未成交", "周期采购", "流失风险", "老客促活", "高价值老客"]
    for name in order:
        if name in tags:
            return name
    return tags[0] if tags else "其他"


def score_buyer(buyer: dict, today: datetime) -> dict:
    """根据已确认字段打机会等级。历史金额高本身不算高机会。"""
    tags = list(dict.fromkeys(buyer.get("tags") or []))
    gmv_high = _is_high(buyer.get("gmv_1m_level"))
    ord_high = _is_high(buyer.get("ord_cnt_1m_level"))
    is_ka = _text(buyer.get("if_ka")) in {"是", "Y", "y", "1", "true", "True", "KA"}
    inq_days = _days_since(buyer.get("lst_inq_time"), today)
    recent_inq = inq_days is not None and 0 <= inq_days <= RECENT_INQ_DAYS
    has_inq = bool(_text(buyer.get("lst_inq_time"))) or bool(_text(buyer.get("inq_relation")))
    advice = _text(buyer.get("follow_suggestion"))
    profile = _text(buyer.get("buyer_profile"))

    reasons = []
    priority = 6
    if recent_inq and ("询盘未成交" in tags or has_inq):
        priority = 1
        reasons.append(f"最近询盘在{inq_days}天内，平台侧仍可见咨询痕迹")
    elif "询盘未成交" in tags and has_inq:
        priority = 2
        reasons.append("有询盘记录，平台侧暂未看到对应成交")
    elif (gmv_high or ord_high or is_ka) and ("老客促活" in tags or "流失风险" in tags):
        priority = 3
        reasons.append("历史采购表现偏高，但近期进入沉默或流失关注名单")
    elif "周期采购" in tags:
        priority = 4
        reasons.append("被归入周期采购客群，可能接近补货窗口")
    elif "流失风险" in tags:
        priority = 5
        reasons.append("进入流失关注名单，需核实是否只是转到其他渠道")
    elif "老客促活" in tags:
        priority = 5
        reasons.append("老客近期活跃度下降，适合核实是否还有原需求")

    evidence = 0
    if recent_inq:
        evidence += 2
    if "询盘未成交" in tags:
        evidence += 2
    if "周期采购" in tags:
        evidence += 1
    if gmv_high or ord_high or is_ka:
        evidence += 1
    if advice or profile:
        evidence += 1

    if priority <= 2 and evidence >= 3:
        level = "高"
    elif priority <= 4 and evidence >= 2:
        level = "中"
    elif priority <= 5 and (gmv_high or ord_high or is_ka or recent_inq):
        level = "中"
    else:
        level = "低"

    # 只有历史金额高、没有当前信号，不能升到高。
    if level == "高" and not recent_inq and "询盘未成交" not in tags and "周期采购" not in tags:
        level = "中"

    if not reasons:
        reasons.append("当前仅有客群归类，缺少足够的近期互动证据")

    buyer = dict(buyer)
    buyer["tags"] = tags
    buyer["priority"] = priority
    buyer["level"] = level
    buyer["reason"] = reasons[0]
    buyer["evidence"] = evidence
    buyer["recent_inq"] = recent_inq
    buyer["inq_days"] = inq_days
    buyer["gmv_high"] = gmv_high
    buyer["ord_high"] = ord_high
    buyer["is_ka"] = is_ka
    return buyer


def apply_antispam(buyer: dict, previous: Optional[dict]) -> dict:
    buyer = dict(buyer)
    buyer["antispam"] = False
    if not previous:
        return buyer
    same_level = previous.get("level") == buyer.get("level")
    same_tags = set(previous.get("tags") or []) == set(buyer.get("tags") or [])
    yesterday_contact = previous.get("follow_time") in {"今天", "1-2天后"}
    if same_level and same_tags and yesterday_contact and not buyer.get("recent_inq"):
        buyer["antispam"] = True
        buyer["reason"] = "昨日已建议联系，今日暂无新变化，先观察"
        if buyer["level"] == "高":
            buyer["level"] = "中"
    return buyer


def management_advice(buyer: dict) -> str:
    if buyer.get("antispam"):
        return "暂时观察"
    if buyer.get("level") == "低":
        return "暂时观察"
    important = buyer.get("is_ka") or buyer.get("gmv_high") or "流失风险" in buyer.get("tags", [])
    if buyer.get("level") == "高" and important:
        return "老板重点过问"
    if buyer.get("level") in {"高", "中"}:
        return "业务员继续跟进"
    return "暂无法判断是否需要老板介入"


def next_action(buyer: dict) -> tuple:
    tags = buyer.get("tags") or []
    if buyer.get("antispam"):
        return "先不要重复联系，改问负责同事昨天联系后的真实反馈", "暂不联系"
    if "询盘未成交" in tags:
        return "今天核实是否已转微信、电话或其他渠道，并确认项目是否还在推进", "今天"
    if "周期采购" in tags:
        return "今天按原采购周期确认一次是否有补货计划，不要连续催单", "今天"
    if "流失风险" in tags:
        return "先确认客户是否已换供应商或项目暂停，再决定是否做挽回", "1-2天后"
    if "老客促活" in tags:
        return "先确认是否还需要原规格，不要直接催订单", "1-2天后"
    if buyer.get("level") == "低":
        return "本周不主动打扰，等出现新询盘或复购信号再联系", "暂不联系"
    return "建议负责同事今天主动确认一次当前需求，不默认客户没有进展", "今天"


def reference_script(buyer: dict) -> str:
    custom = _text(buyer.get("follow_suggestion"))
    if custom:
        return custom
    tags = buyer.get("tags") or []
    if "周期采购" in tags:
        return "您好，之前您这边采购的产品最近使用情况怎么样？按照之前的采购周期，想跟您确认一下近期是否有补货计划。"
    if "询盘未成交" in tags:
        return "您好，上次您咨询的需求我再跟进一下。目前项目有没有确定下来？如果规格或交期需要调整，我这边可以继续配合。"
    if "流失风险" in tags or "老客促活" in tags:
        return "您好，好久没联系了。想确认一下您之前用的规格最近还有没有需求，如果项目有变化也可以直接告诉我。"
    return ""


def confirmed_lines(buyer: dict) -> list:
    lines = []
    if buyer.get("tags"):
        lines.append("客群标签：" + "、".join(buyer["tags"]))
    if _text(buyer.get("lst_inq_time")):
        lines.append("最近询盘时间：" + _text(buyer.get("lst_inq_time")))
    if _text(buyer.get("inq_relation")):
        lines.append("询盘关系：" + _text(buyer.get("inq_relation")))
    if _text(buyer.get("gmv_1m_level")):
        lines.append("近1月成交金额档：" + _text(buyer.get("gmv_1m_level")))
    if _text(buyer.get("ord_cnt_1m_level")):
        lines.append("近1月下单频次档：" + _text(buyer.get("ord_cnt_1m_level")))
    if _text(buyer.get("if_ka")):
        lines.append("重点客户标记：" + _text(buyer.get("if_ka")))
    if _text(buyer.get("buyer_profile")):
        lines.append("画像摘要：" + _text(buyer.get("buyer_profile")))
    if not lines:
        lines.append("当前数据源只返回了买家账号，其余经营字段暂未提供")
    lines.append("负责人：当前数据源未提供负责人信息")
    return lines


def needs_private_reminder(buyer: dict) -> bool:
    return "询盘未成交" in (buyer.get("tags") or []) or bool(_text(buyer.get("lst_inq_time")))


def select_top(buyers: list, limit: int = 10) -> tuple:
    eligible = [b for b in buyers if b.get("level") in {"高", "中"}]
    eligible.sort(key=lambda b: (LEVEL_RANK.get(b.get("level"), 0), -b.get("priority", 9), b.get("evidence", 0)), reverse=True)
    top = eligible[:limit]
    rest = max(len(eligible) - len(top), 0)
    return top, rest


def build_buyer_view(buyer: dict) -> dict:
    action, follow_time = next_action(buyer)
    advice = management_advice(buyer)
    return {
        "login_id": buyer.get("login_id") or "",
        "level": buyer.get("level") or "低",
        "type": primary_type(buyer.get("tags") or []),
        "tags": buyer.get("tags") or [],
        "reason": buyer.get("reason") or "",
        "confirmed": confirmed_lines(buyer),
        "advice": advice,
        "action": action,
        "follow_time": follow_time,
        "script": reference_script(buyer),
        "private_reminder": needs_private_reminder(buyer),
        "antispam": bool(buyer.get("antispam")),
    }


def compare_history(today_top: list, previous: Optional[dict]) -> dict:
    if not previous or not previous.get("top"):
        return {"available": False}
    prev_map = {item.get("login_id"): item for item in previous.get("top") or []}
    today_map = {item.get("login_id"): item for item in today_top}
    new_ids = [i for i in today_map if i not in prev_map]
    left_ids = [i for i in prev_map if i not in today_map]
    upgraded, downgraded = [], []
    for login_id, item in today_map.items():
        old = prev_map.get(login_id)
        if not old:
            continue
        if LEVEL_RANK.get(item.get("level"), 0) > LEVEL_RANK.get(old.get("level"), 0):
            upgraded.append(login_id)
        elif LEVEL_RANK.get(item.get("level"), 0) < LEVEL_RANK.get(old.get("level"), 0):
            downgraded.append(login_id)
    return {
        "available": True,
        "previous_date": previous.get("date") or "",
        "new": new_ids,
        "left": left_ids,
        "upgraded": upgraded,
        "downgraded": downgraded,
    }


def _section(title: str, rows: list, empty: str) -> list:
    lines = [f"## {title}", ""]
    if not rows:
        lines += [empty, ""]
        return lines
    for row in rows:
        lines.append(f"- {row}")
    lines.append("")
    return lines


def render_report(date_text: str, top: list, extra_count: int, coverage: dict, history: dict, errors: list) -> str:
    views = [build_buyer_view(b) for b in top]
    boss = [v for v in views if v["advice"] == "老板重点过问"]
    staff = [v for v in views if v["advice"] == "业务员继续跟进"]
    repurchase = [v for v in views if "周期采购" in v["tags"]]
    churn = [v for v in views if "流失风险" in v["tags"]]
    inquiry = [v for v in views if "询盘未成交" in v["tags"]]

    focus = "今天暂无足够证据进入重点名单的客户"
    if views:
        focus = f"{views[0]['login_id']}（{views[0]['type']}，{views[0]['reason']}）"

    lines = [
        "# 1688 全店客户机会日报",
        "",
        f"日期：{date_text}",
        "",
        "## 一、今日老板摘要",
        "",
        f"- 今日重点客户：{len(views)} 位",
        f"- 老板建议重点过问：{len(boss)} 位",
        f"- 建议业务员今天重点跟进：{len(staff)} 位",
        f"- 进入复购窗口：{len(repurchase)} 位",
        f"- 流失风险：{len(churn)} 位",
        f"- 询盘未成交重点机会：{len(inquiry)} 位",
        "",
        f"今天最值得关注的是{focus}。",
        "",
    ]
    if extra_count:
        lines += [f"另外还有 {extra_count} 位中等机会客户未列入前十。", ""]
    if len(views) < 10:
        lines += [f"今日符合高价值条件客户共 {len(views)} 位。", ""]
    if errors:
        lines += ["今日部分客户数据查询失败，以下只根据已成功返回的数据判断。", ""]

    lines += ["## 二、今日最值得关注 Top 10", ""]
    if not views:
        lines += ["今日暂无符合重点条件的客户。", ""]
    for idx, view in enumerate(views, 1):
        lines += [
            f"### {idx}. {view['login_id']}",
            "",
            f"机会等级：{view['level']}",
            "",
            f"客户类型：{view['type']}",
            "",
            "为什么现在值得关注：",
            view["reason"],
            "",
            "当前可确认情况：",
        ]
        lines += [f"- {item}" for item in view["confirmed"]]
        lines += [
            "",
            f"管理建议：{view['advice']}",
            "",
            "下一步动作：",
            view["action"],
            "",
            f"建议跟进时间：{view['follow_time']}",
            "",
        ]
        if view["script"]:
            lines += ["参考话术：", view["script"], ""]
        if view["private_reminder"]:
            lines += ["提醒：1688平台暂未看到成交记录，建议核实是否已转微信、电话或其他渠道继续跟进。", ""]

    lines += ["## 三、需要老板重点过问的客户", ""]
    if not boss:
        lines += ["今日暂无需要老板重点过问的客户。", ""]
    for view in boss:
        lines += [
            f"- {view['login_id']}：{view['reason']}。建议老板询问负责同事当前真实进度和卡点，不要直接接管客户。",
        ]
    if boss:
        lines.append("")

    lines += ["## 四、今天建议业务员重点跟进", ""]
    if not staff:
        lines += ["今日暂无适合今天由业务员重点跟进的客户。", ""]
    for view in staff:
        lines.append(f"- {view['login_id']}：{view['action']}。话术：{view['script'] or '暂无足够信息生成话术'}")
    if staff:
        lines.append("")

    lines += _section(
        "五、复购机会",
        [f"{v['login_id']}：{v['reason']}。动作：{v['action']}" for v in repurchase],
        "今日暂无明显新增复购机会。",
    )
    lines += _section(
        "六、流失风险",
        [f"{v['login_id']}：{v['reason']}。动作：{v['action']}" for v in churn],
        "今日暂无明显高价值流失风险客户。",
    )
    lines += ["## 七、询盘未成交机会", ""]
    if not inquiry:
        lines += ["今日暂无询盘未成交重点机会。", ""]
    else:
        lines.append("1688平台未成交不代表客户一定没有在私域推进。")
        lines.append("")
        for view in inquiry:
            lines.append(f"- {view['login_id']}：{view['reason']}。动作：{view['action']}")
        lines.append("")

    lines += ["## 八、相比上一期的变化", ""]
    if not history.get("available"):
        lines += ["当前暂无上一期可比数据，本次不做趋势判断。", ""]
    else:
        prev = history.get("previous_date") or "上一期"
        lines += [
            f"- 对比日期：{prev}",
            f"- 新增重点客户：{'、'.join(history.get('new') or []) or '无'}",
            f"- 退出重点名单：{'、'.join(history.get('left') or []) or '无'}",
            f"- 机会升级：{'、'.join(history.get('upgraded') or []) or '无'}",
            f"- 机会下降：{'、'.join(history.get('downgraded') or []) or '无'}",
            "",
        ]

    lines += [
        "## 数据覆盖",
        "",
        f"- 已汇总客群：{coverage.get('cluster_count', 0)} 个",
        f"- 去重后买家：{coverage.get('unique_buyers', 0)} 位",
        f"- 画像建议成功返回：{coverage.get('advice_count', 0)} 位",
        "- 无法从当前1688数据判断业务员是否已经线下跟进。",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def snapshot_from_top(date_text: str, top: list) -> dict:
    items = []
    for buyer in top:
        view = build_buyer_view(buyer)
        items.append({
            "login_id": view["login_id"],
            "level": view["level"],
            "tags": view["tags"],
            "follow_time": view["follow_time"],
        })
    return {"date": date_text, "top": items}
