"""对话录入（3.1 零打断）：先保存原话，再生成草稿；模型输出一律校验；失败退回手动表单。

流程：
  handle()  保存原话(加密) → 模型抽取 → 校验 → 定位原话片段(R3，程序算偏移，不信模型) → 解析人物 → 写草稿
  confirm() 草稿 → observation（可逐条改，或 confirm_all 一次确认）→ 必要时建 opportunity
  quote()   点回原话
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .llm import ModelOutputInvalid
from .schema import SYSTEM_EXTRACT, validate_extraction
from .store import Store
from .world import SELF, Person, World, classify_source_mode


@dataclass
class Reply:
    text: str
    intent: str = "record"
    draft_ids: list = field(default_factory=list)
    question: Optional[str] = None  # 需要用户回答的追问（至多一个）
    fallback: bool = False  # True：模型不可用/输出无效，只保存了原话，请手动填表
    dropped: int = 0  # 因为找不到原话依据而被丢弃的草稿数
    message_id: int = 0


def find_span(text: str, quote: str) -> Optional[tuple]:
    """在原话里定位 quote：先精确匹配，再忽略空白匹配。找不到返回 None（R3：没有依据就没有草稿）。"""
    i = text.find(quote)
    if i >= 0:
        return i, i + len(quote)
    idx = [k for k, c in enumerate(text) if not c.isspace()]
    flat = "".join(text[k] for k in idx)
    q = re.sub(r"\s+", "", quote)
    j = flat.find(q) if q else -1
    if j < 0:
        return None
    return idx[j], idx[j + len(q) - 1] + 1


class Intake:
    def __init__(self, store: Store, world: World, model, clock=lambda: "") -> None:
        self.store, self.world, self.model, self.clock = store, world, model, clock
        self.aliases: dict[str, list] = {}
        self.facts: dict[str, dict] = {}
        self._seq = 0

    # —— 人物 ——
    def register_person(self, p: Person, aliases: tuple = ()) -> None:
        self.world.add_person(p)
        self.store.save_person(p, aliases)
        for n in {p.display_name, *aliases}:
            self.aliases.setdefault(n, []).append(p.id)

    def resolve(self, name: str) -> tuple:
        """返回 ('self'|'one'|'many'|'none', id 或候选列表)。多个候选或无匹配时不得猜。"""
        if name in ("我", "本人", "自己"):
            return "self", SELF
        ids = self.aliases.get(name, [])
        if len(ids) == 1:
            return "one", ids[0]
        return ("many", ids) if ids else ("none", None)

    def _known(self) -> str:
        return "、".join(sorted({n for n in self.aliases})) or "（暂无）"

    # —— 录入 ——
    def handle(self, cid: str, text: str, *, hours_since_event: float = 0) -> Reply:
        mid = self.store.add_message(cid, "user", text, now=self.clock())  # 先保存原话，任何失败都不丢
        pending = self.store.get_pending(cid)
        prompt = f"已知人物：{self._known()}（“我”是用户本人）\n"
        if pending:
            prompt += f"你上一轮问用户：{pending['question']}\n下面是用户对它的回答。\n"
        prompt += f"用户原话：{text}"
        try:
            data = self.model.structured(SYSTEM_EXTRACT, prompt, validate_extraction)
        except (ModelOutputInvalid, OSError) as e:
            self.store.add_message(cid, "assistant", "模型暂时无法解析这条记录，原话已加密保存。可以手动填写。",
                                   "fallback", now=self.clock())
            return Reply("原话已保存 · 模型未能解析，请手动填写", fallback=True, message_id=mid)
        mode, prompted = classify_source_mode(hours_since_event, prompted_by_model=False)
        iid = pending["interaction_id"] if pending else None
        draft_ids, dropped, ask = [], 0, None
        for x in data["drafts"]:
            span = find_span(text, x["source_quote"])
            if span is None:
                dropped += 1
                continue
            rep, sub = self.resolve(x["reporter"]), self.resolve(x["subject"])
            amb = next((n for n, r in ((x["reporter"], rep), (x["subject"], sub)) if r[0] == "many"), None)
            if amb:
                ask = f"你说的“{amb}”是指哪一位？"
                dropped += 1
                continue
            if iid is None:
                self._seq += 1
                iid = f"INT-{cid}-{self._seq}"
            did = f"D{len(self.store.drafts()) + 1}"
            proposed = {"kind": x["kind"], "reporter_name": x["reporter"], "reporter_id": rep[1],
                        "subject_name": x["subject"], "subject_id": sub[1], "content": x["content"],
                        "is_interpretation": x["is_interpretation"], "behavior_key": x.get("behavior_key"),
                        "occurred": x.get("occurred"), "interaction_id": iid, "source_mode": mode}
            self.store.save_draft(did, mid, [{"message_id": mid, "start": span[0], "end": span[1]}], proposed,
                                  now=self.clock())
            draft_ids.append(did)
        if data.get("interaction") and any(v is not None for v in data["interaction"].values()):
            if iid is None:
                self._seq += 1
                iid = f"INT-{cid}-{self._seq}"
            self._merge_facts(iid, data["interaction"])
        q = ask or data.get("clarifying_question")
        self.store.set_pending(cid, {"interaction_id": iid, "question": q} if q and iid else None)
        n = len(draft_ids)
        if data["intent"] == "question":
            body = data.get("reply") or ""
            line = body
        else:
            line = f"已记下 · {n} 条待确认" if n else "已记下"
            if data["intent"] == "unclear":
                line += "\n要一起分析吗"
        self.store.add_message(cid, "assistant", line, now=self.clock())
        return Reply(line, data["intent"], draft_ids, q, False, dropped, mid)

    def _merge_facts(self, iid: str, new: dict) -> None:
        cur = self.facts.setdefault(iid, {"cross_dept": None, "submitter": None, "responsible_named": None})
        for k, v in new.items():
            if v is None:
                continue
            if k == "submitter":
                kind, pid = self.resolve(v)
                v = pid if kind in ("self", "one") else v  # 未解析的名字保留原文，不猜
            cur[k] = v
        self.store.save_interaction(iid, iid, "occurred", cur)
        self._sync_opportunity(iid)

    def _sync_opportunity(self, iid: str) -> None:
        oid, f = f"OPP-{iid}", self.facts[iid]
        s = self.world.stat_opps.get(oid)
        if s is None:
            return
        s.cross_dept, s.target_id, s.responsible_named = f["cross_dept"], f["submitter"], f["responsible_named"]
        self.world.engine.opps[oid].facts.update(cross_dept=f["cross_dept"], submitter=f["submitter"],
                                                 responsible_named=f["responsible_named"])  # 补全未知，不是更正
        if f["responsible_named"] is not None:
            for fu in self.world.followups:
                if fu.target_ref == oid and fu.status == "open":
                    fu.status = "done"
                    self.store.save_followup(fu)

    # —— 确认 ——
    def _person_for(self, name: str, pid: Optional[str]) -> str:
        if pid:
            return pid
        ph = self.world.create_placeholder(name)  # 不猜：未识别的人先建占位，之后可合并
        self.store.save_person(ph)
        self.aliases.setdefault(name, []).append(ph.id)
        return ph.id

    def confirm(self, did: str, edits: Optional[dict] = None) -> str:
        d = next(x for x in self.store.drafts() if x["id"] == did)
        if d["status"] not in ("pending",):
            return d["confirmed_ref"]
        b = {**d["proposed"], **(edits or {})}
        if edits:
            for side in ("reporter", "subject"):
                if f"{side}_name" in edits and f"{side}_id" not in edits:
                    kind, pid = self.resolve(edits[f"{side}_name"])
                    b[f"{side}_id"] = pid if kind in ("self", "one") else None
        rep, sub = self._person_for(b["reporter_name"], b["reporter_id"]), self._person_for(b["subject_name"], b["subject_id"])
        iid = b["interaction_id"]
        self.facts.setdefault(iid, {"cross_dept": None, "submitter": None, "responsible_named": None})
        o = self.world.add_observation(iid, tuple(d["source_spans"]), b["kind"], rep, sub, b["content"],
                                       is_interpretation=b["is_interpretation"], source_mode=b["source_mode"])
        self.store.save_observation(o, d["source_spans"], did)
        if b.get("behavior_key") and b.get("occurred") is not None:
            self._ensure_opportunity(iid, sub, b["behavior_key"], b["occurred"])
        self.store.save_draft(did, d["message_id"], d["source_spans"], b, "edited" if edits else "confirmed", o.id,
                              self.clock())
        return o.id

    def _ensure_opportunity(self, iid: str, actor: str, behavior_key: str, occurred: bool) -> None:
        """同一互动、同一行为者、同一行为只有一个机会；“连问三次”只计一次发生。"""
        oid = f"OPP-{iid}"
        if oid in self.world.stat_opps:
            return
        f = self.facts[iid]
        before = len(self.world.followups)
        self.world.add_opportunity(oid, actor=actor, target=f["submitter"], cross_dept=f["cross_dept"],
                                   responsible_named=f["responsible_named"], occurred=occurred,
                                   behavior_key=behavior_key, interaction_id=iid)
        for fu in self.world.followups[before:]:
            self.store.save_followup(fu)

    def confirm_all(self, cid: Optional[str] = None) -> list:
        return [self.confirm(d["id"]) for d in self.store.drafts("pending")]

    def reject(self, did: str) -> None:
        d = next(x for x in self.store.drafts() if x["id"] == did)
        self.store.save_draft(did, d["message_id"], d["source_spans"], d["proposed"], "rejected", None, self.clock())

    # —— 点回原话 ——
    def quote(self, obs_id: str) -> list:
        o = self.world.observations[next(k for k, v in self.world.observations.items() if v.id == obs_id)]
        return [self.store.quote(s) for s in o.source_spans]
