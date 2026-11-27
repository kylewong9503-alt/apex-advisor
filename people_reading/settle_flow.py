"""会后结算流程（3.5）。

  open        结算模式首屏：只有议题名、会谈时间、开放问题、输入框（防提示）
  submit_report  首份报告：按“结算提醒”记录（当时记录 + prompted_by_model）；此后才展开预测与对照
  premise_questions / answer_premise  逐条核对前提（R11），中性选项，不提示正确答案；各预测各自核对
  propose_outcome / confirm_outcome   模型对照标准提出结果 → 用户确认 → 按机会只确认一次 → 各预测分别结算
  review      三行判断卡、干预结果、议题角色证据（措辞限于原话）、watch 与后续预测建议
"""
from __future__ import annotations

import re
from typing import Optional

from .cards import settlement_screen
from .engine import EngineError, Premise
from .intake import Intake, find_span
from .judgment import judgment_card
from .engine import _same
from .world import Followup, MatterRole, classify_source_mode

AFTER_MARKER = re.compile(r"后来|之后才|才拿出|才讲|才展示|才说")
DEFAULT_CHOICES = (("成立", "holds"), ("不成立", "violated"), ("记不清", "unclear"))
# 议题角色证据里不得出现、除非原话本身就有：主动提出 / 已有具体人选 / 能完成引荐
OVERCLAIMS = ("主动提出", "具体联系人", "具体人选", "已有联系人", "能完成引荐", "答应引荐", "一定会引荐", "已经联系")


def evidence_wording_ok(statement: str, quote: str) -> bool:
    return not any(w in statement and w not in quote for w in OVERCLAIMS)


def validate_proposal(categories: Optional[tuple]):
    def v(d) -> list:
        if not isinstance(d, dict):
            return ["必须是对象"]
        errs = []
        if not isinstance(d.get("observable"), bool):
            errs.append("observable 必须是布尔")
        if d.get("anchor_occurred") is not None and not isinstance(d.get("anchor_occurred"), bool):
            errs.append("anchor_occurred 必须是布尔或 null")
        an = d.get("anchors")
        if an is not None and not (isinstance(an, dict) and all(isinstance(k, str) and (v is None or isinstance(v, bool))
                                                                  for k, v in an.items())):
            errs.append("anchors 必须是 {标志事件: 是否发生(布尔或 null)}")
        if d.get("observable"):
            if categories is not None and d.get("outcome") not in categories:
                errs.append(f"outcome 必须属于 {list(categories)}")
            if categories is None and not isinstance(d.get("outcome"), bool):
                errs.append("outcome 必须是布尔")
            if not isinstance(d.get("basis_quote"), str) or not d["basis_quote"].strip():
                errs.append("basis_quote 必须是用户原话片段")
            if not isinstance(d.get("criterion_used"), str) or not d["criterion_used"].strip():
                errs.append("criterion_used 必须写明对照了哪条标准")
        return errs
    return v


SYSTEM_PROPOSE = """你在帮用户按事前写好的标准判断会谈的实际结果。只输出 JSON：
{"observable": 能否从用户的话判断(没听见/无法判断为 false), "outcome": 类别名或布尔,
 "anchors": {提示里列出的每个标志事件: 它是否发生过(不确定为 null)}，每个标志事件分别判断，不要合并,
 "basis_quote": 逐字摘自用户原话的依据片段, "criterion_used": 你对照的那条判定标准原文}
必须严格按给定的类别判定标准、no_event_rule、multi_topic_rule 归类；整场没发生该行为按 no_event_rule；不得自创类别。"""


class SettlementSession:
    def __init__(self, intake: Intake, matter_id: str, interaction_id: str, model=None, meeting_time: str = "",
                 behavior_key: Optional[str] = None, def_version: Optional[int] = None) -> None:
        """behavior_key / def_version：同一场会对同一机会有多种行为、或同一行为的多个定义版本的预测时，
        必须指明这次按哪一个（行为，版本）结算——结果是按（机会，行为，定义版本）确认的。"""
        self.behavior_key, self.def_version = behavior_key, def_version
        self.report_mode = "contemporaneous"
        self.intake, self.w = intake, intake.world
        self.matter_id, self.iid, self.model, self.meeting_time = matter_id, interaction_id, model, meeting_time
        self.state = "awaiting_report"
        self.report_mid: Optional[int] = None
        self.report_text = ""
        self.report_drafts: list = []
        self.proposal: Optional[dict] = None
        self.answers: dict = {}  # (prediction_id, premise_id) → (status, quote, choice, supplement)
        self.review_data: Optional[dict] = None
        self._beh = None

    # —— 预测匹配 ——
    @property
    def eng(self):
        return self.w.engine

    def matched(self) -> list:
        opp = self.eng.opps[self.iid]
        out = []
        for p in self.eng.preds.values():
            if p.st.status != "locked":
                continue
            if self.behavior_key is not None and p.pre.behavior_key != self.behavior_key:
                continue
            if self.def_version is not None and p.pre.def_version != self.def_version:
                continue
            if p.pre.bind_mode == "specific_item" and p.pre.bound_opportunity_id == self.iid:
                out.append(p)
            elif p.pre.bind_mode == "condition_match" and all(_same(opp.facts.get(k), v) for k, v in p.pre.conditions):
                out.append(p)
        return out

    # —— 1. 首屏：只有四项 ——
    def first_screen(self) -> dict:
        if self.state != "awaiting_report":
            raise EngineError("已提交首份报告，不再是结算首屏")
        return settlement_screen(self.w.matters[self.matter_id].title, self.meeting_time)

    # —— 2/3. 首份报告 ——
    def submit_report(self, cid: str, text: str, *, hours_since_event: float = 0):
        self._behavior_for()  # 先检查：多种行为又没指明 behavior_key 时在这里就报错，不保存任何东西
        reply = self.intake.handle(cid, text, hours_since_event=hours_since_event, prompt_kind="settlement_reminder",
                                   interaction_id=self.iid)
        self.report_mid, self.report_text, self.report_drafts = reply.message_id, text, list(reply.draft_ids)
        self.report_mode, _ = classify_source_mode(hours_since_event, prompt_kind="settlement_reminder")  # 2.5：超过 48 小时 = 事后回忆
        self.w.interactions[self.iid]["status"] = "occurred"
        self.state = "premise_check"
        self._hidden_proposal()  # 先在后台对照标准；展示要等前提核对之后
        return reply

    # —— 4. 前提核对 ——
    def _anchors(self) -> set:
        return {pr.anchor_event for p in self.matched() for pr in p.pre.premises if pr.anchor_event}

    def _anchor_occurred(self, pr: Premise) -> Optional[bool]:
        """这个前提的标志事件是否发生过。不同前提的标志事件各自判断；只有一个标志事件时兼容旧的 anchor_occurred。"""
        d = self.proposal or {}
        if isinstance(d.get("anchors"), dict) and pr.anchor_event in d["anchors"]:
            return d["anchors"][pr.anchor_event]
        if "anchor_occurred" in d and len(self._anchors()) <= 1:
            return d["anchor_occurred"]
        return None  # 不知道就按“发生过”问，不替用户猜

    def premise_questions(self) -> list:
        """结算卡必须先问：只有 prepared_items 而 execution_log 为空时，不得默认前提成立。"""
        qs, seen = [], set()
        ident = lambda pr: (pr.text, pr.anchor_event, pr.cutoff)  # noqa: E731  措辞、标志事件、截止点都相同才算同一个前提
        for p in self.matched():
            for pr in p.pre.premises:
                if ident(pr) in seen:
                    continue  # 完全相同的前提只问一次；截止点不同的是不同的前提，要分别问
                seen.add(ident(pr))
                q = pr.cutoff_question if (self._anchor_occurred(pr) is False and pr.cutoff_question) else \
                    (pr.question or f"关于“{pr.text}”，实际情况是？")
                qs.append({"premise_id": pr.id, "premise_text": pr.text, "cutoff": pr.cutoff, "anchor_event": pr.anchor_event,
                           "question": q,
                           "options": [c[0] for c in (pr.choices or DEFAULT_CHOICES)],
                           "prediction_ids": [x.id for x in self.matched() if any(ident(y) == ident(pr) for y in x.pre.premises)]})
        return qs

    def answer_premise(self, premise_text: str, choice: str, supplement: str = "", *, cutoff: Optional[str] = None,
                       anchor_event: Optional[str] = None) -> None:
        """回答一个前提。前提的身份是（措辞，标志事件，截止点）：措辞相同但另两项不同的是不同的前提，
        必须用 anchor_event / cutoff 指明回答的是哪一个。"""
        cands = {(pr.text, pr.anchor_event, pr.cutoff) for p in self.matched() for pr in p.pre.premises if pr.text == premise_text}
        if cutoff is not None:
            cands = {c for c in cands if c[2] == cutoff}
        if anchor_event is not None:
            cands = {c for c in cands if c[1] == anchor_event}
        if len(cands) > 1:
            raise EngineError(f"前提“{premise_text}”有多个不同的（标志事件，截止点）{sorted((c[1], c[2]) for c in cands)}，"
                              f"请用 anchor_event / cutoff 指明回答的是哪一个")
        if not cands:
            raise EngineError("没有匹配的前提")
        target = next(iter(cands))
        picked = None
        for p in self.matched():
            for pr in p.pre.premises:
                if (pr.text, pr.anchor_event, pr.cutoff) == target:
                    mapping = dict(pr.choices or DEFAULT_CHOICES)
                    if choice not in mapping:
                        raise EngineError(f"选项 {choice!r} 不在该前提的选项里")
                    self.answers[(p.id, pr.id)] = (mapping[choice], choice + (f"；{supplement}" if supplement else ""),
                                                  choice, supplement)
                    picked = pr
        self._log_execution(picked, choice, supplement)

    def _log_execution(self, pr: Premise, choice: str, supplement: str) -> None:
        """execution_log 只按用户实际说过的内容写：只选“都没有”→ not_before_marker；
        只有明确补充“后来才拿出/后来讲了”才写 after_marker；不据“都没有”推断材料后来是否用过。"""
        itv = next((i for i in self.w.interventions.values() if i.interaction_id == self.iid), None)
        if itv is None:
            return
        premise_text = f"{pr.text}｜{pr.anchor_event}｜{pr.cutoff}"  # 同一措辞、不同标志事件/截止点的前提分别记
        marker = pr.anchor_event or pr.cutoff
        status = dict(pr.choices or DEFAULT_CHOICES)[choice]
        named = [i for i in itv.prepared_items if i in supplement]
        itv.execution_log = [e for e in itv.execution_log if e.get("premise") != premise_text]
        for item in itv.prepared_items:
            if status == "unclear":
                timing = "unknown"
            elif status == "violated":
                timing = "before_marker"
            elif AFTER_MARKER.search(supplement) and (not named or item in named):
                timing = "after_marker"
            else:
                timing = "not_before_marker"
            itv.execution_log.append({"item": item, "timing": timing, "marker": marker, "premise": premise_text,
                                      "quote": choice + (f"；{supplement}" if supplement else "")})

    # —— 5. 模型提议结果 ——
    def _behavior_for(self):
        if self._beh is None:  # 结算后 matched() 会清空，所以第一次取到就缓存
            ps = self.matched()
            if not ps:
                raise EngineError("没有可结算的预测")
            kinds = {(x.pre.behavior_key, x.pre.def_version) for x in ps}
            if len(kinds) > 1:  # 一次只确认一个（行为，版本）的结果，否则会结算一半
                keys = sorted({k[0] for k in kinds})
                if len(keys) > 1 and self.behavior_key is None:
                    raise EngineError(f"这个机会有多种行为的预测 {keys}；请用 behavior_key 指明按哪一种行为结算")
                raise EngineError(f"行为 {sorted(kinds)} 有多个定义版本的预测；请用 def_version 指明按哪一个版本结算"
                                  f"（构造 SettlementSession 时传 behavior_key 和 def_version）")
            p = ps[0]
            self._beh = (self.eng.behaviors[(p.pre.behavior_key, p.pre.def_version)], p.pre)
        return self._beh

    def _hidden_proposal(self) -> None:
        beh, pre = self._behavior_for()
        if not self.eng.needs_outcome_confirmation(self.iid, beh.key, beh.version) or self.model is None:
            return
        cats = tuple(beh.categories) if beh.outcome_type == "categorical" else None
        crit = dict(beh.category_criteria) if cats else pre.settlement_criterion
        prompt = (f"行为：{beh.description}\n结果空间与判定标准：{crit}\n整场未出现时：{beh.no_event_rule}\n"
                  f"一句话涉及多个类别时：{beh.multi_topic_rule}\n"
                  f"需要分别判断是否发生的标志事件：{sorted(self._anchors())}\n用户原话：{self.report_text}")
        try:
            d = self.model.structured(SYSTEM_PROPOSE, prompt, validate_proposal(cats))
        except Exception:
            return  # 模型不可用：退回由用户手动选择结果
        if d["observable"]:
            span = find_span(self.report_text, d["basis_quote"])
            if span is None:
                return  # 没有原话依据的提议不展示
            d["span"] = {"message_id": self.report_mid, "start": span[0], "end": span[1]}
        self.proposal = d

    def propose_outcome(self) -> dict:
        beh, _ = self._behavior_for()
        if not self.eng.needs_outcome_confirmation(self.iid, beh.key, beh.version):
            rec = self.eng.outcome_for(self.iid, beh.key, beh.version)
            return {"reused": True, "outcome": rec.outcome, "ask_user": False}
        if self.proposal is None:
            return {"reused": False, "outcome": None, "ask_user": True, "manual": True,
                    "choices": list(beh.categories) if beh.categories else [True, False]}
        d = self.proposal
        return {"reused": False, "ask_user": True, "manual": False, "observable": d["observable"],
                "outcome": d.get("outcome"), "basis": self.intake.store.quote(d["span"]) if d["observable"] else None,
                "criterion": d.get("criterion_used")}

    # —— 6. 用户确认 → 各预测分别结算 ——
    def confirm_outcome(self, outcome=None, *, unobservable: bool = False, supported_by: tuple = ()) -> dict:
        """supported_by：你明确指出“这几条已确认的观察就是结果的依据”。全部是当时记录时，结果不算仅回忆。"""
        beh, _ = self._behavior_for()
        # 写入任何东西之前，先确认每份预测都能结算（哈希、前提都齐）：避免结果已写入而部分预测失败
        for p in self.matched():
            self.eng.check_settle(p.id, {pr.id: None for pr in p.pre.premises if (p.id, pr.id) in self.answers},
                                  opp_id=self.iid, need_outcome=False)
        if self.eng.needs_outcome_confirmation(self.iid, beh.key, beh.version):
            # R10：结果是否“仅由回忆材料支撑”，看结果的依据。默认依据就是这份首份报告：事件后超过 48 小时才报告
            # = 回忆（2.5）。同一场会里别的当时记录（比如只记了签到）与这个结果无关，不能让它变成可计入；
            # 只有你明确用 supported_by 指出当时的记录就是依据，才算有当时的证据。
            byid = {o.id: o for o in self.w.observations.values()}
            cited = [byid[i] for i in supported_by if i in byid]
            if supported_by and len(cited) != len(supported_by):
                raise EngineError("supported_by 里有不存在的观察")
            if any(o.interaction_id != self.iid for o in cited):
                raise EngineError("supported_by 只能指本场会谈的观察；别的会议的记录不能充当这个结果的依据")
            # 还必须是关于这个行为的记录：同一场会里无关的当时记录（比如签到）不能让事后回忆变成可计入
            bk_of = {d["confirmed_ref"]: d["proposed"].get("behavior_key") for d in self.intake.store.drafts() if d.get("confirmed_ref")}
            if any(bk_of.get(o.id) != beh.key for o in cited):
                raise EngineError(f"supported_by 只能指关于“{beh.key}”这个行为的观察")
            # 行为相同还不够：所选结果的依据原话必须就在你引用的当时记录里，才算“当时的记录支持这个结果”。
            # 依据来自晚到的报告时，即使同场有同一行为的当时记录（甚至内容相反），结果仍是事后回忆。
            basis = (self.proposal or {}).get("span") if outcome is None or outcome == (self.proposal or {}).get("outcome") else None
            def covers(o) -> bool:
                return basis is not None and any(isinstance(sp, dict) and sp.get("message_id") == basis.get("message_id")
                                                 and sp["start"] <= basis["start"] and basis["end"] <= sp["end"] for sp in o.source_spans)
            has_contemporaneous = bool(cited) and all(o.source_mode == "contemporaneous" for o in cited) and any(covers(o) for o in cited)
            recalled_only = self.report_mode == "recalled" and not has_contemporaneous
            spans = (self.proposal["span"],) if self.proposal and self.proposal.get("span") else \
                ({"message_id": self.report_mid, "start": 0, "end": len(self.report_text)},)
            for o in cited:  # 你指出的当时记录就是依据：它的原话也进入结果的证据链
                spans += tuple(sp for sp in o.source_spans if sp not in spans)
            chosen = outcome if outcome is not None else (self.proposal or {}).get("outcome")
            if unobservable or (self.proposal and self.proposal.get("observable") is False and outcome is None):
                self.eng.confirm_outcome(self.iid, beh.key, beh.version, None, status="unobservable", source_spans=spans)
            else:
                self.eng.confirm_outcome(self.iid, beh.key, beh.version, chosen, source_spans=spans,
                                         recalled_only=recalled_only,
                                         proposal=_canon_proposal(self.proposal))
        result = {}
        for p in self.matched():  # 各预测只核对自己的前提
            ans = {pr.id: (self.answers[(p.id, pr.id)][0], self.answers[(p.id, pr.id)][1])
                   for pr in p.pre.premises if (p.id, pr.id) in self.answers}
            self.eng.settle_prediction(p.id, ans, opp_id=self.iid)
            result[p.id] = p.st.status
        self.state = "settled"
        self.intake.store.sync_world(self.w)
        return result

    # —— 7. 复盘 ——
    def review(self, model=None, *, confirm_intervention: Optional[str] = None, confirm_drafts: bool = True) -> dict:
        """生成三行判断卡，并处理干预结果、议题角色证据、watch 与后续预测建议。
        confirm_drafts=False：首份报告的草稿要由用户逐条确认（应用里如此），这里不替用户确认，也就不产生回测标签。"""
        confirmed = [self.intake.confirm(d) for d in self.report_drafts
                     if next(x for x in self.intake.store.drafts() if x["id"] == d)["status"] == "pending"] if confirm_drafts else []
        itv = next((i for i in self.w.interventions.values() if i.interaction_id == self.iid), None)
        rev = {"confirmed_observations": confirmed, "cards": [], "role_update": None, "alt_role": None,
               "watch": None, "suggested_prediction": None, "intervention": None}
        hyps = [h for h in self.w.hypotheses.values() if h.formal and h.behavior_key == self._behavior_for()[0].key]
        rev["cards"] = [judgment_card(self.w, h.id, self.intake.store) for h in hyps]
        mdl = model or self.model
        d = None
        if mdl is not None:
            try:
                d = mdl.structured(SYSTEM_REVIEW, f"用户原话：{self.report_text}", validate_review)
            except Exception:
                d = None
        if d:
            self._apply_review(d, itv, rev, confirm_intervention)
        self.review_data = rev
        self.intake.store.sync_world(self.w)
        return rev

    def _apply_review(self, d: dict, itv, rev: dict, confirm_intervention: Optional[str]) -> None:
        pid = self.eng.preds[self.matched_ids()[0]].pre.person_id if self.matched_ids() else None
        if itv is not None and d.get("intervention_outcome"):
            rev["intervention"] = {"proposed": d["intervention_outcome"], "confirmed": False}
            if confirm_intervention:  # 由用户确认才写入
                itv.immediate_outcome = confirm_intervention
                rev["intervention"].update(proposed=d["intervention_outcome"], confirmed=True, value=confirm_intervention)
        re_ = d.get("role_evidence")
        if re_ and pid:
            span = find_span(self.report_text, re_["source_quote"])
            if span and evidence_wording_ok(re_["statement"], re_["source_quote"]):
                role = next((r for r in self.w.roles.values() if r.matter_id == self.matter_id and r.person_id == pid
                             and r.role == re_["role"] and r.dimension == "will"), None)
                if role is None:
                    role = self.w.add_matter_role(self.matter_id, pid, re_["role"], dimension="will", status="proposed")
                role.statement = re_["statement"]
                obs_id = rev["confirmed_observations"][-1] if rev["confirmed_observations"] else ""
                role.evidence_refs.append({"observation": obs_id, "span": {"message_id": self.report_mid,
                                                                           "start": span[0], "end": span[1]}})
                self.w._log("role_update", role.id, {"will_evidence": re_["statement"], "status": role.status},
                            "结算后新增一条行为证据；can 维度保持原状")
                rev["role_update"] = {"role": role.id, "statement": role.statement, "status": role.status}
        alt = d.get("alt_role")
        if alt and pid:
            r = self.w.add_matter_role(self.matter_id, pid, alt["role"], dimension=None, status="proposed",
                                       proposed_by="model", statement=alt["statement"])
            rev["alt_role"] = {"role": r.id, "statement": r.statement, "proposed_by": "model"}
        wt = d.get("watch")
        if wt:
            fu = Followup(f"F{len(self.w.followups) + 1}", "watch", f"{self.matter_id}:{pid}", "next_interaction_with_person",
                          wt["text"])
            self.w.followups.append(fu)
            rev["watch"] = {"text": fu.text, "kind": "观察信号", "cost": 0}
        fp = d.get("follow_prediction")
        if fp:
            fu = Followup(f"F{len(self.w.followups) + 1}", "confirm_draft", f"{self.matter_id}:{pid}", "matter_opened",
                          "建议锁定预测：" + fp["statement"])
            self.w.followups.append(fu)
            rev["suggested_prediction"] = {"statement": fp["statement"], "decided_by": "user"}

    def matched_ids(self) -> list:
        return [p.id for p in self.eng.preds.values() if p.st.settled_opportunity_id == self.iid
                or p.st.reviewed_opportunity_id == self.iid]


def _canon_proposal(d: Optional[dict]) -> str:
    if not d:
        return ""
    return f"对照标准：{d.get('criterion_used', '')}；模型建议：{d.get('outcome')}"


def validate_review(d) -> list:
    if not isinstance(d, dict):
        return ["必须是对象"]
    errs = []
    if d.get("intervention_outcome") not in (None, "achieved", "partial", "not_achieved"):
        errs.append("intervention_outcome 非法")
    re_ = d.get("role_evidence")
    if re_ is not None and not (isinstance(re_, dict) and all(isinstance(re_.get(k), str) and re_[k].strip()
                                                              for k in ("role", "statement", "source_quote"))):
        errs.append("role_evidence 需要 role/statement/source_quote")
    for k in ("alt_role",):
        x = d.get(k)
        if x is not None and not (isinstance(x, dict) and isinstance(x.get("role"), str) and isinstance(x.get("statement"), str)):
            errs.append(f"{k} 需要 role/statement")
    for k in ("watch", "follow_prediction"):
        x = d.get(k)
        if x is not None and not (isinstance(x, dict) and any(isinstance(x.get(f), str) and x[f].strip() for f in ("text", "statement"))):
            errs.append(f"{k} 需要 text/statement")
    return errs


SYSTEM_REVIEW = """根据用户会后的原话，只输出 JSON，字段都可为 null：
intervention_outcome: 会前写定的目标达成情况 achieved|partial|not_achieved
role_evidence: 对方在本议题中展现的“愿不愿意出手”的行为证据 {"role","statement","source_quote"}；statement 必须严格限于原话所支持的内容，
  不得写成“主动提出”“已有具体联系人”“能完成引荐”，除非原话明确这样说
alt_role: 你提出的另一种可能（模型提出，待用户确认）{"role","statement"}
watch: 值得观察的后续信号 {"text"}（不必问对方，代价为 0）
follow_prediction: 建议锁定的后续预测 {"statement"}（只是建议，由用户决定）
不得创建人物，不得生成人与人之间的关系，除非原话明确指向具体的人。"""
