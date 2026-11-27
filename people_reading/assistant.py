"""对话编排：在 Intake 之上加三个确定性入口（先于模型，可测）——
  · 调用 Skill：点名（“用竞争解释分析一下”）或按适用问题自选
  · 回忆：“上次还差什么信息？” → open_questions 与未完成的待办
  · 事实更正：“那份提案是丁提交的” → correction_preview → 用户确认后才写入，并重算
其余消息交给 Intake 录入。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .intake import Intake, Reply
from .skills import COMPETING, SkillRegistry, run_competing_explanation
from .stats import dim_a, dim_b
from .world import SELF

RE_RECALL = re.compile(r"上次.{0,4}还差什么|还差什么信息|还缺什么信息")
RE_CORRECT = re.compile(r"那(?:份|个|次)?(?:提案|材料)?.*?是(.{1,8}?)提交的")


@dataclass
class AReply:
    text: str
    kind: str  # record | interpretation | recall | correction_preview | correction_applied | ask
    interpretation_id: Optional[str] = None
    preview_id: Optional[str] = None
    intake: Optional[Reply] = None


def correction_preview_full(w, opp_id: str, field_name: str, value) -> dict:
    """3.4：列出受影响的统计、比较、预测、解读。只读，不写入。"""
    eng = w.engine.correction_preview(opp_id, field_name, value)
    s = w.stat_opps[opp_id]

    def dims():
        return {"A": dim_a(w, s.actor_id)["text"], "B": dim_b(w, s.actor_id)["text"]}
    before, old = dims(), s.target_id
    if field_name == "submitter":
        s.target_id = value
    try:
        after = dims()
    finally:
        s.target_id = old
    return {**eng, "stats": {k: {"before": before[k], "after": after[k]} for k in before if before[k] != after[k]},
            "interpretations": [i for i, d in w.interpretations.items() if opp_id in d["cited_opps"]]}


class Assistant:
    def __init__(self, intake: Intake, skills: SkillRegistry) -> None:
        self.intake, self.skills = intake, skills
        self.previews: dict[str, dict] = {}
        self.premeetings: dict[str, object] = {}

    @property
    def w(self):
        return self.intake.world

    def _say(self, cid: str, text: str, card: str = "") -> None:
        self.intake.store.add_message(cid, "card" if card else "assistant", text, card, now=self.intake.clock())

    def _person_in(self, cid: str, text: str) -> Optional[str]:
        for name in sorted(self.intake.aliases, key=len, reverse=True):
            ids = self.intake.aliases[name]
            if name in text and len(ids) == 1:
                return ids[0]
        return self.intake.focus.get(cid)  # “他”→ 当前在聊的人

    def handle(self, cid: str, text: str, *, model=None) -> AReply:
        if RE_RECALL.search(text):
            return self._recall(cid, text)
        m = RE_CORRECT.search(text)
        if m:
            return self._correct(cid, text, m.group(1).strip())
        sv = self.skills.select(text)
        if sv is not None:
            return self._run_skill(cid, text, sv, model)
        r = self.intake.handle(cid, text)
        return AReply(r.text, "record", intake=r)

    # —— 调用 Skill ——
    def _run_skill(self, cid: str, text: str, sv, model) -> AReply:
        self.intake.store.add_message(cid, "user", text, now=self.intake.clock())
        if sv.skill == "会前准备":
            return self._prepare(cid, model)
        pid = self._person_in(cid, text)
        if pid is None:
            msg = "想分析哪一位？"
            self._say(cid, msg)
            return AReply(msg, "ask")
        res = run_competing_explanation(self.w, pid, text, model=model or self.intake.model, conversation_id=cid,
                                        store=self.intake.store)
        self._say(cid, res["text"], "interpretation")
        return AReply(res["text"], "interpretation", interpretation_id=res["interpretation_id"])

    def _prepare(self, cid: str, model) -> AReply:
        w = self.w
        planned = [(i, m) for i, m in w.interactions.items() if m.get("status") == "planned"]
        pid = self.intake.focus.get(cid) or (planned[-1][1].get("with") if planned else None)
        if not planned or not w.matters or pid is None:
            msg = "还没有议题或计划中的会谈，先说一下要谈什么。"
            self._say(cid, msg)
            return AReply(msg, "ask")
        from .premeeting import PreMeeting
        pm = PreMeeting(self.intake, list(w.matters)[-1], pid, planned[-1][0], model or self.intake.model)
        out = pm.prepare()
        self.premeetings[cid] = pm
        if out is None:
            msg = "暂时无法生成会前准备（模型不可用或输出无效），请手动填写。"
            self._say(cid, msg)
            return AReply(msg, "ask")
        msg = f"【会前准备@v1】先问你一件事：{out.fork_question['question']}\n" \
              f"若{out.fork_question['branch_a']['condition']}，建议{out.fork_question['branch_a']['prep']}；" \
              f"若{out.fork_question['branch_b']['condition']}，建议{out.fork_question['branch_b']['prep']}（也可以回答“记不清”）。"
        self._say(cid, msg, "prep")
        return AReply(msg, "prep")

    # —— 回忆 ——
    def _recall(self, cid: str, text: str) -> AReply:
        self.intake.store.add_message(cid, "user", text, now=self.intake.clock())
        mine = [(i, d) for i, d in self.w.interpretations.items() if d.get("conversation_id") == cid]
        if not mine:
            msg = "这段对话里还没有做过解读。"
            self._say(cid, msg)
            return AReply(msg, "recall")
        iid, d = mine[-1]
        lines = [f"上次（{d['skill_version_id']}，{iid}）还差这些信息："] + [f"· {q}" for q in d["open_questions"]]
        watch = [f.text for f in self.w.followups if f.status == "open"]
        lines += ["未完成的待办："] + [f"· {t}" for t in watch] if watch else []
        if self.w.interpretation_changed(iid):
            lines.append("注意：这次解读引用的数据已被更正，依据已变化，建议重新分析。")
        msg = "\n".join(lines)
        self._say(cid, msg, "followup")
        return AReply(msg, "recall", interpretation_id=iid)

    # —— 事实更正 ——
    def _correct(self, cid: str, text: str, name: str) -> AReply:
        mid = self.intake.store.add_message(cid, "user", text, now=self.intake.clock())
        iid = self.intake.last_iid.get(cid)
        oid = f"OPP-{iid}" if iid else None
        if oid not in self.w.stat_opps:
            msg = "你说的是哪一份提案？"
            self._say(cid, msg)
            return AReply(msg, "ask")
        kind, pid = self.intake.resolve(name)
        if kind not in ("self", "one"):
            msg = f"没找到“{name}”，是哪一位？"
            self._say(cid, msg)
            return AReply(msg, "ask")
        pv = correction_preview_full(self.w, oid, "submitter", pid)
        key = f"CP{len(self.previews) + 1}"
        self.previews[key] = {"opp": oid, "field": "submitter", "value": pid, "message_id": mid, "preview": pv}
        lines = [f"提交人更正预览：{oid} 的提交人 {pv['old']} → {pv['new']}。"]
        lines += [f"统计 {k} 维：{v['before']} → {v['after']}" for k, v in pv["stats"].items()] or ["统计：没有变化"]
        lines += [f"比较变化 {len(pv['comparison_changes'])} 处；预测标记 {len(pv['predictions_marked_lock_facts_corrected'])} 份"]
        if pv["interpretations"]:
            lines.append("引用了这份提案的解读：" + "、".join(pv["interpretations"]) + "（确认后将标“依据已变化”）")
        lines.append("确认后才写入。")
        msg = "\n".join(lines)
        self._say(cid, msg, "correction_preview")
        return AReply(msg, "correction_preview", preview_id=key)

    def confirm_correction(self, cid: str, preview_id: str) -> AReply:
        p = self.previews.pop(preview_id)
        self.w.correct_fact(p["opp"], p["field"], p["value"], confirmed_by_user=True,
                            source_spans=({"message_id": p["message_id"]},), reason="用户更正",
                            preview_token=p["preview"]["token"])
        self.intake.store.sync_world(self.w)
        self.intake.facts.pop(p["opp"].removeprefix("OPP-"), None)  # 本地缓存作废，下次从 World 读
        changed = [i for i in p["preview"]["interpretations"] if self.w.interpretation_changed(i)]
        msg = f"已更正 {p['opp']} 的提交人，并重算。" + (f"依据已变化的解读：{'、'.join(changed)}。" if changed else "")
        self._say(cid, msg, "correction_applied")
        return AReply(msg, "correction_applied")
