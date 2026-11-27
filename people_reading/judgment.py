"""判断卡（第 4 节，所有入口通用）与两条时间线。

卡身固定三行，顺序不可调换：实际发生 / 事前预测 / 回忆材料。
只有“事前预测”行计入累计；回忆材料单独展示（R10）；不对记忆可靠性下结论。
"""
from __future__ import annotations

from typing import Callable, Optional

from .cards import FORBIDDEN, prediction_row
from .world import World

ROWS = ("实际发生", "事前预测", "回忆材料")
MEMORY_NOTE = "一次落空不足以评价记忆总体是否可靠，系统不对记忆可靠性下结论。"
ACTIONS = ("点数字就地展开原话", "继续分析", "查看时间线")


def _quote(store, span) -> str:
    if isinstance(span, dict) and store is not None and "start" in span:
        return store.quote(span)
    return str(span)


def _rows_of(w: World, hid: str) -> list:
    return sorted([(k, r) for k, r in w.engine.rows.items() if hid in (k[2], k[3])], key=lambda kr: kr[1].updated_at)


def _recalls(w: World, hid: str) -> list:
    byid = {o.id: o for o in w.observations.values()}
    return [byid[o] for o, h in w.recall_links if h == hid and o in byid]


def judgment_card(w: World, hid: str, store=None, label_time: Callable[[int], str] = lambda t: "今天") -> dict:
    h = w.hypotheses[hid]
    eng = w.engine
    rows = _rows_of(w, hid)
    recalls = _recalls(w, hid)
    by = "你的判断" if h.proposed_by == "user" else "模型提出"
    no_support = not rows and not recalls
    name = w.persons[h.person_id].display_name if h.person_id in w.persons else h.person_id

    # 行 1：实际发生
    actual = {"label": ROWS[0], "text": "尚无相关结果", "quotes": [], "counts": None}
    settled = [(k, r) for k, r in rows if r.status in ("counted", "below_threshold", "recall_only")]
    if settled:
        k, r = settled[-1]
        src = eng.preds[r.source_prediction_id]
        beh = eng.behaviors[(r.behavior_key, src.pre.def_version)]
        rec = eng.outcomes[(k[0], r.behavior_key, src.pre.def_version)]
        label = rec.outcome if beh.outcome_type == "categorical" else (beh.event_label if rec.outcome else f"没有{beh.event_label}")
        actual.update(text=f"{name}的{beh.description}归入“{label}”。" if beh.outcome_type == "categorical"
                      else f"{name}：{label}。", quotes=[_quote(store, s) for s in rec.source_spans])
    # 行 2：事前预测（唯一计入累计的一行）
    pre = {"label": ROWS[1], "text": "尚无事前预测" if not h.formal else "已形成规则，等待结果", "counts": True}
    if rows:
        k, r = rows[-1]
        pre["text"] = prediction_row(eng, k[0], k[2], k[3])
    # 行 3：回忆材料（单独展示，不计入）
    rec_row = {"label": ROWS[2], "counts": False, "items": [
        f"{label_time(o.recorded_at)}你回忆：{o.content}。它支持“{h.statement}”，未计入比较。" for o in recalls],
        "text": ""}
    rec_row["text"] = "\n".join(rec_row["items"]) or "无"
    # 证据徽标：同一事件只算一次
    inter = {o.interaction_id for o in recalls}
    inter |= {k[0] for k, _ in rows}
    recalled_i = {o.interaction_id for o in recalls}
    k_rec = len([i for i in inter if i in recalled_i])
    badge = f"关联 {len(inter)} 次互动" + (f" · 其中 {k_rec} 次为事后回忆" if k_rec else "")
    # 仍待区分：同一行为下的其他规则、待办里的观察项
    rivals = [r.name for rid, r in eng.rules.items() if rid != hid and r.behavior_key == h.behavior_key and h.behavior_key]
    pending = [f"替代解释：{n}" for n in rivals] + [f.text for f in w.followups if f.kind == "watch" and f.status != "done"]
    card = {"id": hid, "header": {"statement": h.statement, "by": by,
                                  "note": ("关系已变，需复核" if h.status == "needs_review" else "尚无互动支持" if no_support else ""), "css": "user" if h.proposed_by == "user" else "model",
                                  "formal": h.formal},
            "rows": [actual, pre, rec_row], "badge": badge, "pending": pending, "actions": list(ACTIONS)}
    assert [r["label"] for r in card["rows"]] == list(ROWS)
    assert not any(wd in x["text"] for x in card["rows"] for wd in FORBIDDEN)
    return card


def event_timeline(w: World, person_id: str, label_time: Callable[[int], str] = lambda t: f"t{t}") -> list:
    out = []
    for iid, it in w.interactions.items():
        obs = [o for o in w.observations.values() if o.interaction_id == iid and person_id in (o.subject_person_id, o.reporter_id)]
        if not obs and it.get("status") != "planned":
            continue
        modes = {o.source_mode for o in obs}
        mode = "计划中" if not obs else ("事后回忆" if modes == {"recalled"} else "当时记录")
        out.append({"interaction": iid, "label": it.get("label", iid), "status": it.get("status"), "mode": mode,
                    "recorded_at": min((o.recorded_at for o in obs), default=None),
                    "text": f"{it.get('label', iid)}（{mode}" + (f"，于 {label_time(min(o.recorded_at for o in obs))} 记录）" if obs else "）")})
    return sorted(out, key=lambda e: (e["recorded_at"] is None, e["recorded_at"] or 0))


def judgment_timeline(w: World, hid: str, label_time: Callable[[int], str] = lambda t: f"t{t}") -> dict:
    h = w.hypotheses[hid]
    events = []
    before = [o for o in w.observations.values() if h.person_id in (o.subject_person_id,) and o.recorded_at < h.formed_at]
    events.append({"at": h.formed_at, "kind": "formed",
                   "text": f"{label_time(h.formed_at)} 形成判断“{h.statement}”（{'你' if h.proposed_by == 'user' else '模型'}提出），"
                           + ("当时依据：" + "；".join(o.content for o in before) if before else "当时没有相关材料")})
    for o in _recalls(w, hid):
        events.append({"at": o.recorded_at, "kind": "material",
                       "text": f"{label_time(o.recorded_at)} 回忆材料支持该判断：{o.content}（事后回忆，未计入比较）"})
    for k, r in _rows_of(w, hid):
        if r.status in ("counted", "below_threshold"):
            events.append({"at": r.updated_at, "kind": "outcome", "text": f"{label_time(r.updated_at)} 实际结果“{r.outcome}”：" + prediction_row(w.engine, k[0], k[2], k[3])})
    events.sort(key=lambda e: e["at"])
    return {"events": events, "note": MEMORY_NOTE}
