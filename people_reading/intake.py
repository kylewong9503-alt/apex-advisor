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
        self.last_iid: dict[str, str] = {}  # 对话 → 最近一次涉及的互动
        self.focus: dict[str, str] = {}  # 对话 → 当前在聊的人物
        self._seq = 0
        self.last_error = ""

    def adopt_world(self) -> None:
        """从已有 World 与库里保存的别名恢复人名索引（重新打开应用后调用）。
        已合并的占位人物，其名字和别名都指向合并后的真实人物。"""
        saved = self.store.load_aliases()

        def final(pid: str) -> str:
            seen = set()
            while self.world.persons[pid].merged_into_id and pid not in seen:
                seen.add(pid)
                pid = self.world.persons[pid].merged_into_id
            return pid
        for p in self.world.persons.values():
            if p.is_self:
                continue
            target = final(p.id)
            for n in {p.display_name, *saved.get(p.id, [])}:
                lst = self.aliases.setdefault(n, [])
                if target not in lst:
                    lst.append(target)

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
    def handle(self, cid: str, text: str, *, hours_since_event: float = 0, prompt_kind: Optional[str] = None,
               interaction_id: Optional[str] = None) -> Reply:
        """prompt_kind：None | "settlement_reminder" | "judgment_support_question"（见 2.5）。
        interaction_id：把这条消息产生的草稿挂到指定互动（结算时挂到刚结束的那场会）。"""
        mid = self.store.add_message(cid, "user", text, now=self.clock())  # 先保存原话，任何失败都不丢
        pending = self.store.get_pending(cid)
        prompt = f"已知人物：{self._known()}（“我”是用户本人）\n"
        open_c = [c for c in self.world.commitments.values() if c.status == "open"]
        if open_c:
            prompt += "已记录且未关闭的承诺：" + "；".join(f"{c.id}={c.content}" for c in open_c) + "\n"
        if pending:
            prompt += f"你上一轮问用户：{pending['question']}\n下面是用户对它的回答。\n"
        prompt += f"用户原话：{text}"
        try:
            data = self.model.structured(SYSTEM_EXTRACT, prompt, validate_extraction)
        except (ModelOutputInvalid, OSError) as e:
            # 只记异常类型；远程调用的异常信息里只有状态码（见 llm.RemoteModelError），不含原话、密钥或响应体
            self.last_error = f"{type(e).__name__}" + (f": {e}" if isinstance(e, OSError) else "")
            self.store.add_message(cid, "assistant", "模型暂时无法解析这条记录，原话已加密保存。可以手动填写。",
                                   "fallback", now=self.clock())
            return Reply("原话已保存 · 模型未能解析，请手动填写", fallback=True, message_id=mid)
        mode, prompted = classify_source_mode(hours_since_event, prompted_by_model=False, prompt_kind=prompt_kind)
        iid = interaction_id or (pending["interaction_id"] if pending else None)
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
                        "occurred": x.get("occurred"), "interaction_id": iid, "source_mode": mode,
                        "prompted_by_model": prompted}
            self.store.save_draft(did, mid, [{"message_id": mid, "start": span[0], "end": span[1]}], proposed,
                                  now=self.clock())
            draft_ids.append(did)
            if sub[1] and sub[1] != SELF:
                self.focus[cid] = sub[1]  # 当前在聊的人
        for r in data.get("relations", []):
            did = self._relation_draft(r, text, mid, iid)
            if did is None:
                dropped += 1
            elif did == "ambiguous":
                ask = ask or "有重名的人，请说明是哪一位。"
            else:
                draft_ids.append(did)
        for c in data.get("commitments", []):
            span, a, b = find_span(text, c["source_quote"]), self.resolve(c["from"]), self.resolve(c["to"])
            if span is None or "many" in (a[0], b[0]):
                dropped += 1
                continue
            did = f"D{len(self.store.drafts()) + 1}"
            self.store.save_draft(did, mid, [{"message_id": mid, "start": span[0], "end": span[1]}],
                                  {"target_table": "commitment", "from_name": c["from"], "from_id": a[1], "to_name": c["to"],
                                   "to_id": b[1], "content": c["content"], "due_text": c.get("due_text") or "",
                                   "interaction_id": iid or f"INT-{cid}-c"}, now=self.clock())
            draft_ids.append(did)
        for u in data.get("commitment_updates", []):
            span = find_span(text, u["source_quote"])
            if span is None or u["id"] not in self.world.commitments or self.world.commitments[u["id"]].status != "open":
                dropped += 1  # 编号不存在或已关闭：不猜
                continue
            did = f"D{len(self.store.drafts()) + 1}"
            self.store.save_draft(did, mid, [{"message_id": mid, "start": span[0], "end": span[1]}],
                                  {"target_table": "commitment_update", "commitment_id": u["id"], "status": u["status"],
                                   "content": self.world.commitments[u["id"]].content,
                                   "interaction_id": iid or f"INT-{cid}-c"}, now=self.clock())
            draft_ids.append(did)
        if data.get("planned_meeting"):
            pm = data["planned_meeting"]
            who = self.resolve(pm["with"])
            did = f"D{len(self.store.drafts()) + 1}"
            self.store.save_draft(did, mid, [{"message_id": mid, "start": 0, "end": len(text)}],
                                  {"target_table": "interaction", "with_name": pm["with"], "with_id": who[1],
                                   "when": pm.get("when") or ""}, now=self.clock())
            draft_ids.append(did)
        for hy in data.get("hypotheses", []):
            span, who = find_span(text, hy["source_quote"]), self.resolve(hy["subject"])
            if span is None or who[0] == "many":
                dropped += 1
                continue
            did = f"D{len(self.store.drafts()) + 1}"
            self.store.save_draft(did, mid, [{"message_id": mid, "start": span[0], "end": span[1]}],
                                  {"target_table": "hypothesis", "subject_name": hy["subject"], "subject_id": who[1],
                                   "statement": hy["statement"], "proposed_by": "user"}, now=self.clock())
            draft_ids.append(did)
        if data.get("matter"):
            did = f"D{len(self.store.drafts()) + 1}"
            self.store.save_draft(did, mid, [{"message_id": mid, "start": 0, "end": len(text)}],
                                  {"target_table": "matter", "title": data["matter"]["title"],
                                   "purpose": data["matter"].get("purpose") or ""}, now=self.clock())
            draft_ids.append(did)
        if data.get("interaction") and any(v is not None for v in data["interaction"].values()):
            if iid is None:
                self._seq += 1
                iid = f"INT-{cid}-{self._seq}"
            conflicts = self._merge_facts(iid, data["interaction"])
            if conflicts and not ask:
                k, old, new_ = conflicts[0]
                label = {"cross_dept": "是否跨部门", "submitter": "提交人", "responsible_named": "是否写明负责人"}[k]
                ask = f"之前记录的{label}是“{self._fact_text(old)}”，这次提到的是“{self._fact_text(new_)}”。要更正吗？"
        if iid:
            self.last_iid[cid] = iid
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

    def _relation_draft(self, r: dict, text: str, mid: int, iid: Optional[str]):
        """关系/角色草稿：必须有原话依据；人物不猜；用户说的关系 proposed_by=user。"""
        span = find_span(text, r["source_quote"])
        if span is None:
            return None
        a = self.resolve(r["from"])
        b = self.resolve(r["to"]) if r.get("to") else ("none", None)
        if "many" in (a[0], b[0]):
            return "ambiguous"
        if r["scope"] == "global":
            body = {"target_table": "edge", "type": r["type"]}
        elif r.get("role"):
            body = {"target_table": "matter_role", "role": r["role"], "dimension": r.get("dimension"),
                    "matter_title": r["matter"]}
        else:
            body = {"target_table": "matter_edge", "type": r["type"], "dimension": r.get("dimension"),
                    "matter_title": r["matter"]}
        body.update(from_name=r["from"], from_id=a[1], to_name=r.get("to"), to_id=b[1], proposed_by="user")
        did = f"D{len(self.store.drafts()) + 1}"
        self.store.save_draft(did, mid, [{"message_id": mid, "start": span[0], "end": span[1]}], body, now=self.clock())
        return did

    def _facts_for(self, iid: str) -> dict:
        """该互动已知的事实。已有机会时以 World 里的为准（用户确认过的更正在那里，缓存可能是旧的）；
        还没有机会时才用本地缓存。"""
        s = self.world.stat_opps.get(f"OPP-{iid}")
        if s is not None:
            self.facts[iid] = {"cross_dept": s.cross_dept, "submitter": s.target_id, "responsible_named": s.responsible_named}
        elif iid not in self.facts:
            self.facts[iid] = {"cross_dept": None, "submitter": None, "responsible_named": None}
        return self.facts[iid]

    def _fact_text(self, v) -> str:
        if v is True:
            return "是"
        if v is False:
            return "否"
        return "我" if v == SELF else (self.world.persons[v].display_name if v in self.world.persons else str(v))

    def _merge_facts(self, iid: str, new: dict) -> list:
        """只补全未知。已知的事实不会被模型改写：冲突的字段原样保留，并返回给调用方去问用户（走更正流程）。"""
        cur = self._facts_for(iid)
        conflicts = []
        for k, v in new.items():
            if v is None:
                continue
            if k == "submitter":
                kind, pid = self.resolve(v)
                v = pid if kind in ("self", "one") else v  # 未解析的名字保留原文，不猜
            if cur[k] is None:
                cur[k] = v
            elif cur[k] != v:
                conflicts.append((k, cur[k], v))
        self.store.save_interaction(iid, iid, "occurred", cur)
        self._sync_opportunity(iid)
        return conflicts

    def _sync_opportunity(self, iid: str) -> None:
        oid, f = f"OPP-{iid}", self.facts[iid]
        s = self.world.stat_opps.get(oid)
        if s is None:
            return
        s.cross_dept, s.target_id, s.responsible_named = f["cross_dept"], f["submitter"], f["responsible_named"]
        self.world.engine.opps[oid].facts.update(cross_dept=f["cross_dept"], submitter=f["submitter"],
                                                 responsible_named=f["responsible_named"])  # 只补全未知，不是更正
        self.world.engine.recompute(oid, "补全未知事实")
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
        """确认（或带修改确认）一条草稿；同时记一条理解回测的标签（模型原版 vs 你的最终版）。"""
        d = next(x for x in self.store.drafts() if x["id"] == did)
        if d["status"] not in ("pending",):
            return d["confirmed_ref"]
        ref = self._confirm(d, edits)
        from .skillset import record_label  # 延迟导入：skillset 依赖本模块
        record_label(self.store, d, "edited" if edits else "confirmed", {**d["proposed"], **(edits or {})})
        return ref

    def _confirm(self, d: dict, edits: Optional[dict]) -> str:
        did = d["id"]
        b = {**d["proposed"], **(edits or {})}
        tt = b.get("target_table", "observation")
        if tt in ("commitment", "commitment_update"):
            ref = self._confirm_commitment(tt, b, d["source_spans"])
            self.store.save_draft(did, d["message_id"], d["source_spans"], b, "edited" if edits else "confirmed", ref,
                                  self.clock())
            self.store.sync_world(self.world)
            return ref
        if tt != "observation":
            ref = self._confirm_structural(tt, b)
            self.store.save_draft(did, d["message_id"], d["source_spans"], b, "edited" if edits else "confirmed", ref,
                                  self.clock())
            self.store.sync_world(self.world)
            return ref
        if edits:
            for side in ("reporter", "subject"):
                if f"{side}_name" in edits and f"{side}_id" not in edits:
                    kind, pid = self.resolve(edits[f"{side}_name"])
                    b[f"{side}_id"] = pid if kind in ("self", "one") else None
        rep, sub = self._person_for(b["reporter_name"], b["reporter_id"]), self._person_for(b["subject_name"], b["subject_id"])
        iid = b["interaction_id"]
        self._facts_for(iid)
        # R3：观察的内容就是用户的原话片段（程序按偏移取出）。模型写的概括单独放在 summary，不是证据；
        # 只有用户在确认时亲手改写（edits["content"]），才以用户写的为准。
        quote = " … ".join(self.store.quote(sp) for sp in d["source_spans"])
        o = self.world.add_observation(iid, tuple(d["source_spans"]), b["kind"], rep, sub, (edits or {}).get("content") or quote,
                                       is_interpretation=b["is_interpretation"], source_mode=b["source_mode"],
                                       prompted_by_model=b.get("prompted_by_model", False),
                                       summary=d["proposed"].get("content", ""))
        self.store.save_observation(o, d["source_spans"], did)
        if b.get("behavior_key") and b.get("occurred") is not None:
            self._ensure_opportunity(iid, sub, b["behavior_key"], b["occurred"])
        self.store.save_draft(did, d["message_id"], d["source_spans"], b, "edited" if edits else "confirmed", o.id,
                              self.clock())
        self.store.sync_world(self.world)
        return o.id

    def _matter_id(self, title: str, purpose: str = "") -> str:
        for m in self.world.matters.values():
            if m.title == title:
                if purpose and m.purpose != purpose:
                    if m.purpose:  # 目的可改，改动写 revision
                        self.world._log("purpose_change", m.id, {"before": m.purpose, "after": purpose}, "用户修改目的")
                    m.purpose = purpose
                return m.id
        mid = f"MT{len(self.world.matters) + 1}"
        self.world.add_matter(mid, title, purpose)
        return mid

    def _confirm_commitment(self, tt: str, b: dict, spans: list) -> str:
        """承诺：先把原话存成观察（R3），再写账本。到期、兑现、违约都不由系统自动判断，只记用户说的。"""
        w = self.world
        iid = b["interaction_id"]
        if tt == "commitment":
            frm = self._person_for(b["from_name"], b["from_id"])
            to = self._person_for(b["to_name"], b["to_id"])
            quote = " … ".join(self.store.quote(sp) for sp in spans)
            o = w.add_observation(iid, tuple(spans), "behavior", SELF, frm, quote, summary=f"承诺：{b['content']}")
            self.store.save_observation(o, spans, None)
            return w.add_commitment(frm, to, b["content"], evidence_obs_id=o.id, due_at=b.get("due_at"),
                                    due_text=b.get("due_text", "")).id
        c = w.commitments[b["commitment_id"]]
        word = "兑现" if b["status"] == "fulfilled" else "未兑现"
        quote = " … ".join(self.store.quote(sp) for sp in spans)
        o = w.add_observation(iid, tuple(spans), "behavior", SELF, c.from_person_id, quote, summary=f"{word}：{c.content}")
        self.store.save_observation(o, spans, None)
        w.close_commitment(c.id, b["status"], closing_obs_id=o.id)
        return c.id

    def _confirm_structural(self, tt: str, b: dict) -> str:
        w = self.world
        if tt == "matter":
            return self._matter_id(b["title"], b.get("purpose", ""))
        if tt == "interaction":
            who = self._person_for(b["with_name"], b["with_id"])
            iid = f"MTG-{who}-{len([i for i in w.interactions if i.startswith('MTG-')]) + 1}"
            w.add_interaction(iid, f"与 {b['with_name']} 的会谈（{b['when']}）" if b["when"] else f"与 {b['with_name']} 的会谈", "planned")
            w.interactions[iid]["with"] = who
            return iid
        if tt == "hypothesis":
            who = self._person_for(b["subject_name"], b["subject_id"])
            return w.add_hypothesis(who, b["statement"], proposed_by=b["proposed_by"]).id
        frm = self._person_for(b["from_name"], b["from_id"])
        to = self._person_for(b["to_name"], b["to_id"]) if b.get("to_name") else None
        if tt == "edge":
            # 用户自述的全局关系：已确认的“自述”，不是被核实的事实（source_tier=self_reported，见 attributes）
            return w.add_edge(frm, to, "interactional", b["type"], proposed_by=b["proposed_by"],
                              status="confirmed", source_tier="self_reported").id
        mt = self._matter_id(b["matter_title"])
        if tt == "matter_role":
            # will 需要行为证据：用户说“他愿意”也只是 proposed；can/未定维度由用户自述即可 confirmed
            status = "proposed" if b.get("dimension") == "will" else "confirmed"
            return w.add_matter_role(mt, frm, b["role"], dimension=b.get("dimension"), status=status,
                                     proposed_by=b["proposed_by"]).id
        return w.add_matter_edge(mt, frm, to, b["type"], status="confirmed", dimension=b.get("dimension"),
                                 proposed_by=b["proposed_by"]).id

    def _ensure_opportunity(self, iid: str, actor: str, behavior_key: str, occurred: bool) -> None:
        """同一互动、同一行为者、同一行为只有一个机会；“连问三次”只计一次发生。"""
        oid = f"OPP-{iid}"
        if oid in self.world.stat_opps:
            return
        f = self._facts_for(iid)
        before = len(self.world.followups)
        self.world.add_opportunity(oid, actor=actor, target=f["submitter"], cross_dept=f["cross_dept"],
                                   responsible_named=f["responsible_named"], occurred=occurred,
                                   behavior_key=behavior_key, interaction_id=iid)
        for fu in self.world.followups[before:]:
            self.store.save_followup(fu)

    def confirm_all(self, cid: Optional[str] = None) -> list:
        """一次确认。给了 cid 就只确认这个对话里的待确认草稿；不给才是全部。"""
        return [self.confirm(d["id"]) for d in self.store.drafts("pending", cid)]

    def reject(self, did: str) -> None:
        d = next(x for x in self.store.drafts() if x["id"] == did)
        if d["status"] != "pending":
            return  # 已处理过：不重复记
        self.store.save_draft(did, d["message_id"], d["source_spans"], d["proposed"], "rejected", None, self.clock())
        from .skillset import record_label
        record_label(self.store, d, "rejected", None)

    # —— 点回原话 ——
    def quote(self, obs_id: str) -> list:
        o = self.world.observations[next(k for k, v in self.world.observations.items() if v.id == obs_id)]
        return [self.store.quote(s) for s in o.source_spans]
