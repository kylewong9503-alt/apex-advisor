"""第 2 步：加密存储、本地模型适配器、对话录入（第 9 节 ①–③ 与“点回原话”）。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from people_reading import crypto, model_eval
from people_reading.intake import Intake, find_span
from people_reading.llm import (ExternalModelRefused, LocalModel, ModelOutputInvalid, ScriptedModel, assert_local,
                                clean_output)
from people_reading.schema import validate_extraction
from people_reading.store import Store
from people_reading.world import SELF, Person, World

TEXT1 = "今天会上，X 连问三次谁负责。散会后甲说，X 很不满。"
PASS = "测试口令-1"


def extraction(**kw):
    base = {"intent": "record", "reply": None, "clarifying_question": None, "interaction": None, "drafts": []}
    base.update(kw)
    return base


D_BEH = {"kind": "behavior", "reporter": "我", "subject": "X", "content": "X 连问三次谁负责", "is_interpretation": False,
         "source_quote": "X 连问三次谁负责", "behavior_key": "ask_owner", "occurred": True}
D_HEAR = {"kind": "hearsay", "reporter": "甲", "subject": "X", "content": "X 很不满", "is_interpretation": True,
          "source_quote": "X 很不满", "behavior_key": None, "occurred": None}
OUT1 = extraction(drafts=[D_BEH, D_HEAR], clarifying_question="三次都是针对同一份提案吗？",
                  interaction={"cross_dept": None, "submitter": None, "responsible_named": None})
OUT2 = extraction(interaction={"cross_dept": True, "submitter": "我", "responsible_named": None})


@pytest.fixture
def env(tmp_path):
    def make(outputs, name="a.db"):
        st = Store(str(tmp_path / name), PASS)
        it = Intake(st, World(), ScriptedModel(outputs))
        for pid in ("X", "甲", "乙"):
            it.register_person(Person(pid, pid))
        st.new_conversation("c1", "测试")
        return st, it
    return make


# ───────── 加密存储 ─────────
def test_crypto_roundtrip_wrong_key_and_aad():
    k = crypto.derive_key("p", b"s" * 16)
    t = crypto.enc_text(k, "原话", "a")
    assert crypto.dec_text(k, t, "a") == "原话"
    with pytest.raises(crypto.WrongPassphrase):
        crypto.dec_text(k, t, "b")  # 密文不能挪到别处
    with pytest.raises(crypto.WrongPassphrase):
        crypto.dec_text(crypto.derive_key("q", b"s" * 16), t, "a")
    with pytest.raises(crypto.WrongPassphrase):
        crypto.derive_key("", b"s" * 16)


def test_store_encrypts_on_disk_and_checks_passphrase(tmp_path):
    p = str(tmp_path / "s.db")
    st = Store(p, PASS)
    st.new_conversation("c", "机密标题")
    mid = st.add_message("c", "user", TEXT1)
    st.close()
    raw = Path(p).read_bytes()
    assert "连问三次".encode() not in raw and "机密标题".encode() not in raw
    with pytest.raises(crypto.WrongPassphrase):
        Store(p, "错的口令")
    st2 = Store(p, PASS)
    assert st2.get_message(mid)["text"] == TEXT1


def test_ciphertext_cannot_be_swapped_between_rows(tmp_path):
    st = Store(str(tmp_path / "s.db"), PASS)
    st.new_conversation("c")
    a, b = st.add_message("c", "user", "甲"), st.add_message("c", "user", "乙")
    ta = st.db.execute("SELECT text FROM message WHERE id=?", (a,)).fetchone()[0]
    st.db.execute("UPDATE message SET text=? WHERE id=?", (ta, b))
    with pytest.raises(crypto.WrongPassphrase):
        st.get_message(b)


def test_export_and_backup_are_encrypted(tmp_path):
    st = Store(str(tmp_path / "s.db"), PASS)
    st.new_conversation("c")
    st.add_message("c", "user", TEXT1)
    out = tmp_path / "backup.bin"
    st.export_encrypted(str(out))
    assert "连问三次".encode() not in out.read_bytes()
    assert "CREATE TABLE" in st.import_encrypted(str(out))
    other = Store(str(tmp_path / "o.db"), "另一个口令")
    with pytest.raises(crypto.WrongPassphrase):
        other.import_encrypted(str(out))


# ───────── 本地模型适配器 ─────────
@pytest.mark.parametrize("url", ["http://127.0.0.1:11434/v1", "http://localhost:11434/v1", "http://[::1]:11434/v1"])
def test_loopback_allowed(url):
    assert_local(url)


@pytest.mark.parametrize("url", ["https://api.deepseek.com/v1", "http://10.0.0.5:11434/v1",
                                 "http://127.0.0.1.evil.com/v1", "http://192.168.1.2:11434/v1"])
def test_external_refused(url):
    with pytest.raises(ExternalModelRefused):
        assert_local(url)
    with pytest.raises(ExternalModelRefused):
        LocalModel(url)


def test_clean_output_strips_think_and_fences():
    raw = '<think>先想想 {"x":1}</think>\n```json\n{"a": "含}括号", "b": {"c": 1}}\n```'
    assert json.loads(clean_output(raw)) == {"a": "含}括号", "b": {"c": 1}}


class _Stub(BaseHTTPRequestHandler):
    replies: list = []
    seen: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Stub.seen.append(body)
        out = json.dumps({"choices": [{"message": {"content": _Stub.replies.pop(0)}}]}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def stub():
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Stub.seen = []
    yield srv, f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def test_local_model_over_http_with_think_tags_and_retry(stub):
    srv, url = stub
    good = json.dumps(extraction())
    _Stub.replies = ["<think>…</think> 这不是 JSON", "<think>想</think>" + good]
    m = LocalModel(url, "deepseek-r1:14b")
    out = m.structured("sys", "usr", validate_extraction)
    assert out["intent"] == "record" and m.calls == 2
    assert "上次输出有问题" in _Stub.seen[1]["messages"][1]["content"]  # 重试时带着错误
    assert _Stub.seen[0]["model"] == "deepseek-r1:14b" and _Stub.seen[0]["temperature"] == 0


def test_local_model_gives_up_after_retry(stub):
    srv, url = stub
    _Stub.replies = ['{"intent": "bogus", "drafts": []}'] * 2
    with pytest.raises(ModelOutputInvalid):
        LocalModel(url).structured("s", "u", validate_extraction)


def test_schema_rejects_bad_shapes():
    assert validate_extraction(extraction()) == []
    assert validate_extraction(extraction(drafts=[{**D_BEH, "kind": "gossip"}]))
    assert validate_extraction(extraction(interaction={"cross_dept": "yes"}))
    assert validate_extraction([])


# ───────── 对话录入：第 9 节 ①–③ ─────────
def test_9_1_casual_record(env, tmp_path):
    st, it = env([OUT1])
    r = it.handle("c1", TEXT1)
    assert r.text == "已记下 · 2 条待确认" and len(r.draft_ids) == 2 and r.question == "三次都是针对同一份提案吗？"
    assert "连问三次".encode() not in (tmp_path / "a.db").read_bytes()  # 原话加密保存
    assert st.get_message(r.message_id)["text"] == TEXT1
    assert [d["status"] for d in st.drafts()] == ["pending", "pending"]


def test_9_2_clarify_links_same_interaction(env):
    st, it = env([OUT1, OUT2])
    it.handle("c1", TEXT1)
    pending = st.get_pending("c1")
    assert pending["question"] == "三次都是针对同一份提案吗？"
    iid = pending["interaction_id"]
    r2 = it.handle("c1", "是，都是我提交的跨部门提案")
    assert r2.text == "已记下" and r2.draft_ids == [] and st.get_pending("c1") is None
    assert "你上一轮问用户：三次都是针对同一份提案吗？" in it.model.prompts[1]
    assert it.facts[iid] == {"cross_dept": True, "submitter": SELF, "responsible_named": None}


def test_9_3_confirm_rows_and_click_back(env):
    st, it = env([OUT1, OUT2])
    it.handle("c1", TEXT1)
    it.handle("c1", "是，都是我提交的跨部门提案")
    ids = it.confirm_all()
    w = it.world
    obs = {o.kind: o for o in w.observations.values()}
    assert obs["behavior"].reporter_id == SELF and obs["behavior"].subject_person_id == "X"
    assert obs["hearsay"].reporter_id == "甲" and obs["hearsay"].is_interpretation is True
    assert obs["behavior"].interaction_id == obs["hearsay"].interaction_id
    assert it.quote(ids[0]) == ["X 连问三次谁负责"] and it.quote(ids[1]) == ["X 很不满"]
    opp = w.stat_opps["OPP-" + obs["behavior"].interaction_id]
    assert (opp.actor_id, opp.target_id, opp.cross_dept, opp.responsible_named, w.occurred(opp.id)) == ("X", SELF, True, None, True)
    assert [f.text for f in w.followups] == ["补充材料是否写了负责人"]
    assert [d["status"] for d in st.drafts()] == ["confirmed", "confirmed"]


def test_9_3_edit_one_row_and_reject_other(env):
    st, it = env([OUT1])
    r = it.handle("c1", TEXT1)
    o = it.confirm(r.draft_ids[0], edits={"content": "X 就负责人问了三次"})
    it.reject(r.draft_ids[1])
    assert it.world.observations[next(iter(it.world.observations))].content == "X 就负责人问了三次"
    assert [d["status"] for d in st.drafts()] == ["edited", "rejected"] and o


def test_repeated_description_counts_one_occurrence(env):
    st, it = env([extraction(drafts=[D_BEH, {**D_BEH, "content": "又问了一次", "source_quote": "连问三次"}])])
    it.handle("c1", TEXT1)
    it.confirm_all()
    assert len(it.world.observations) == 2 and len(it.world.stat_opps) == 1


def test_filling_unknown_later_closes_followup(env):
    st, it = env([OUT1, OUT2, extraction(interaction={"cross_dept": None, "submitter": None, "responsible_named": False})])
    it.handle("c1", TEXT1); it.handle("c1", "是，都是我提交的跨部门提案")
    it.confirm_all()
    assert it.world.followups[0].status == "open"
    oid = next(iter(it.world.stat_opps))
    st.set_pending("c1", {"interaction_id": oid.removeprefix("OPP-"), "question": "写了负责人吗？"})
    it.handle("c1", "没写")
    assert it.world.followups[0].status == "done" and it.world.stat_opps[oid].responsible_named is False


# ───────── 约束：R3 / 不猜 / 失败退回 ─────────
def test_quote_not_in_original_is_dropped(env):
    st, it = env([extraction(drafts=[D_BEH, {**D_HEAR, "source_quote": "X 非常生气"}])])
    r = it.handle("c1", TEXT1)
    assert len(r.draft_ids) == 1 and r.dropped == 1 and r.text == "已记下 · 1 条待确认"


def test_find_span_tolerates_whitespace_only():
    assert find_span("X 连问 三次", "X连问三次") == (0, 7)
    assert find_span("abc", "abd") is None


def test_unknown_person_becomes_placeholder_not_a_guess(env):
    st, it = env([extraction(drafts=[{**D_BEH, "subject": "老张", "source_quote": "X 连问三次谁负责"}])])
    r = it.handle("c1", TEXT1)
    assert st.drafts()[0]["proposed"]["subject_id"] is None
    it.confirm(r.draft_ids[0])
    ph = [p for p in it.world.persons.values() if p.is_placeholder]
    assert len(ph) == 1 and ph[0].display_name == "老张"


def test_ambiguous_name_asks_instead_of_guessing(env):
    st, it = env([extraction(drafts=[{**D_BEH, "subject": "小王"}])])
    it.register_person(Person("W1", "王总"), aliases=("小王",))
    it.register_person(Person("W2", "王经理"), aliases=("小王",))
    r = it.handle("c1", TEXT1)
    assert r.draft_ids == [] and r.question == "你说的“小王”是指哪一位？" and st.drafts() == []


def test_invalid_output_falls_back_and_writes_nothing(env):
    st, it = env(["不是 JSON", "还是不是"])
    r = it.handle("c1", TEXT1)
    assert r.fallback and "手动" in r.text and r.draft_ids == []
    assert st.drafts() == [] and not it.world.observations
    assert any(m["text"] == TEXT1 for m in st.messages("c1"))  # 原话仍然保存了


def test_model_down_falls_back(env):
    class Down:
        def structured(self, *a, **k):
            raise ConnectionRefusedError("模型没起")
    st, it = env([])
    it.model = Down()
    r = it.handle("c1", TEXT1)
    assert r.fallback and st.drafts() == [] and st.get_message(r.message_id)["text"] == TEXT1


def test_question_and_unclear_intents(env):
    st, it = env([extraction(intent="question", reply="先看他追问的是不是同一类提案。", drafts=[D_BEH]),
                  extraction(intent="unclear", drafts=[D_HEAR])])
    r = it.handle("c1", TEXT1)
    assert r.text == "先看他追问的是不是同一类提案。" and len(r.draft_ids) == 1  # 回答 + 草稿在下方
    r2 = it.handle("c1", TEXT1)
    assert r2.text == "已记下 · 1 条待确认\n要一起分析吗" and r2.question is None  # 不额外发问


def test_persistence_roundtrip_reopen(env, tmp_path):
    st, it = env([OUT1, OUT2])
    it.handle("c1", TEXT1); it.handle("c1", "是，都是我提交的跨部门提案")
    it.confirm_all()
    st.close()
    st2 = Store(str(tmp_path / "a.db"), PASS)
    w, facts = st2.load_world()
    assert {p for p in w.persons} >= {"X", "甲", "乙", SELF}
    assert len(w.observations) == 2 and [f.text for f in w.followups] == ["补充材料是否写了负责人"]
    o = next(o for o in w.observations.values() if o.kind == "hearsay")
    assert st2.quote(o.source_spans[0]) == "X 很不满"
    assert list(facts.values())[0]["cross_dept"] is True
    assert len(st2.messages("c1")) == 4  # 两条原话 + 两条回复


# ───────── 选型评测脚本自身 ─────────
class PerfectModel:
    """按用例给出理想输出，验证评测脚本的判分逻辑本身是对的。"""
    def structured(self, system, user, validate):
        if "你上一轮问用户" in user:
            return OUT2
        if "我该怎么办" in user:
            return extraction(intent="question", reply="先看是否针对同一类提案。")
        if "有点累" in user:
            return extraction()
        return OUT1


def test_model_eval_scores(monkeypatch):
    res = model_eval.evaluate(PerfectModel(), repeats=2)
    assert not res["errors"] and all(v == 1.0 for v in res["rates"].values()), res
    sloppy = ScriptedModel(["垃圾"] * 40)
    bad = model_eval.evaluate(sloppy, repeats=1)
    assert any(v == 0.0 for v in bad["rates"].values())
