"""Skill（第 5 节）：每个 Skill 定义五项，带版本；每次解读都显示名称与版本并保存为 interpretation。

竞争解释 v1：
  读取  人物相关的互动、机会统计、假设组与 pair_evidence、待办(watch)、三维对照、议题角色
  输出  已有解释 / 累计倍数与次数 / 尚未排除的解释 / 下次值得观察的信号
  可建议 新建规则、watch_item、角色或关系边、预测（均为草稿，本 Skill 不直接写入）

R7：证据包里的数字全部由程序计算；模型只写一段叙述，叙述里出现的数字必须都在证据包里，
否则整段丢弃，改用程序模板。R12：叙述不得出现越界表述。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from .cards import FORBIDDEN
from .stats import MIN_KNOWN, dim_a, dim_b, dim_c, known_opps, rate
from .world import SELF, World


@dataclass(frozen=True)
class SkillVersion:
    skill: str
    version: int
    description: str
    applicable_questions: tuple
    inputs: tuple
    steps: tuple
    output_format: str
    allowed_actions: tuple

    @property
    def id(self) -> str:
        return f"{self.skill}@v{self.version}"


COMPETING = SkillVersion(
    "竞争解释", 1, "对同一个人的行为，并列比较几种解释，写明还分不清什么。",
    applicable_questions=("他是不是只对我这样", "为什么他总是", "他这样是针对我吗", "分析一下他"),
    inputs=("人物相关的互动", "机会统计", "假设组与 pair_evidence", "watch_item", "三维对照", "议题角色"),
    steps=("统计该人物在已知机会里的发生率", "取三维对照并判断哪一侧样本不足", "汇总规则对的累计倍数与有区分力的互动次数",
           "列出尚未排除的解释与下次值得观察的信号"),
    output_format="已有解释；累计倍数和次数；尚未排除的解释；下次值得观察的信号",
    allowed_actions=("新建规则草稿", "watch_item 草稿", "角色或关系边草稿", "预测草稿"),
)
BUILTIN = {COMPETING.skill: COMPETING}


def _register_prep() -> None:  # 避免循环导入：premeeting 依赖 SkillVersion
    from .premeeting import PREP
    BUILTIN[PREP.skill] = PREP


class SkillRegistry:
    def __init__(self, store=None) -> None:
        _register_prep()
        self.versions: dict[str, SkillVersion] = dict(BUILTIN)
        self.enabled: dict[str, bool] = {n: True for n in BUILTIN}
        self.store = store
        if store:
            for sv in BUILTIN.values():
                store.save_entity("skill_version", sv.id, {**sv.__dict__})
                store.save_entity("skill", sv.skill, {"name": sv.skill, "description": sv.description, "enabled": True})
            store.db.commit()

    def get(self, name: str) -> Optional[SkillVersion]:
        return self.versions.get(name) if self.enabled.get(name) else None

    def select(self, text: str) -> Optional[SkillVersion]:
        """点名调用优先（“用竞争解释分析一下”）；否则按适用问题关键词自选。没有合适的返回 None。"""
        for name, sv in self.versions.items():
            if self.enabled.get(name) and name in text:
                return sv
        for name, sv in self.versions.items():
            if self.enabled.get(name) and any(q in text for q in sv.applicable_questions):
                return sv
        return None


# ───────── 证据包（全部由程序计算） ─────────
def _name(w: World, pid: str) -> str:
    return w.persons[pid].display_name if pid in w.persons else pid


def build_packet(w: World, person_id: str, *, recent_days: int = 10) -> dict:
    opps = [s for s in w.stat_opps.values() if s.actor_id == person_id]
    kn = known_opps(w, person_id)
    overall = rate(w, kn)
    days = [s.day for s in kn]
    recent_from = (max(days) - recent_days + 1) if days else 0
    da, db, dc = dim_a(w, person_id), dim_b(w, person_id), dim_c(w, person_id, recent_from)
    excluded = [s.id for s in opps if s.responsible_named is None]
    interactions = {o.interaction_id for o in w.observations.values() if o.subject_person_id == person_id}
    interactions |= {s.interaction_id for s in opps}
    badge = w.evidence_badge(person_id)
    # 规则对：只取该人物的预测所冻结的规则
    rule_ids = sorted({r for p in w.engine.preds.values() if p.pre.person_id == person_id for r in p.rule_snaps})
    pairs = []
    for i, a in enumerate(rule_ids):
        for b in rule_ids[i + 1:]:
            c = w.engine.cumulative(a, b)
            if c["n"]:
                hi, lo, k = (b, a, c["multiple"]) if c["multiple"] >= 1 else (a, b, round(1 / c["multiple"], 9))
                pairs.append({"hi": hi, "lo": lo, "hi_name": w.engine.rules[hi].name, "lo_name": w.engine.rules[lo].name,
                              "multiple": k, "n": c["n"], "opps": c["opportunities"]})
    recall_only = sum(1 for r in w.engine.rows.values() if r.status == "recall_only")
    followups = [f for f in w.followups if f.status == "open" and f.target_ref in {s.id for s in opps}]
    roles = [{"matter": w.matters[r.matter_id].title if r.matter_id in w.matters else r.matter_id, "role": r.role,
              "dimension": r.dimension, "status": r.status} for r in w.roles.values() if r.person_id == person_id]
    return {"person": person_id, "name": _name(w, person_id), "min_known": MIN_KNOWN,
            "overall": overall, "dim_a": da, "dim_b": db, "dim_c": dc, "excluded_unknown_opps": excluded,
            "interactions": sorted(interactions), "badge": badge, "pairs": pairs, "recall_only_rows": recall_only,
            "followups": [{"id": f.id, "text": f.text, "target": f.target_ref} for f in followups], "roles": roles,
            "cited_opps": sorted(s.id for s in opps),
            "cited_pairs": sorted({(k[0], k[2], k[3]) for k, r in w.engine.rows.items()
                                   if r.status != "not_evaluable" and k[2] in rule_ids and k[3] in rule_ids})}


def _signals(packet: dict) -> list:
    """下次值得观察的信号：只写看什么、能分清什么、代价；零代价的观察信号优先。"""
    out = []
    a, b, c = packet["dim_a"], packet["dim_b"], packet["dim_c"]
    n = packet["name"]
    if a["state"] == "待区分":
        need = max(0, MIN_KNOWN - a["peers"]["known"])
        out.append({"kind": "观察信号", "cost": 0, "separates": "“只对我这样”与“对同级也这样”",
                    "text": f"观察 {n} 对同级提交的（材料未写负责人的）提案是否追问；同级一侧还差 {need} 次已知机会"})
    if b["state"] == "待区分":
        need = max(0, MIN_KNOWN - b["others_to_me"]["known"])
        out.append({"kind": "观察信号", "cost": 0, "separates": "“针对我”与“针对这类事”",
                    "text": f"观察其他人向我提交提案时 {n} 的反应；这一侧还差 {need} 次已知机会"})
    if c["state"] == "待区分":
        out.append({"kind": "观察信号", "cost": 0, "separates": "“一贯如此”与“最近才这样”",
                    "text": f"继续记录 {n} 的后续互动，让“近期 / 过去”两侧各达到 {MIN_KNOWN} 次已知机会"})
    for f in packet["followups"]:
        out.append({"kind": "补充条件", "cost": 0, "separates": "能否把这次机会计入统计", "text": f["text"]})
    return out


def _unresolved(packet: dict) -> list:
    u = []
    for key, label in (("dim_a", "A 维（对我 vs 对同级）"), ("dim_b", "B 维（对我 vs 其他人对我）"),
                       ("dim_c", "C 维（近期 vs 过去）")):
        if packet[key]["state"] == "待区分":
            u.append(f"{label}：{packet[key]['text'].replace('待区分：', '')}，暂时分不清")
    if packet["excluded_unknown_opps"]:
        u.append(f"{len(packet['excluded_unknown_opps'])} 次机会因“材料是否写了负责人”未知，没有计入统计")
    return u


def template_summary(packet: dict) -> str:
    o, a = packet["overall"], packet["dim_a"]
    s = f"在 {o['known']} 次已知机会里，{packet['name']} 追问了 {o['true']} 次（{o['text']}）。"
    if a["state"] == "待区分":
        s += f"对我 {a['me']['text']}，对同级 {a['peers']['text']}。{a['text'].replace('待区分：', '')}，" \
             "目前分不清“只对我这样”和“对谁都这样”。"
    else:
        s += f"对我 {a['me']['text']}，对同级 {a['peers']['text']}（开放比较，不自动归因）。"
    return s


NUM = re.compile(r"\d+(?:\.\d+)?(?:/\d+)?%?")


def numbers_ok(text: str, packet: dict) -> bool:
    """R7：叙述里出现的数字必须都能在证据包里找到（含 a/b 形式）。"""
    allowed = set(NUM.findall(json.dumps(packet, ensure_ascii=False, default=str)))
    allowed |= {str(i) for i in range(0, 11)}  # “一次”“两次”之类的小序数不算越界
    return all(t in allowed for t in NUM.findall(text)) and not any(w in text for w in FORBIDDEN)


def validate_narrative(d) -> list:
    if not isinstance(d, dict) or not isinstance(d.get("summary"), str) or not d["summary"].strip():
        return ["必须输出 {\"summary\": 非空字符串}"]
    return []


SYSTEM_NARRATE = """你在写“竞争解释”的一段总结。只输出 JSON：{"summary": "..."}。
要求：只用证据包里已有的数字；不得下“他就是针对你”之类的结论；样本不足的地方明说“分不清”；
不得出现“猜中”“证实”等词；不超过 120 字。"""


def run_competing_explanation(w: World, person_id: str, question: str, *, model=None, conversation_id: str = "",
                              store=None) -> dict:
    packet = build_packet(w, person_id)
    summary, by = template_summary(packet), "template"
    if model is not None:
        try:
            d = model.structured(SYSTEM_NARRATE, json.dumps({"question": question, "packet": packet},
                                                            ensure_ascii=False, default=str), validate_narrative)
            if numbers_ok(d["summary"], packet):
                summary, by = d["summary"].strip(), "model"
        except Exception:  # 模型不可用或输出无效：用程序模板，解读照常给出
            pass
    signals, unresolved = _signals(packet), _unresolved(packet)
    names = list(dict.fromkeys(n for p in packet["pairs"] for n in (p["lo_name"], p["hi_name"])))
    existing = names or ["尚无已锁定预测支持的解释"]
    cumulative = [f"实际结果在“{p['hi_name']}”下的事前概率，是在“{p['lo_name']}”下的 {p['multiple']:.4g} 倍，"
                  f"来自 {p['n']} 次有区分力的互动" for p in packet["pairs"]] or ["尚无有区分力的互动"]
    b = packet["badge"]
    lines = [f"【{COMPETING.id}】{summary}",
             "已有解释：" + "；".join(existing),
             "累计倍数和次数：" + "；".join(cumulative),
             "尚未排除：" + ("；".join(unresolved) if unresolved else "无"),
             "下次值得观察：" + ("；".join(f"{s['text']}（{s['kind']}，代价 {s['cost']}）" for s in signals) if signals else "无"),
             f"依据：{b['text']}；统计口径：材料未写负责人的提案，共 {packet['overall']['known']} 次已知机会"]
    text = "\n".join(lines)
    open_questions = unresolved + [s["text"] for s in signals if s["kind"] == "补充条件"]
    iid = w.save_interpretation(question, text, cited_opps=packet["cited_opps"],
                                cited_pairs=packet["cited_pairs"], open_questions=open_questions,
                                skill_version_id=COMPETING.id, conversation_id=conversation_id,
                                person_ids=[person_id], narrated_by=by)
    if store:
        store.sync_world(w)
    return {"interpretation_id": iid, "text": text, "packet": packet, "signals": signals,
            "open_questions": open_questions, "narrated_by": by, "skill_version": COMPETING.id}
