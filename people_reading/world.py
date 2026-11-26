"""世界层：人、组织、三层关系（验收 B）、互动、观察、机会统计（验收 A 的数据面）。

在 Engine 之上组合：Engine 管预测/结算/比较；World 管事实与结构。
R3 观察必须回指原话；R8 三层不互相推导；R9 范围规则；R10 回忆判定细则（2.5）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional

from .engine import Engine, EngineError, Revision

SELF = "user"


class WorldError(EngineError):
    pass


# ───────── 记录类型 ─────────
@dataclass
class Person:
    id: str
    display_name: str
    is_self: bool = False
    is_placeholder: bool = False
    placeholder_desc: str = ""
    merged_into_id: Optional[str] = None
    quadrant_manual: Optional[str] = None


@dataclass
class OrgUnit:
    id: str
    name: str


@dataclass
class Position:
    id: str
    person_id: str
    title: str
    org_unit_id: str
    grade_level: int
    is_unit_head: bool = False
    valid_from: int = 0
    valid_to: Optional[int] = None
    source: str = "org_doc"


@dataclass
class UnitLink:
    id: str
    from_unit_id: str
    to_unit_id: str
    type: str  # solid_report | dotted_report | support
    valid_from: int = 0
    valid_to: Optional[int] = None


@dataclass
class Edge:
    id: str
    from_person_id: str
    to_person_id: str
    kind: str  # structural | interactional
    type: str
    proposed_by: str = "user"
    status: str = "confirmed"  # confirmed | proposed | refuted
    attributes: dict = field(default_factory=dict)
    valid_from: int = 0
    valid_to: Optional[int] = None


@dataclass
class Matter:
    id: str
    title: str
    purpose: str = ""


@dataclass
class MatterRole:
    id: str
    matter_id: str
    person_id: str
    role: str
    dimension: Optional[str] = None  # can | will | None
    statement: str = ""
    proposed_by: str = "user"
    status: str = "proposed"  # confirmed | proposed | refuted | unknown
    evidence_refs: list = field(default_factory=list)
    valid_from: int = 0
    valid_to: Optional[int] = None


@dataclass
class MatterEdge:
    id: str
    matter_id: str
    from_person_id: str
    to_person_id: str
    type: str
    dimension: Optional[str] = None
    proposed_by: str = "user"
    status: str = "proposed"
    evidence_refs: list = field(default_factory=list)
    valid_from: int = 0
    valid_to: Optional[int] = None


@dataclass
class Draft:
    id: str
    source_spans: tuple
    target_table: str  # edge | matter_role | matter_edge
    proposed: dict
    status: str = "pending"  # pending | confirmed | edited | rejected
    confirmed_ref: Optional[str] = None


@dataclass
class Observation:
    id: str
    interaction_id: str
    source_spans: tuple
    kind: str  # behavior | quote | hearsay
    reporter_id: str
    subject_person_id: str
    content: str
    is_interpretation: bool = False
    source_mode: str = "contemporaneous"  # contemporaneous | recalled
    prompted_by_model: bool = False
    involved: list = field(default_factory=list)


@dataclass
class StatOpp:
    """opportunity：一次“X 有没有机会对某人做某事”。occurred 为 None 表示未知。"""
    id: str
    interaction_id: str
    actor_id: str
    target_id: str
    behavior_key: str
    cross_dept: Optional[bool]
    responsible_named: Optional[bool]
    occurred_manual: Optional[bool]
    day: int


@dataclass
class Followup:
    id: str
    kind: str
    target_ref: str
    trigger: str
    text: str
    status: str = "open"


def classify_source_mode(hours_since_event: float, *, prompted_by_model: bool = False,
                         prompt_kind: Optional[str] = None, first_report_mode: Optional[str] = None) -> tuple:
    """2.5 的判定细则。返回 (source_mode, prompted_by_model)。

    prompt_kind: None（用户主动）| "settlement_reminder"（已锁定预测的中性结算提醒）
                 | "judgment_support_question"（模型为解释/支持当前判断而追问过去的互动）
    """
    if prompt_kind == "judgment_support_question":
        return "recalled", True  # 无论距事件多久
    if first_report_mode is not None and hours_since_event <= 48:
        return first_report_mode, prompted_by_model  # 补充报告沿用首份判定
    if hours_since_event > 48:
        return "recalled", prompted_by_model
    if prompt_kind == "settlement_reminder":
        return "contemporaneous", True
    return "contemporaneous", prompted_by_model


# ───────── World ─────────
class World:
    def __init__(self) -> None:
        self.engine = Engine()
        self.persons: dict[str, Person] = {}
        self.units: dict[str, OrgUnit] = {}
        self.positions: dict[str, Position] = {}
        self.unit_links: dict[str, UnitLink] = {}
        self.edges: dict[str, Edge] = {}
        self.matters: dict[str, Matter] = {}
        self.roles: dict[str, MatterRole] = {}
        self.matter_edges: dict[str, MatterEdge] = {}
        self.drafts: dict[str, Draft] = {}
        self.observations: dict[str, Observation] = {}
        self.interactions: dict[str, dict] = {}
        self.stat_opps: dict[str, StatOpp] = {}
        self.followups: list[Followup] = []
        self.interpretations: dict[str, dict] = {}
        self.revisions: list[Revision] = []
        self.add_person(Person(SELF, "我", is_self=True))

    # —— 基础 ——
    def now(self) -> int:
        return self.engine.now()

    def _log(self, kind, subject, detail, reason="") -> Revision:
        r = Revision(len(self.revisions) + 1, self.now(), kind, subject, detail, reason)
        self.revisions.append(r)
        return r

    def _nid(self, prefix: str, table: dict) -> str:
        return f"{prefix}{len(table) + 1}"

    def add_person(self, p: Person) -> Person:
        self.persons[p.id] = p
        return p

    def create_placeholder(self, desc: str) -> Person:
        pid = self._nid("PH", {k: 1 for k in self.persons if k.startswith("PH")})
        return self.add_person(Person(pid, desc, is_placeholder=True, placeholder_desc=desc))

    # —— 组织（问题 1：职务） ——
    def add_unit(self, uid: str, name: str) -> OrgUnit:
        self.units[uid] = OrgUnit(uid, name)
        return self.units[uid]

    def add_position(self, person_id: str, title: str, unit_id: str, grade: int, *, head: bool = False,
                     source: str = "org_doc") -> Position:
        pos = Position(self._nid("POS", self.positions), person_id, title, unit_id, grade, head, self.now(), None, source)
        self.positions[pos.id] = pos
        return pos

    def end_position(self, person_id: str) -> Position:
        """岗位换人/离岗：关闭旧记录，不覆盖；前任的 edge 与 matter_role 不会被新人继承（它们挂在前任 person_id 上）。"""
        pos = self.current_position(person_id)
        if pos is None:
            raise WorldError("该人物没有有效职务")
        pos.valid_to = self.now()
        self._log("edge_update", f"position:{pos.id}", {"closed": pos.title}, "职务关闭")
        return pos

    def current_position(self, person_id: str, at: Optional[int] = None) -> Optional[Position]:
        for pos in self.positions.values():
            if pos.person_id == person_id and pos.valid_from <= (at if at is not None else 10**9) \
                    and (pos.valid_to is None or (at is not None and at < pos.valid_to)):
                return pos
        return None

    def org_state_at(self, t: int) -> dict:
        return {pos.org_unit_id + ":" + pos.title: pos.person_id for pos in self.positions.values()
                if pos.valid_from <= t and (pos.valid_to is None or t < pos.valid_to)}

    def add_unit_link(self, from_unit: str, to_unit: str, type_: str) -> UnitLink:
        if type_ not in ("solid_report", "dotted_report", "support"):
            raise WorldError("unit_link 类型非法")
        ul = UnitLink(self._nid("UL", self.unit_links), from_unit, to_unit, type_, self.now())
        self.unit_links[ul.id] = ul
        return ul

    def unit_head(self, unit_id: str) -> Optional[str]:
        for pos in self.positions.values():
            if pos.org_unit_id == unit_id and pos.is_unit_head and pos.valid_to is None:
                return pos.person_id
        return None

    def org_path(self, person_id: str, from_person: str = SELF) -> Optional[dict]:
        """由 position + unit_link 推出的组织路径。它不是人对人的边，界面必须与 direct_report 区分。"""
        me = self.current_position(from_person)
        if me is None:
            return None
        start = me.org_unit_id
        if self.unit_head(start) == person_id:
            return None  # 本单元负责人：由 structural 边表达，不是“线下”
        queue, seen = [(start, [start])], {start}
        while queue:
            u, path = queue.pop(0)
            for ul in self.unit_links.values():
                if ul.from_unit_id == u and ul.valid_to is None and ul.type == "solid_report" and ul.to_unit_id not in seen:
                    np_ = path + [ul.to_unit_id]
                    if self.unit_head(ul.to_unit_id) == person_id:
                        names = [self.persons[from_person].display_name] + [self.units[x].name for x in np_]
                        names[-1] += f"（{self.persons[person_id].display_name} 负责）"
                        return {"path": names, "label": "团队在其线下", "is_direct_report": False}
                    seen.add(ul.to_unit_id)
                    queue.append((ul.to_unit_id, np_))
        return None

    # —— 关系（问题 2：全局边） ——
    def add_edge(self, from_p: str, to_p: str, kind: str, type_: str, *, proposed_by: str = "user",
                 status: str = "confirmed", matter_id: Optional[str] = None, **attrs) -> Edge:
        if matter_id is not None:  # R9：限定于某议题的关系不得存成全局 edge
            raise WorldError("议题内关系必须存为 matter_role 或 matter_edge")
        if kind == "structural" and type_ not in ("direct_report", "dotted_report"):
            raise WorldError("structural 边只能是 direct_report / dotted_report")
        if kind == "interactional" and type_ not in ("depends_on", "trusts", "info_channel", "owes_commitment"):
            raise WorldError("interactional 边类型非法")
        e = Edge(self._nid("E", self.edges), from_p, to_p, kind, type_, proposed_by, status, dict(attrs), self.now())
        self.edges[e.id] = e
        self._log("edge_update", e.id, {"new": f"{from_p}-{type_}->{to_p}", "status": status})
        return e

    def relationship_view(self, person_id: str) -> dict:
        """个人页“和我的关系”：结构关系、组织路径、互动关系三者分开。"""
        edges = [e for e in self.edges.values() if e.status != "refuted" and e.valid_to is None
                 and {e.from_person_id, e.to_person_id} == {SELF, person_id}]
        return {"structural": [e for e in edges if e.kind == "structural"],
                "org_path": self.org_path(person_id),
                "interactional": [e for e in edges if e.kind == "interactional"]}

    # —— 议题（问题 3） ——
    def add_matter(self, mid: str, title: str, purpose: str = "") -> Matter:
        self.matters[mid] = Matter(mid, title, purpose)
        return self.matters[mid]

    def add_matter_role(self, matter_id: str, person_id: str, role: str, *, dimension: Optional[str] = None,
                        status: str = "proposed", proposed_by: str = "user", statement: str = "") -> MatterRole:
        r = MatterRole(self._nid("MR", self.roles), matter_id, person_id, role, dimension, statement, proposed_by, status,
                       valid_from=self.now())
        self.roles[r.id] = r
        return r

    def add_matter_edge(self, matter_id: str, from_p: str, to_p: str, type_: str, *, status: str = "proposed",
                        dimension: Optional[str] = None, proposed_by: str = "user") -> MatterEdge:
        me = MatterEdge(self._nid("ME", self.matter_edges), matter_id, from_p, to_p, type_, dimension, proposed_by, status,
                        valid_from=self.now())
        self.matter_edges[me.id] = me
        return me

    def upgrade_suggestions(self) -> list:
        """R9：同类型 matter_edge 在 ≥2 个议题都 confirmed → 建议升级；不自动写入。"""
        groups: dict[tuple, set] = {}
        for me in self.matter_edges.values():
            if me.status == "confirmed" and me.valid_to is None:
                groups.setdefault((me.from_person_id, me.to_person_id, me.type), set()).add(me.matter_id)
        done = {(e.from_person_id, e.to_person_id, e.attributes.get("from_matter_edge_type")) for e in self.edges.values()}
        return [{"from": k[0], "to": k[1], "type": k[2], "matters": sorted(v)}
                for k, v in sorted(groups.items()) if len(v) >= 2 and k not in done]

    def confirm_upgrade(self, suggestion: dict, *, confirmed_by_user: bool) -> Edge:
        if not confirmed_by_user:
            raise WorldError("升级为全局关系必须由用户确认")
        return self.add_edge(suggestion["from"], suggestion["to"], "interactional", "depends_on",
                             proposed_by="user", from_matter_edge_type=suggestion["type"],
                             upgraded_from_matters=suggestion["matters"])

    # —— 草稿（零打断录入的落点） ——
    def propose(self, source_spans: tuple, *, scope: str, from_p: Optional[str] = None, to_p: Optional[str] = None,
                type_: Optional[str] = None, matter_id: Optional[str] = None, person_id: Optional[str] = None,
                role: Optional[str] = None, dimension: Optional[str] = None, proposed_by: str = "user") -> Draft:
        if not source_spans:
            raise WorldError("草稿必须回指原话片段")
        if scope == "global":
            tbl, body = "edge", dict(from_person_id=from_p, to_person_id=to_p, kind="interactional", type=type_,
                                     proposed_by=proposed_by, status="proposed")
        elif role is not None:
            tbl, body = "matter_role", dict(matter_id=matter_id, person_id=person_id, role=role, dimension=dimension,
                                            proposed_by=proposed_by, status="proposed")
        else:
            tbl, body = "matter_edge", dict(matter_id=matter_id, from_person_id=from_p, to_person_id=to_p, type=type_,
                                            dimension=dimension, proposed_by=proposed_by, status="proposed")
        d = Draft(self._nid("D", self.drafts), tuple(source_spans), tbl, body)
        self.drafts[d.id] = d
        return d

    def confirm_draft(self, draft_id: str) -> str:
        d = self.drafts[draft_id]
        b = dict(d.proposed)
        if d.target_table == "edge":
            ref = self.add_edge(b["from_person_id"], b["to_person_id"], b["kind"], b["type"],
                                proposed_by=b["proposed_by"], status="confirmed").id
        elif d.target_table == "matter_role":
            ref = self.add_matter_role(b["matter_id"], b["person_id"], b["role"], dimension=b["dimension"],
                                       status="confirmed", proposed_by=b["proposed_by"]).id
        else:
            ref = self.add_matter_edge(b["matter_id"], b["from_person_id"], b["to_person_id"], b["type"],
                                       status="confirmed", dimension=b["dimension"], proposed_by=b["proposed_by"]).id
        d.status, d.confirmed_ref = "confirmed", ref
        return ref

    # —— 人物合并（占位 → 真实人物） ——
    def merge_person(self, placeholder_id: str, real_id: str) -> Revision:
        ph, real = self.persons[placeholder_id], self.persons[real_id]
        if not ph.is_placeholder or real.is_placeholder:
            raise WorldError("只能把占位人物合并到真实人物")
        moved = {"observations": 0, "edges": 0, "matter_edges": 0, "matter_roles": 0, "positions": 0, "opportunities": 0}
        for o in self.observations.values():
            hit = False
            for fld in ("reporter_id", "subject_person_id"):
                if getattr(o, fld) == placeholder_id:
                    setattr(o, fld, real_id); hit = True
            if placeholder_id in o.involved:
                o.involved = [real_id if x == placeholder_id else x for x in o.involved]; hit = True
            moved["observations"] += hit
        for tbl, name in ((self.edges, "edges"), (self.matter_edges, "matter_edges")):
            for e in tbl.values():
                hit = False
                for fld in ("from_person_id", "to_person_id"):
                    if getattr(e, fld) == placeholder_id:
                        setattr(e, fld, real_id); hit = True
                moved[name] += hit
        for r in self.roles.values():
            if r.person_id == placeholder_id:
                r.person_id = real_id; moved["matter_roles"] += 1
        for p in self.positions.values():
            if p.person_id == placeholder_id:
                p.person_id = real_id; moved["positions"] += 1
        for s in self.stat_opps.values():
            for fld in ("actor_id", "target_id"):
                if getattr(s, fld) == placeholder_id:
                    setattr(s, fld, real_id); moved["opportunities"] += 1
        ph.merged_into_id = real_id
        return self._log("person_merge", f"{placeholder_id}→{real_id}", moved, "占位人物并入真实人物")

    # —— 互动、观察、机会 ——
    def add_interaction(self, iid: str, label: str = "", status: str = "occurred", day: int = 0) -> dict:
        self.interactions[iid] = {"id": iid, "label": label, "status": status, "day": day}
        return self.interactions[iid]

    def add_observation(self, interaction_id: str, source_spans: tuple, kind: str, reporter_id: str,
                        subject_person_id: str, content: str, *, is_interpretation: bool = False,
                        source_mode: str = "contemporaneous", prompted_by_model: bool = False,
                        involved: tuple = (), from_model: bool = False) -> Observation:
        if from_model:  # R3：AI 生成内容不得成为观察
            raise WorldError("AI 生成内容不得作为观察入库；请存为 interpretation")
        if not source_spans:
            raise WorldError("观察必须回指用户原话片段")
        if kind not in ("behavior", "quote", "hearsay"):
            raise WorldError("kind 非法")
        if interaction_id not in self.interactions:
            self.add_interaction(interaction_id)
        o = Observation(self._nid("O", self.observations), interaction_id, tuple(source_spans), kind, reporter_id,
                        subject_person_id, content, is_interpretation, source_mode, prompted_by_model, list(involved))
        self.observations[o.id] = o
        return o

    def add_opportunity(self, opp_id: str, *, actor: str, target: str, cross_dept: Optional[bool],
                        responsible_named: Optional[bool], occurred: Optional[bool] = None, day: int = 0,
                        behavior_key: str = "ask_owner", interaction_id: Optional[str] = None) -> StatOpp:
        iid = interaction_id or opp_id
        if any(s.interaction_id == iid and s.actor_id == actor and s.behavior_key == behavior_key
               for s in self.stat_opps.values()):
            raise WorldError("唯一约束 (interaction_id, actor_id, behavior_key)")
        if iid not in self.interactions:
            self.add_interaction(iid, opp_id, day=day)
        s = StatOpp(opp_id, iid, actor, target, behavior_key, cross_dept, responsible_named, occurred, day)
        self.stat_opps[opp_id] = s
        self.engine.add_opportunity(opp_id, behavior_key, {"cross_dept": cross_dept, "submitter": target,
                                                          "responsible_named": responsible_named}, opp_id)
        if responsible_named is None:  # 依赖字段为 null：不进入该条件统计，并生成“补充条件”
            self.followups.append(Followup(self._nid("F", {f.id: 1 for f in self.followups}), "fill_unknown",
                                           opp_id, "next_interaction_with_person", "补充材料是否写了负责人"))
        return s

    def occurred(self, opp_id: str) -> Optional[bool]:
        s = self.stat_opps[opp_id]
        for (o, bk, _v), rec in self.engine.outcomes.items():
            if o == opp_id and bk == s.behavior_key and rec.status == "confirmed":
                return rec.outcome
        return s.occurred_manual

    def correct_fact(self, opp_id: str, field_name: str, value: Any, **kw) -> Revision:
        """事实更正：引擎重算比较；统计层同步字段。"""
        rev = self.engine.apply_correction(opp_id, field_name, value, **kw)
        s = self.stat_opps[opp_id]
        if field_name == "submitter":
            s.target_id = value
        elif hasattr(s, field_name):
            setattr(s, field_name, value)
        return rev

    # —— 证据徽标（第 4 节） ——
    def evidence_badge(self, person_id: str) -> dict:
        by_int: dict[str, list] = {}
        for o in self.observations.values():
            if o.subject_person_id == person_id:
                by_int.setdefault(o.interaction_id, []).append(o)
        recalled = [i for i, obs in by_int.items() if all(x.source_mode == "recalled" for x in obs)]
        n, k = len(by_int), len(recalled)  # 同一事件的多条转述只算一次
        text = f"关联 {n} 次互动" + (f" · 其中 {k} 次为事后回忆" if k else "")
        return {"interactions": n, "recalled": k, "text": text}

    # —— 解读与“依据已变化” ——
    @staticmethod
    def _h(obj) -> str:
        return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

    def _digest(self, cited_opps, cited_pairs) -> str:
        """被引用机会的事实摘要（随 World 一起持久化，重新打开后仍可比较）。"""
        return self._h({o: self.engine.opps[o].facts for o in cited_opps})

    def _pair_digests(self, cited_pairs) -> dict:
        """被引用规则对的当前比较状态摘要。预测尚未持久化时，重新打开后这些行不存在 → 视为“无法比较”，不报变化。"""
        out = {}
        for (o, a, b) in cited_pairs:
            row = next((r for k, r in self.engine.rows.items() if k[0] == o and k[2:] == (a, b)), None)
            if row is not None:
                st = row.state()
                st.pop("facts_basis", None)
                out[f"{o}|{a}|{b}"] = self._h(st)
        return out

    def save_interpretation(self, question: str, output: str, *, cited_opps=(), cited_pairs=(),
                            open_questions=(), **meta) -> str:
        """meta：skill_version_id / conversation_id / person_ids / matter_id 等，原样保存。"""
        iid = f"INT{len(self.interpretations) + 1}"
        self.interpretations[iid] = {"question": question, "output": output, "cited_opps": tuple(cited_opps),
                                     "cited_pairs": tuple(cited_pairs), "open_questions": tuple(open_questions),
                                     "input_digest": self._digest(cited_opps, cited_pairs),
                                     "pair_digests": self._pair_digests(cited_pairs), **meta}
        return iid

    def interpretation_changed(self, iid: str) -> bool:
        i = self.interpretations[iid]
        if self._digest(i["cited_opps"], i["cited_pairs"]) != i["input_digest"]:
            return True
        now = self._pair_digests(i["cited_pairs"])
        return any(now[k] != v for k, v in i.get("pair_digests", {}).items() if k in now)
