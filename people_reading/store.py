"""SQLite 持久化（对话、原话、草稿、观察、互动、人物、待办）。敏感列全部 AES-GCM 加密。

列的 AAD 绑定“表.列#行id”，密文不能被挪到别的行或列。
load_world() 把已确认的数据装回 World；World 的计算逻辑不依赖存储。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from . import crypto
from .world import Followup, Observation, Person, World

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS conversation(id TEXT PRIMARY KEY, title TEXT, matter_id TEXT, created_at TEXT, updated_at TEXT, pending_json TEXT);
CREATE TABLE IF NOT EXISTS message(id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, role TEXT, text TEXT, card_type TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS draft(id TEXT PRIMARY KEY, message_id INTEGER, source_spans TEXT, proposed TEXT, status TEXT, confirmed_ref TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS person(id TEXT PRIMARY KEY, display_name TEXT, aliases TEXT, is_self INTEGER, is_placeholder INTEGER, placeholder_desc TEXT, merged_into_id TEXT);
CREATE TABLE IF NOT EXISTS interaction(id TEXT PRIMARY KEY, label TEXT, status TEXT, facts TEXT);
CREATE TABLE IF NOT EXISTS observation(id TEXT PRIMARY KEY, interaction_id TEXT, source_spans TEXT, kind TEXT, reporter_id TEXT, subject_person_id TEXT, content TEXT, is_interpretation INTEGER, source_mode TEXT, prompted_by_model INTEGER, involved TEXT, draft_id TEXT);
CREATE TABLE IF NOT EXISTS followup(id TEXT PRIMARY KEY, kind TEXT, target_ref TEXT, trigger_ TEXT, text TEXT, status TEXT);
"""

ENC = {  # 加密列
    "message": ("text",), "draft": ("source_spans", "proposed"), "conversation": ("title", "pending_json"),
    "person": ("display_name", "aliases", "placeholder_desc"), "interaction": ("label", "facts"),
    "observation": ("source_spans", "content", "involved"), "followup": ("text",),
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

    def drafts(self, status: Optional[str] = None) -> list:
        q = "SELECT * FROM draft" + (" WHERE status=?" if status else "") + " ORDER BY rowid"
        return [self._row("draft", r) for r in self.db.execute(q, (status,) if status else ())]

    # —— 事实 ——
    def save_person(self, p: Person, aliases: tuple = ()) -> None:
        self.db.execute("INSERT OR REPLACE INTO person VALUES(?,?,?,?,?,?,?)",
                        (p.id, self._e("person", "display_name", p.id, p.display_name),
                         self._e("person", "aliases", p.id, list(aliases)), int(p.is_self), int(p.is_placeholder),
                         self._e("person", "placeholder_desc", p.id, p.placeholder_desc), p.merged_into_id))
        self.db.commit()

    def save_interaction(self, iid: str, label: str, status: str, facts: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO interaction VALUES(?,?,?,?)",
                        (iid, self._e("interaction", "label", iid, label), status,
                         self._e("interaction", "facts", iid, facts)))
        self.db.commit()

    def save_observation(self, o: Observation, spans: list, draft_id: Optional[str]) -> None:
        self.db.execute("INSERT OR REPLACE INTO observation VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (o.id, o.interaction_id, self._e("observation", "source_spans", o.id, spans), o.kind,
                         o.reporter_id, o.subject_person_id, self._e("observation", "content", o.id, o.content),
                         int(o.is_interpretation), o.source_mode, int(o.prompted_by_model),
                         self._e("observation", "involved", o.id, o.involved), draft_id))
        self.db.commit()

    def save_followup(self, f: Followup) -> None:
        self.db.execute("INSERT OR REPLACE INTO followup VALUES(?,?,?,?,?,?)",
                        (f.id, f.kind, f.target_ref, f.trigger, self._e("followup", "text", f.id, f.text), f.status))
        self.db.commit()

    def load_world(self) -> tuple:
        """返回 (World, {interaction_id: facts})。只装回人物、互动、观察、待办。"""
        w = World()
        facts: dict = {}
        for r in self.db.execute("SELECT * FROM person"):
            d = self._row("person", r)
            if d["id"] != "user":
                w.add_person(Person(d["id"], d["display_name"], bool(d["is_self"]), bool(d["is_placeholder"]),
                                    d["placeholder_desc"], d["merged_into_id"]))
        for r in self.db.execute("SELECT * FROM interaction"):
            d = self._row("interaction", r)
            w.add_interaction(d["id"], d["label"], d["status"])
            facts[d["id"]] = d["facts"]
        for r in self.db.execute("SELECT * FROM observation ORDER BY rowid"):
            d = self._row("observation", r)
            o = w.add_observation(d["interaction_id"], tuple(d["source_spans"]), d["kind"],
                                  d["reporter_id"], d["subject_person_id"], d["content"],
                                  is_interpretation=bool(d["is_interpretation"]), source_mode=d["source_mode"],
                                  prompted_by_model=bool(d["prompted_by_model"]), involved=tuple(d["involved"]))
            o.id = d["id"]
        for r in self.db.execute("SELECT * FROM followup"):
            d = self._row("followup", r)
            w.followups.append(Followup(d["id"], d["kind"], d["target_ref"], d["trigger_"], d["text"], d["status"]))
        return w, facts

    # —— 备份 / 导出：整体加密 ——
    def export_encrypted(self, path: str) -> None:
        dump = "\n".join(self.db.iterdump())
        with open(path, "wb") as f:
            f.write(crypto.encrypt_bytes(self.key, dump.encode(), b"export"))

    def import_encrypted(self, path: str) -> str:
        with open(path, "rb") as f:
            return crypto.decrypt_bytes(self.key, f.read(), b"export").decode()
