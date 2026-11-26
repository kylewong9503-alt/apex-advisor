"""判断卡“事前预测”行与结算首屏的文案（第 4 节、R12）。文案全部由模板生成，不经模型。"""
from __future__ import annotations

from .engine import Engine

FIELD_LABELS = {"submitter": "提交人", "cross_dept": "是否跨部门", "responsible_named": "是否写明负责人"}
FORBIDDEN = ("猜中", "证实", "获胜", "都没预料到", "都错了", "模型对了", "你猜错")


def _num(x: float) -> str:
    return f"{x:.4g}"


def settlement_screen(matter_title: str, meeting_time: str, question: str = "他最先问了什么？") -> dict:
    """3.5 第 2 步：首份报告提交前，页面只有这四项；判断卡、预测、候选答案一律不出现。"""
    return {"matter": matter_title, "meeting_time": meeting_time, "open_question": question, "input_box": True}


def prediction_row(eng: Engine, opp_id: str, h1: str, h2: str) -> str:
    a, b = sorted((h1, h2))
    row = next(r for k, r in eng.rows.items() if k[0] == opp_id and k[2:] == (a, b))
    r1, r2 = eng.rules[a], eng.rules[b]
    beh = eng.behaviors[(row.behavior_key, eng.preds[row.source_prediction_id].pre.def_version)] \
        if row.source_prediction_id else None
    binary = beh is not None and beh.outcome_type == "binary"
    label = row.outcome if not binary else (beh.event_label if row.outcome else f"没有{beh.event_label}")
    if row.status == "recall_only":
        return "实际结果仅有事后回忆支撑，未计入比较。"
    if row.status == "not_evaluable":
        return "没有可完整评估这两条规则的预测，未计入比较。"
    thr = eng.preds[row.source_prediction_id].pre.threshold
    if row.status == "below_threshold":
        z = abs(row.p1 - row.p2) * 100
        what = f"实际首问归入“{row.outcome}”" if not binary else f"实际结果是“{label}”"
        text = (f"{what}。两条规则事前分别给这一类 {_num(row.p1 * 100)}% 和 {_num(row.p2 * 100)}%，"
                f"相差 {_num(z)} 个百分点，未达到 {_num(thr * 100)} 个百分点的比较门槛。")
    else:
        hi, lo = (r2, r1) if row.p2 >= row.p1 else (r1, r2)  # 分子取事前概率较高的规则
        k = row.lr if row.p2 >= row.p1 else 1 / row.lr
        cum = eng.cumulative(lo.id, hi.id)
        text = (f"这次结果（{label}）在“{hi.name}”下的事前概率，是在“{lo.name}”下的 {_num(k)} 倍；"
                f"累计 {_num(cum['multiple'])} 倍，来自 {cum['n']} 次有区分力的互动。")
        if binary and not row.outcome:
            text += f"这次只说明没有发生“{beh.event_label}”。"
    if row.facts_basis == "corrected":
        field = next((r.subject.split(".")[-1] for r in eng.revisions if r.kind == "fact_correction"
                      and r.subject.startswith(opp_id + ".")), "")
        text += f"{FIELD_LABELS.get(field, field)}已更正，比较已重算。"
    assert not any(w in text for w in FORBIDDEN)
    return text
