"""SQLite 持久化（对话、原话、草稿、观察、互动、人物、待办）。敏感列全部 AES-GCM 加密。

列的 AAD 绑定“表.列#行id”，密文不能被挪到别的行或列。
load_world() 把已确认的数据装回 World；World 的计算逻辑不依赖存储。
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
from typing import Any, Optional

from . import crypto
from .engine import Engine, Revision
from .world import (Commitment, Edge, Followup, Hypothesis, Intervention, Matter, MatterEdge, MatterRole, Observation, OrgUnit, Person, Position, StatOpp,
                    UnitLink, World)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS conversation(id TEXT PRIMARY KEY, title TEXT, matter_id TEXT, created_at TEXT, updated_at TEXT, pending_json TEXT);
CREATE TABLE IF NOT EXISTS message(id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, role TEXT, text TEXT, card_type TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS draft(id TEXT PRIMARY KEY, message_id INTEGER, source_spans TEXT, proposed TEXT, status TEXT, confirmed_ref TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS person(id TEXT PRIMARY KEY, display_name TEXT, aliases TEXT, is_self INTEGER, is_placeholder INTEGER, placeholder_desc TEXT, merged_into_id TEXT, code TEXT, influence INTEGER, quadrant_manual TEXT, quadrant_set_at INTEGER, quadrant_auto TEXT);
CREATE TABLE IF NOT EXISTS interaction(id TEXT PRIMARY KEY, label TEXT, status TEXT, facts TEXT);
CREATE TABLE IF NOT EXISTS observation(id TEXT PRIMARY KEY, interaction_id TEXT, source_spans TEXT, kind TEXT, reporter_id TEXT, subject_person_id TEXT, content TEXT, is_interpretation INTEGER, source_mode TEXT, prompted_by_model INTEGER, involved TEXT, draft_id TEXT, recorded_at INTEGER, summary TEXT);
CREATE TABLE IF NOT EXISTS entity(kind TEXT, id TEXT, data TEXT, PRIMARY KEY(kind, id));
CREATE TABLE IF NOT EXISTS followup(id TEXT PRIMARY KEY, kind TEXT, target_ref TEXT, trigger_ TEXT, text TEXT, status TEXT);
"""

ENC = {  # 加密列
    "message": ("text",), "draft": ("source_spans", "proposed"), "conversation": ("title", "pending_json"),
    "person": ("display_name", "aliases", "placeholder_desc"), "interaction": ("label", "facts"),
    "observation": ("source_spans", "content", "involved", "summary"), "followup": ("text",),
}


class Store:
    def __init__(self, path: str, passphrase: str) -> None:
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        row = self.db.execute("SELECT v FROM meta WHERE k='salt'").fetchone()
        if row is None:
            salt = crypto.new_salt()
            self.key = crypto.derive_key(passphrase, salt)
            self.db.execute("INSERT INTO meta VALUES('salt',?)", (salt.hex(),))
            self.db.execute("INSERT INTO meta VALUES('check',?)", (crypto.enc_text(self.key, crypto.CHECK_PLAIN, "meta"),))
            self.db.commit()
        else:
            self.key = crypto.derive_key(passphrase, bytes.fromhex(row["v"]))
            chk = self.db.execute("SELECT v FROM meta WHERE k='check'").fetchone()["v"]
            if crypto.dec_text(self.key, chk, "meta") != crypto.CHECK_PLAIN:
                raise crypto.WrongPassphrase("口令错误")

    def close(self) -> None:
        self.db.close()

    # —— 通用 ——
    def _e(self, table: str, col: str, rid: Any, val: Any) -> str:
        return crypto.enc_text(self.key, json.dumps(val, ensure_ascii=False), f"{table}.{col}#{rid}")

    def _d(self, table: str, col: str, rid: Any, token: str) -> Any:
        return json.loads(crypto.dec_text(self.key, token, f"{table}.{col}#{rid}"))

    def _row(self, table: str, row: sqlite3.Row) -> dict:
        d = dict(row)
        for c in ENC.get(table, ()):
            if d.get(c) is not None:
                d[c] = self._d(table, c, d["id"], d[c])
        return d

    # —— 对话与原话 ——
    def new_conversation(self, cid: str, title: str = "", now: str = "") -> None:
        self.db.execute("INSERT INTO conversation VALUES(?,?,?,?,?,?)",
                        (cid, self._e("conversation", "title", cid, title), None, now, now,
                         self._e("conversation", "pending_json", cid, None)))
        self.db.commit()

    def conversations(self) -> list:
        """最近对话（侧栏用）：[(id, 标题, 更新时间)]，新的在前。"""
        rows = [self._row("conversation", r) for r in self.db.execute("SELECT * FROM conversation ORDER BY rowid DESC")]
        return [(r["id"], r["title"], r["updated_at"]) for r in rows]

    def set_pending(self, cid: str, pending: Optional[dict]) -> None:
        self.db.execute("UPDATE conversation SET pending_json=? WHERE id=?",
                        (self._e("conversation", "pending_json", cid, pending), cid))
        self.db.commit()

    def get_pending(self, cid: str) -> Optional[dict]:
        r = self.db.execute("SELECT * FROM conversation WHERE id=?", (cid,)).fetchone()
        return self._row("conversation", r)["pending_json"] if r else None

    def add_message(self, cid: str, role: str, text: str, card_type: str = "", now: str = "") -> int:
        cur = self.db.execute("INSERT INTO message(conversation_id,role,text,card_type,created_at) VALUES(?,?,?,?,?)",
                              (cid, role, "", card_type, now))
        mid = cur.lastrowid
        self.db.execute("UPDATE message SET text=? WHERE id=?", (self._e("message", "text", mid, text), mid))
        self.db.commit()
        return mid

    def get_message(self, mid: int) -> dict:
        return self._row("message", self.db.execute("SELECT * FROM message WHERE id=?", (mid,)).fetchone())

    def messages(self, cid: str) -> list:
        return [self._row("message", r) for r in
                self.db.execute("SELECT * FROM message WHERE conversation_id=? ORDER BY id", (cid,))]

    def quote(self, span: dict) -> str:
        """点回原话：按 {message_id,start,end} 取原文片段。"""
        return self.get_message(span["message_id"])["text"][span["start"]:span["end"]]

    # —— 草稿 ——
    def save_draft(self, did: str, message_id: int, spans: list, proposed: dict, status: str = "pending",
                   confirmed_ref: Optional[str] = None, now: str = "") -> None:
        self.db.execute("INSERT OR REPLACE INTO draft VALUES(?,?,?,?,?,?,?)",
                        (did, message_id, self._e("draft", "source_spans", did, spans),
                         self._e("draft", "proposed", did, proposed), status, confirmed_ref, now))
        self.db.commit()

    def drafts(self, status: Optional[str] = None, conversation_id: Optional[str] = None) -> list:
        q, args = "SELECT d.* FROM draft d JOIN message m ON d.message_id = m.id WHERE 1=1", []
        if status:
            q += " AND d.status=?"
            args.append(status)
        if conversation_id:
            q += " AND m.conversation_id=?"
            args.append(conversation_id)
        # 按草稿编号排序：INSERT OR REPLACE 会改变 rowid，用 rowid 排序的话，确认过的草稿会跑到末尾
        return [self._row("draft", r) for r in self.db.execute(q + " ORDER BY CAST(SUBSTR(d.id, 2) AS INTEGER)", args)]

    # —— 事实 ——
    def save_person(self, p: Person, aliases: tuple = ()) -> None:
        self.db.execute("INSERT OR REPLACE INTO person VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (p.id, self._e("person", "display_name", p.id, p.display_name),
                         self._e("person", "aliases", p.id, list(aliases)), int(p.is_self), int(p.is_placeholder),
                         self._e("person", "placeholder_desc", p.id, p.placeholder_desc), p.merged_into_id,
                         p.code, p.influence, p.quadrant_manual, p.quadrant_set_at, p.quadrant_auto))
        self.db.commit()

    def load_aliases(self) -> dict:
        """{人物 id: [别名...]}：登记人物时保存的别名，重新打开后恢复人名索引用。"""
        return {r["id"]: self._d("person", "aliases", r["id"], r["aliases"]) for r in self.db.execute("SELECT id, aliases FROM person")}

    def save_interaction(self, iid: str, label: str, status: str, facts: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO interaction VALUES(?,?,?,?)",
                        (iid, self._e("interaction", "label", iid, label), status,
                         self._e("interaction", "facts", iid, facts)))
        self.db.commit()

    def save_observation(self, o: Observation, spans: list, draft_id: Optional[str]) -> None:
        self.db.execute("INSERT OR REPLACE INTO observation VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (o.id, o.interaction_id, self._e("observation", "source_spans", o.id, spans), o.kind,
                         o.reporter_id, o.subject_person_id, self._e("observation", "content", o.id, o.content),
                         int(o.is_interpretation), o.source_mode, int(o.prompted_by_model),
                         self._e("observation", "involved", o.id, o.involved), draft_id, o.recorded_at,
                         self._e("observation", "summary", o.id, o.summary)))
        self.db.commit()

    def save_followup(self, f: Followup) -> None:
        self.db.execute("INSERT OR REPLACE INTO followup VALUES(?,?,?,?,?,?)",
                        (f.id, f.kind, f.target_ref, f.trigger, self._e("followup", "text", f.id, f.text), f.status))
        self.db.commit()

    # —— 通用实体（结构层、机会、解读、修订）：整体加密的 JSON ——
    def save_entity(self, kind: str, eid: str, data: dict) -> None:
        rid = f"{kind}:{eid}"
        self.db.execute("INSERT OR REPLACE INTO entity VALUES(?,?,?)", (kind, eid, self._e("entity", "data", rid, data)))

    def entities(self, kind: str) -> list:
        return [(r["id"], self._d("entity", "data", f"{kind}:{r['id']}", r["data"]))
                for r in self.db.execute("SELECT id, data FROM entity WHERE kind=? ORDER BY rowid", (kind,))]

    def sync_world(self, w: World) -> None:
        """把 World 里可持久化的结构、机会、解读、修订整体落库（幂等 upsert）。"""
        tables = {"org_unit": w.units, "position": w.positions, "unit_link": w.unit_links, "edge": w.edges,
                  "matter": w.matters, "matter_role": w.roles, "matter_edge": w.matter_edges}
        for kind, tbl in tables.items():
            for eid, obj in tbl.items():
                self.save_entity(kind, eid, dataclasses.asdict(obj))
        for oid, o in w.stat_opps.items():
            self.save_entity("opportunity", oid, {**dataclasses.asdict(o), "corrected": w.engine.opps[oid].corrected})
        for iid, i in w.interpretations.items():
            self.save_entity("interpretation", iid, i)
        for r in w.revisions:
            self.save_entity("world_revision", str(r.id), dataclasses.asdict(r))
        for p in w.persons.values():
            if p.id == "user":
                continue
            if self.db.execute("SELECT 1 FROM person WHERE id=?", (p.id,)).fetchone():
                self.db.execute(
                    "UPDATE person SET display_name=?, is_placeholder=?, placeholder_desc=?, merged_into_id=?, code=?, influence=?,"
                    " quadrant_manual=?, quadrant_set_at=?, quadrant_auto=? WHERE id=?",
                    (self._e("person", "display_name", p.id, p.display_name), int(p.is_placeholder),
                     self._e("person", "placeholder_desc", p.id, p.placeholder_desc), p.merged_into_id, p.code,
                     p.influence, p.quadrant_manual, p.quadrant_set_at, p.quadrant_auto, p.id))  # 保留已存的别名
            else:
                self.save_person(p)
        for o in w.observations.values():  # 人物合并后观察改指真实人物，必须落库
            self.db.execute("UPDATE observation SET reporter_id=?, subject_person_id=?, involved=? WHERE id=?",
                            (o.reporter_id, o.subject_person_id, self._e("observation", "involved", o.id, o.involved), o.id))
        for f in w.followups:
            self.save_followup(f)
        for iid, meta in w.interactions.items():
            self.save_entity("interaction_meta", iid, dict(meta))
        for kind, tbl in (("hypothesis", w.hypotheses), ("intervention", w.interventions), ("commitment", w.commitments)):
            for eid, obj in tbl.items():
                self.save_entity(kind, eid, dataclasses.asdict(obj))
        for i, ((pid, sn), (kind, cost)) in enumerate(w.scene_ratings.items()):
            self.save_entity("scene_rating", str(i), {"person": pid, "scene": sn, "kind": kind, "cost": cost})
        for i, l in enumerate(w.recall_links):
            self.save_entity("recall_link", str(i), {"obs_id": l[0], "hyp_id": l[1]})
        self.save_entity("engine", "state", w.engine.to_dict())  # 预测、结果、比较行、修订、规则（声明式）
        self.db.commit()

    def snapshot_world(self, w: World, facts: Optional[dict] = None) -> None:
        """完整落库：人物、互动、观察、待办 + 结构/机会/解读/修订。用于首次导入与测试。"""
        for p in w.persons.values():
            if p.id != "user":
                self.save_person(p)
        for iid, i in w.interactions.items():
            self.save_interaction(iid, i.get("label", ""), i.get("status", "occurred"), (facts or {}).get(iid, {}))
        for o in w.observations.values():
            self.save_observation(o, [s if isinstance(s, dict) else {"text": s} for s in o.source_spans], None)
        for f in w.followups:
            self.save_followup(f)
        self.sync_world(w)

    def load_world(self) -> tuple:
        """返回 (World, {interaction_id: facts})。只装回人物、互动、观察、待办。"""
        w = World()
        facts: dict = {}
        for r in self.db.execute("SELECT * FROM person"):
            d = self._row("person", r)
            if d["id"] != "user":
                w.add_person(Person(d["id"], d["display_name"], bool(d["is_self"]), bool(d["is_placeholder"]),
                                    d["placeholder_desc"], d["merged_into_id"], d["quadrant_manual"], d["code"] or "",
                                    d["influence"] or 3, d["quadrant_set_at"], d["quadrant_auto"]))
        for r in self.db.execute("SELECT * FROM interaction"):
            d = self._row("interaction", r)
            w.add_interaction(d["id"], d["label"], d["status"])
            facts[d["id"]] = d["facts"]
        for r in self.db.execute("SELECT * FROM observation ORDER BY rowid"):
            d = self._row("observation", r)
            o = w.add_observation(d["interaction_id"], tuple(x["text"] if set(x) == {"text"} else x for x in d["source_spans"]), d["kind"],
                                  d["reporter_id"], d["subject_person_id"], d["content"],
                                  is_interpretation=bool(d["is_interpretation"]), source_mode=d["source_mode"],
                                  prompted_by_model=bool(d["prompted_by_model"]), involved=tuple(d["involved"]))
            o.id, o.recorded_at, o.summary = d["id"], d["recorded_at"] or 0, d["summary"] or ""
        for r in self.db.execute("SELECT * FROM followup"):
            d = self._row("followup", r)
            w.followups.append(Followup(d["id"], d["kind"], d["target_ref"], d["trigger_"], d["text"], d["status"]))
        for kind, tbl, cls in (("org_unit", w.units, OrgUnit), ("position", w.positions, Position),
                               ("unit_link", w.unit_links, UnitLink), ("edge", w.edges, Edge),
                               ("matter", w.matters, Matter), ("matter_role", w.roles, MatterRole),
                               ("matter_edge", w.matter_edges, MatterEdge)):
            for eid, d in self.entities(kind):
                tbl[eid] = cls(**d)
        eng = self.entities("engine")
        if eng:
            w.engine = Engine.from_dict(eng[0][1])
        for oid, d in self.entities("opportunity"):
            corrected = d.pop("corrected", False)
            s = StatOpp(**d)
            w.stat_opps[oid] = s
            if oid not in w.engine.opps:  # 旧库（无引擎快照）：由机会重建
                o = w.engine.add_opportunity(oid, s.behavior_key, {"cross_dept": s.cross_dept, "submitter": s.target_id,
                                                                   "responsible_named": s.responsible_named}, oid)
                o.corrected = corrected
        for iid, meta in self.entities("interaction_meta"):
            w.interactions[iid] = dict(meta)
        for eid, d in self.entities("hypothesis"):
            w.hypotheses[eid] = Hypothesis(**d)
        for _, d in self.entities("scene_rating"):
            w.scene_ratings[(d["person"], d["scene"])] = (d["kind"], d["cost"])
        for eid, d in self.entities("commitment"):
            w.commitments[eid] = Commitment(**d)
        for eid, d in self.entities("intervention"):
            w.interventions[eid] = Intervention(**d)
        w.recall_links = [(d["obs_id"], d["hyp_id"]) for _, d in self.entities("recall_link")]
        for iid, d in self.entities("interpretation"):
            d["cited_opps"] = tuple(d["cited_opps"])
            d["cited_pairs"] = tuple(tuple(x) for x in d["cited_pairs"])
            d["open_questions"] = tuple(d["open_questions"])
            w.interpretations[iid] = d
        for _, d in self.entities("world_revision"):
            d["source_spans"] = tuple(d["source_spans"])
            w.revisions.append(Revision(**d))
        return w, facts

    # —— 备份 / 导出：整体加密 ——
    def export_encrypted(self, path: str) -> None:
        dump = "\n".join(self.db.iterdump())
        with open(path, "wb") as f:
            f.write(crypto.encrypt_bytes(self.key, dump.encode(), b"export"))

    def import_encrypted(self, path: str) -> str:
        with open(path, "rb") as f:
            return crypto.decrypt_bytes(self.key, f.read(), b"export").decode()
