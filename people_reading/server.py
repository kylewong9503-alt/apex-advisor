"""本地服务：把加密存储、口令锁、一问一答页面连成一个能在手机浏览器里用的 app（可“添加到主屏幕”）。

安全模型（单用户）：
  - 默认只监听 127.0.0.1；监听其它地址必须显式 allow_remote=True（应放在 Tailscale 等私有加密通道之后，不要暴露公网）。
  - 解锁前任何页面都不返回数据；口令错误计数与冷却由 AppLock 负责；空闲超时自动上锁，上锁后会话失效。
  - 会话是随机令牌（HttpOnly、SameSite=Strict）；POST 还要求同源（Origin/Host 一致）；Host 头必须在白名单里（防 DNS 重绑定）。
  - 所有响应 Cache-Control: no-store；不注册 service worker（不在手机上缓存任何人物信息）；CSP 禁止一切外链。
  - 密钥与口令不进日志；访问日志只记方法与路径类别，不记查询串与表单。
"""
from __future__ import annotations

import hashlib
import hmac
import html
import http.server
import ipaddress
import secrets
import threading
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from .crypto import WrongPassphrase
from .flow import build_settle_steps, ask, check, build_prep_steps, open_ask, render_flow
from .intake import Intake
from .llm import RemoteModel, make_model
from .engine import EngineError
from .premeeting import CONFIDENCE, RULE_P, PreMeeting, make_premise, profile
from .settle_flow import SettlementSession
from . import scenes as SC
from .world import SELF as SELF_ID
from .privacy import AppLock, Locked, Redactor, TooManyAttempts
from .shell import render_app
from .theme import BRAND, FONT_LINK, mark_svg

ICON = mark_svg()
from .workbench import workbench_view

E = html.escape
CSP = ("default-src 'none'; style-src 'self' 'unsafe-inline'; font-src 'self'; connect-src 'self'; script-src 'unsafe-inline'; img-src 'self' data:; "
       "manifest-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
MANIFEST = ('{"name":"Kagami","short_name":"Kagami","start_url":"/","display":"standalone","background_color":"#f4f1e9",'
            '"theme_color":"#1f3b61","icons":[{"src":"/icon.svg","sizes":"any","type":"image/svg+xml","purpose":"any"}]}')
HEAD_EXTRA = "<link rel='manifest' href='/manifest.webmanifest'><meta name='theme-color' content='#1f3b61'><link rel='icon' href='/icon.svg'>"


def codespaces_host(env: dict, port: int = 8765) -> str:
    """GitHub Codespaces 的端口转发域名，例如 <CODESPACE_NAME>-8765.app.github.dev；不在 Codespaces 里返回空串。"""
    name, dom = env.get("CODESPACE_NAME"), env.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")
    return f"{name}-{port}.{dom}" if name and dom else ""


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


KIND_LABEL = {"commitment": "承诺", "commitment_update": "承诺进展", "edge": "人与人的关系", "hypothesis": "你对他的判断",
              "interaction": "计划中的会谈", "matter": "要办的事", "matter_role": "他在这件事里的角色", "matter_edge": "这件事里的关系",
              "behavior": "你看到的", "quote": "他说的话", "hearsay": "别人转述"}


def kind_label(proposed: dict) -> str:
    """每条草稿是哪一类，让你一眼看出这一屏里的几条各是什么（同一句话常被拆成几类）。"""
    return KIND_LABEL.get(proposed.get("target_table") if proposed.get("target_table") not in (None, "observation") else proposed.get("kind"), "")


def _page(body: str, title: str = BRAND) -> str:
    from .flow import FLOW_CSS
    return (f"<!doctype html><html lang='zh'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
            f"<title>{E(title)}</title><meta name='privacy-capture' content='deny'>{FONT_LINK}{HEAD_EXTRA}<style>{FLOW_CSS}"
            f".gate{{max-width:420px;margin:0 auto;padding:96px 24px;display:flex;flex-direction:column;gap:24px;height:auto}}"
            f"input{{font:inherit;font-size:20px;padding:14px 16px;border:1px solid var(--line-strong);border-radius:2px;background:var(--surface-raised);color:var(--ink);width:100%}}"
            f"</style></head><body>{body}</body></html>")


def unlock_page(msg: str = "") -> str:
    return _page("<form class='gate' method='post' action='/unlock'><span class='brand'>" + mark_svg(28) + BRAND + "</span><h1 style='font-size:28px;margin:0'>输入口令</h1>"
                 "<input type='password' name='passphrase' autocomplete='current-password' aria-label='口令' autofocus>"
                 + (f"<p class='why'>{E(msg)}</p>" if msg else "") + "<button class='btn p' type='submit' style='justify-content:center'>解锁</button></form>")


class App:
    """进程内状态：一把锁、一个会话令牌、一个遮挡开关。"""

    CID = "main"

    def __init__(self, path: str, *, allowed_hosts=(), idle_seconds: float = 300, model=None, **kw) -> None:
        self.lock = AppLock(path, idle_seconds=idle_seconds, **kw)
        self.model = model  # None = 未配置模型：只能手动，不编造
        self.w = None
        self.intake: Optional[Intake] = None
        self.sess: Optional[SettlementSession] = None  # 会后结算（内存里；重启后要重新开始）
        self.sess_hours: Optional[float] = None
        self.sess_review: Optional[dict] = None
        self.notice = ""  # 下一次渲染时给你看一次的提示（比如模型没能解析）
        self.reading: Optional[dict] = None  # 刚确认记下之后的“解读 → 选择 → 留意”，走完才回到空白输入
        self.settle_pick: Optional[list] = None  # 同一场会有多个（行为，版本）要结算时，先让你选一项
        self.salt = secrets.token_bytes(16)  # 把提交绑定到所显示题目的标识：带盐哈希，不含任何原文
        self.lk: Optional[dict] = None  # 锁定预测进行中的选择（锁定前都可改，锁定后不可改）
        self.pms: dict = {}  # 议题 → PreMeeting（模型只在会前页打开时调用一次，失败也缓存，不反复发送）
        self.pm_failed: set = set()
        self.token: Optional[str] = None
        self.masked = False
        self.allowed_hosts = {h.lower() for h in allowed_hosts}
        self.mutex = threading.Lock()

    def authed(self, cookie: str) -> bool:
        with self.mutex:
            if self.lock.locked:  # 空闲超时 → 会话失效，内存里的进度一并清掉
                self.token = None
                self.on_lock()
                return False
            return bool(self.token) and secrets.compare_digest(cookie or "", self.token)

    # —— 解锁后载入世界；上锁后丢弃 ——
    def on_unlock(self) -> None:
        self.on_lock()  # 上一次会话（含空闲超时自动上锁）留下的向导与会话一律作废
        store = self.lock.store
        self.w, _ = store.load_world()
        self.intake = Intake(store, self.w, self.model)
        self.intake.adopt_world()
        self.pms, self.pm_failed = {}, set()
        if self.CID not in [c for c, _, _ in store.conversations()]:
            store.new_conversation(self.CID, "对话")

    def on_lock(self) -> None:
        self.w, self.intake, self.pms, self.pm_failed = None, None, {}, set()
        self.sess, self.sess_hours, self.sess_review = None, None, None
        self.lk = None
        self.reading = None
        self.settle_pick = None
        self.salt = secrets.token_bytes(16)

    def tok(self, *parts) -> str:
        return hmac.new(self.salt, repr(parts).encode(), hashlib.sha256).hexdigest()[:16]

    def _world(self):
        self.lock.store  # 已上锁会抛 Locked
        return self.w

    def external(self) -> bool:
        return isinstance(self.model, RemoteModel)

    def banner(self) -> str:
        return "外部模型已开启：你在这里输入的原话会发送给 DeepSeek" if self.external() else ""

    def pending_steps(self, only=None) -> list:
        """待确认的草稿，按“一条原话”分组：一条原话一屏，默认全选，取消勾选的就是不记。
        只有一条时仍是“记下 / 不记”。展示的是你的原话片段（程序定位），不是模型的转述。only：只看这些草稿 id。"""
        store, groups = self.lock.store, {}
        for d in store.drafts(status="pending", conversation_id=self.CID):
            if only is None or d["id"] in only:
                groups.setdefault(d["message_id"], []).append(d)
        steps = []
        for mid, ds in groups.items():
            quotes = {d["id"]: " … ".join(store.quote(sp) for sp in d["source_spans"]) for d in ds}
            if len(ds) == 1:
                d = ds[0]
                steps.append(ask("记下这一条吗？", [("记下", "", ""), ("不记", "", "")], cue=f"原话：{quotes[d['id']]}", selected=0,
                                 act={"url": "/confirm", "fields": {"id": d["id"]}}))
            else:
                steps.append(check(f"这 {len(ds)} 条都记下吗？", [(d["id"], quotes[d["id"]], kind_label(d["proposed"])) for d in ds],
                                   cue="从你说的这段话里整理出来的",
                                   act={"url": "/confirm_many", "fields": {"msg": str(mid)}}))
        return steps

    # —— 记下之后：回执 + 解读（模型提出几种解释，你选，再决定怎么分清）——
    def _start_reading(self, refs: list) -> None:
        """refs = [(草稿, 确认后的 id)]。只在“这一轮确认的草稿里有关于某个人的观察”时给解读；其余只给一条回执。"""
        from . import skillset as K
        w, st = self._world(), self.lock.store
        if self.sess is not None or self.lk is not None or not refs:
            return
        obs = [o for _, ref in refs for o in w.observations.values() if o.id == ref]
        counts: dict = {}
        for d, _ in refs:
            lab = KIND_LABEL.get(d["proposed"].get("target_table") if d["proposed"].get("target_table") not in (None, "observation") else d["proposed"].get("kind"), "其他")
            counts[lab] = counts.get(lab, 0) + 1
        self.reading = {"counts": counts, "stage": "receipt", "items": None, "pid": None, "quote": "", "picked": None, "why": ""}
        by_person: dict = {}
        iids: dict = {}
        for o in obs:  # 按观察的主体分组：每个人单独读，不把几个人的话拼在一起
            if o.subject_person_id and o.subject_person_id != SELF_ID and not o.is_interpretation:
                by_person.setdefault(o.subject_person_id, []).append(o.content)
                iids.setdefault(o.subject_person_id, o.interaction_id)
        self.reading["queue"] = [(pid, " … ".join(cs), iids[pid]) for pid, cs in by_person.items()]
        self.reading["people"] = list(by_person)  # 回执里“打开档案”用；不依赖模型是否成功
        w0, chk = self._world(), []  # 这些人以前留下的、还没核对的“留意项”：这次新记录是核对的机会
        for iid0, meta in w0.interactions.items():
            for wt in meta.get("watch", []):
                fu = next((f for f in w0.followups if f.id == wt["fid"]), None)
                if wt["pid"] in by_person and fu is not None and fu.status == "open":
                    chk.append({"fid": wt["fid"], "pid": wt["pid"], "signal": wt["signal"], "statement": wt["statement"], "src": iid0,
                                "now": iids[wt["pid"]]})
        self.reading["checks"] = chk
        if chk:
            self.reading["stage"] = "watch"
        if not by_person:
            self.reading["why"] = ("这一轮记下的里没有“关于某个人的所见所闻”（你的看法、计划、承诺不做事件解读）。"
                                   "想让我解读，可以在输入框里用 @ 选一个人再说。")

    def reading_start(self) -> None:
        """回执上的“看解读”：这时才调用模型。多人时先问先看谁；模型给不出就留在回执并说明原因。"""
        r = self.reading
        if r is None or r["stage"] != "receipt":
            raise KeyError("reading")
        if len(r["queue"]) > 1 and not r.get("chosen"):
            r["stage"] = "who"
            return
        if not self._load_next_reading():
            self.notice = "这次没有给出解读：" + (r["why"] or "模型没有给出可用的解释") + "。"
            print(f"[reading] 没有解读：{r['why']}", file=__import__("sys").stderr, flush=True)
            r["stage"] = "receipt"
            r["tried"] = True

    def reading_watch(self, choice: str) -> None:
        r = self.reading
        if r is None or r["stage"] != "watch" or not r["checks"] or choice not in ("0", "1", "2"):
            raise KeyError("watch")
        c, w = r["checks"].pop(0), self._world()
        if choice != "2":
            fu = next(f for f in w.followups if f.id == c["fid"])
            fu.status = "seen" if choice == "0" else "not_seen"
            for wt in w.interactions[c["src"]].get("watch", []):
                if wt["fid"] == c["fid"]:
                    wt["resolved"] = {"answer": fu.status, "by_interaction": c["now"]}  # 留下是哪次记录核对的
            self.lock.store.sync_world(w)
        if not r["checks"]:
            r["stage"] = "receipt"

    def reading_who(self, choice: str) -> None:
        r = self.reading
        if r is None or r["stage"] != "who" or not choice.isdigit() or int(choice) >= len(r["queue"]):
            raise KeyError("who")
        r["queue"].insert(0, r["queue"].pop(int(choice)))
        r["chosen"] = True
        r["stage"] = "receipt"
        self.reading_start()

    def _load_next_reading(self) -> bool:
        """取队列里下一个能读出东西的人；都读完（或模型给不出）返回 False。"""
        from . import skillset as K
        r, w = self.reading, self._world()
        while r.get("queue"):
            pid, text, iid = r["queue"].pop(0)
            diag: list = []
            meta: dict = {}
            items = K.read_event(self.model, text, person_name=w.persons[pid].display_name, diag=diag, meta=meta)
            if diag:
                r["why"] = "；".join(diag)
            if items:
                r.update(stage="fact" if meta.get("missing_fact") else "view", items=items, pid=pid, quote=text, picked=None,
                         iid=iid, fact=meta.get("missing_fact"))
                return True
        return False

    def reading_steps(self) -> list:
        r = self.reading
        if r is None:
            return []
        n = lambda u: {"url": u, "fields": {}}  # noqa: E731
        if r["stage"] == "fact":
            f = r["fact"]
            return [ask(f["question"], [(o, "", "") for o in f["options"]] + [("记不清", "", "")], cue="补一个事实，解读会更准（可跳过）",
                        selected=None, act=n("/reading/fact"))]
        if r["stage"] == "view":
            return [{"kind": "reading", "cue": "你刚记下的", "title": "先这样看：几种说得通的解释", "quote": r["quote"], "items": r["items"],
                     "note": "这只是几种都说得通的解释，现在还分不出哪种更对；它们不算证据，也不会计入任何比较。", "act": n("/reading/next")}]
        if r["stage"] == "pick":
            opts = [(x["statement"], "model", "") for x in r["items"]] + [("说不准", "", "")]
            return [ask("你觉得哪种更像？", opts, cue="凭直觉选，之后可以改", selected=None, act=n("/reading/pick"))]
        if r["stage"] == "signal":
            opts = [(x["signal"], "", "不用问对方，零代价") for x in r["items"]] + [("先放着", "", "")]
            return [ask("想让我替你留意哪一个现象？", opts, cue="以后遇到时我会提醒你", selected=None, act=n("/reading/signal"))]
        tail = "；".join(f"{v} 条{k}" for k, v in r["counts"].items())
        if r["stage"] == "watch":
            c = r["checks"][0]
            return [ask(f"上次说要留意：{c['signal']}。这次的记录里出现了吗？", [("出现了", "", "对应的解释：" + c["statement"]), ("没出现", "", ""), ("还不确定", "", "继续留着")],
                        cue="有一条待核对的观察", selected=None, act=n("/reading/watch"))]
        if r["stage"] == "who":
            w = self._world()
            return [ask("先看谁的？", [(w.persons[pid].display_name, "", "") for pid, *_ in r["queue"]], cue="几个人都有新记录", selected=None,
                        act=n("/reading/who"))]
        if r["stage"] == "receipt":
            w = self._world()
            links = [(w.persons[pid].display_name, "/person/" + self.tok("who", pid), pid) for pid in r.get("people", []) if pid in w.persons]
            can = bool(r["queue"])
            return [{"kind": "receipt", "cue": "已记下", "title": tail, "links": links,
                     "note": "" if can else (r["why"] or ""), "can_read": can,
                     "act": n("/reading/start"), "done": n("/reading/done")}]
        extra = "。留意的现象已加入待办，下次再说到相关的人或事时会提醒你。" if r.get("watched") else "。"
        why = f"这次没有给出解读：{r['why']}。" if r.get("why") and not r.get("watched") and r.get("picked") is None else ""
        return [{"kind": "result", "cue": "已归档", "title": f"已记下：{tail}", "big": "", "big_note": "",
                 "note": why + "你可以随时用右上角的“记一句”继续记" + extra, "act": n("/reading/done")}]

    def reading_next(self, expect: str, choice: str = "") -> None:
        """expect：这个请求对应的步骤（view / pick / signal）；与当前步骤不一致就拒绝，不跳步。"""
        r = self.reading
        if r is None or r["stage"] != expect:
            raise KeyError("reading")
        st = r["stage"]
        if st == "fact":
            f = r["fact"]
            if not choice.isdigit() or int(choice) > len(f["options"]):
                raise KeyError("fact")
            i, w = int(choice), self._world()
            if i < len(f["options"]) and r.get("iid") in w.interactions:  # 答案存在这次互动上，不进观察、不进比较
                w.interactions[r["iid"]].setdefault("facts", []).append({"q": f["question"], "a": f["options"][i], "by": "user"})
                self.lock.store.sync_world(w)
            if i < len(f["options"]):  # 答案成了新的信息：带着它重新生成并重新筛选解读（不再追问）
                from . import skillset as K
                diag: list = []
                items = K.read_event(self.model, r["quote"], person_name=w.persons[r["pid"]].display_name, diag=diag,
                                     extra=f"{f['question']} → {f['options'][i]}")
                if items:
                    r["items"], r["stage"] = items, "view"
                else:
                    r["why"] = "；".join(diag) or "补充事实后没有可用的解释"
                    self._advance()
                return
            r["stage"] = "view"
        elif st == "view":
            r["stage"] = "pick"
        elif st == "pick":
            if not choice.isdigit() or int(choice) > len(r["items"]):
                raise KeyError("pick")
            i, w = int(choice), self._world()
            if i < len(r["items"]):
                for j, x in enumerate(r["items"]):  # 你选的记为你的判断；其余是模型提出的另一种可能
                    w.add_hypothesis(r["pid"], x["statement"], proposed_by="user" if j == i else "model")
                r["picked"] = i
            else:
                for x in r["items"]:
                    w.add_hypothesis(r["pid"], x["statement"], proposed_by="model")
            self.lock.store.sync_world(w)
            r["stage"] = "signal"
        elif st == "signal":
            if not choice.isdigit() or int(choice) > len(r["items"]):
                raise KeyError("signal")
            i, w = int(choice), self._world()
            if i < len(r["items"]):
                from .world import Followup
                fid = f"F{len(w.followups) + 1}"
                w.followups.append(Followup(fid, "watch", r["pid"], "when_relevant", r["items"][i]["signal"]))
                if r.get("iid") in w.interactions:  # 来源：哪件事、哪种解释、要看的现象；以后同一个人的新记录回到这里确认
                    w.interactions[r["iid"]].setdefault("watch", []).append(
                        {"fid": fid, "statement": r["items"][i]["statement"], "signal": r["items"][i]["signal"], "pid": r["pid"]})
                r["watched"] = True
                self.lock.store.sync_world(w)
            self._advance()
        else:
            raise KeyError("done")

    def _advance(self) -> None:
        """这个人走完了：读下一位（可能先是他的补事实题）；没有了就收尾。"""
        if not self._load_next_reading():
            self.reading["stage"] = "done"

    def reading_done(self) -> None:
        if self.reading is None or self.reading["stage"] not in ("receipt", "done"):
            raise KeyError("done")
        self.reading = None

    def confirm_many(self, msg: str, ids: str) -> None:
        store = self.lock.store
        group = [d for d in store.drafts(status="pending", conversation_id=self.CID) if str(d["message_id"]) == msg]
        if not group:
            raise KeyError(msg)
        keep = {i for i in ids.split(",") if i}
        if not keep <= {d["id"] for d in group}:
            raise KeyError("ids")  # 只能处理这一组里还在等你的草稿
        refs = []
        for d in group:
            if d["id"] in keep:
                refs.append((d, self.intake.confirm(d["id"])))
            else:
                self.intake.reject(d["id"])
        store.sync_world(self._world())
        self._start_reading(refs)

    def _has_judgment(self) -> bool:
        got = self._planned()
        if got is None:
            return True  # 没有计划中的会谈：不在这里拦（会前页自己会退回手动）
        w = self._world()
        return any(h.person_id == got[1]["with"] and h.proposed_by == "user" and h.status != "retired" for h in w.hypotheses.values())

    def pre_meeting(self, mid: str):
        if self.model is None or mid in self.pm_failed:
            return None
        if mid not in self.pms:
            w = self._world()
            itv = next(((i, x) for i, x in reversed(list(w.interactions.items())) if x.get("status") == "planned" and x.get("with")), None)
            if itv is None:
                self.pm_failed.add(mid)
                return None
            pm = PreMeeting(self.intake, mid, itv[1]["with"], itv[0], self.model)
            if pm.prepare() is None:
                self.pm_failed.add(mid)
                return None
            self.pms[mid] = pm
        return self.pms[mid]

    def flow_html(self) -> str:
        store = self.lock.store
        w = self._world()
        rd = Redactor(w, "masked" if self.masked else "code")
        steps = self.settle_steps() or self.lock_steps() or self.clarify_steps() or self.pending_steps() or self.reading_steps()
        if not steps and w.matters and not self._has_judgment():
            # 会前准备是“你的判断 vs 另一种可能”：你还没说出判断时不调用模型，先问你
            steps = [self._say_step("你觉得他会先问什么？", w.matters[list(w.matters)[-1]].title, rd)]
        if not steps and w.matters:
            mid = list(w.matters)[-1]
            pm = self.pre_meeting(mid)
            v = workbench_view(w, mid, store, prep=pm.prep if pm else None)
            steps = build_prep_steps(v)
            if pm and steps and steps[-1]["kind"] == "answer":
                eng = w.engine
                locked = any(p.pre.matter_id == mid and p.st.status == "locked" for p in eng.preds.values())
                acted = any(i.matter_id == mid for i in w.interventions.values())
                btns = [{"label": "✓ 已记为行动", "disabled": True} if acted else {"label": "记为行动", "act": {"url": "/act", "fields": {}}}]
                if pm.prep.category_draft is not None:
                    btns.append({"label": "✓ 已锁定预测", "disabled": True} if locked else
                                {"label": "锁定预测", "act": {"url": "/lock/start", "fields": {}, "reload": True}})
                if self._settleable():
                    btns.append({"label": "会后", "act": {"url": "/settle/start", "fields": {}, "reload": True}})
                steps[-1]["buttons"] = btns
        steps = steps or [self._say_step("今天想记一件什么事？", "直接说；用 @ 选谁、# 选场合", rd)]
        return render_flow(steps, rd=rd, library_href="/library", head_extra=HEAD_EXTRA, banner=self.banner(), notice=self._take_notice(),
                           say_href="" if steps[0]["kind"] == "open" else "/say", people_href="/people", draft_key="" if self.masked else "kg.draft")

    def _take_notice(self) -> str:
        n, self.notice = self.notice, ""
        return n

    def people_chips(self, rd) -> list:
        """常提到的人（按被记录的次数、再按新近排序）做成标签；令牌是带盐哈希，页面里没有人物 id，遮挡时显示的也是遮挡后的名字。"""
        from collections import Counter
        w = self._world()
        freq = Counter(o.subject_person_id for o in w.observations.values())
        people = [p for p in w.persons.values() if not p.is_self and not p.is_placeholder and not p.merged_into_id]
        order = {p.id: i for i, p in enumerate(people)}
        people.sort(key=lambda p: (-freq.get(p.id, 0), -order[p.id]))
        return [(self.tok("who", p.id), rd.p(p.id)) for p in people[:12]]

    def scene_chips(self, rd) -> list:
        return [(self.tok("scene", n), rd.t(n) if rd.masked else n) for n in SC.all_scenes(self.lock.store)]

    def _scenes(self, toks: str, newscene: str) -> tuple:
        """页面上选的场景：令牌对不上就拒绝；自定义的当场加进你的场景库（上限 12 个）。"""
        names = []
        if newscene.strip():
            try:
                names.append(SC.add_custom(self.lock.store, newscene))
            except ValueError:
                raise KeyError("scene")
        known = {self.tok("scene", n): n for n in SC.all_scenes(self.lock.store)}
        for t in [x for x in toks.split(",") if x]:
            if t not in known:
                raise KeyError("scene")
            if known[t] not in names:
                names.append(known[t])
        return tuple(names)

    def _who(self, tok: str, newname: str) -> Optional[str]:
        """页面上选的“这是在说谁”：返回人物 id；选“新的人”就当场建一个。令牌对不上就拒绝，不猜。"""
        w = self._world()
        name = newname.strip()[:40]
        if name:
            from .world import Person
            exist = [p.id for p in w.persons.values() if p.display_name == name and not p.merged_into_id]
            if exist:
                return exist[0]  # 同名的已经有了：用已有的，不重复建
            pid = w._nid("P", w.persons)
            while pid in w.persons:
                pid += "x"
            self.intake.register_person(Person(pid, name, code=name), aliases=(name,))
            return pid
        if not tok:
            return None
        for p in w.persons.values():
            if not p.is_self and not p.is_placeholder and not p.merged_into_id and hmac.compare_digest(self.tok("who", p.id), tok):
                return p.id
        raise KeyError("who")

    def _say_step(self, q: str, cue: str, rd) -> dict:
        return open_ask(q, cue=cue, act={"url": "/say", "fields": {}}, people=self.people_chips(rd), scenes=self.scene_chips(rd))

    def say_html(self, who: str = "") -> str:
        """随时记一句：不论现在在哪个流程里。who：从某人的档案页进来时预选的人（令牌，对不上就当没选）。"""
        rd = Redactor(self._world(), "masked" if self.masked else "code")
        step = self._say_step("想记一件什么事？", "直接说；用 @ 选谁、# 选场合", rd)
        if who and who not in {t for t, _ in step["people"]}:  # 档案里的人不一定在常用的前 12 个：只要令牌对得上就显式加进选项
            w = self._world()
            p = next((p for p in w.persons.values() if not p.is_self and not p.is_placeholder and not p.merged_into_id
                      and hmac.compare_digest(self.tok("who", p.id), who)), None)
            if p is None:
                who = ""
            else:
                step["people"] = list(step["people"]) + [(who, rd.p(p.id))]
        return render_flow([step], rd=rd, library_href="/library", head_extra=HEAD_EXTRA, banner=self.banner(), people_href="/people",
                           pre_who=who, draft_key="" if self.masked else "kg.draft")

    def _page(self, title: str, body: str) -> str:
        from .privacy import NO_CAPTURE_META, PRINT_GUARD_CSS
        from .theme import BASE_CSS, mark_data_uri
        css = (BASE_CSS + "main{padding-top:var(--space-4)}.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:var(--space-6)}"
               ".pl{display:flex;justify-content:space-between;align-items:center;min-height:52px;border-top:1px solid var(--line);color:var(--ink);text-decoration:none}"
               ".pl small{color:var(--ink-muted)}input.q{margin-bottom:var(--space-4)}")
        return (f"<!doctype html><html lang='zh'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<title>{E(BRAND)}</title>{NO_CAPTURE_META}<link rel='icon' href='{mark_data_uri()}'>{FONT_LINK}{HEAD_EXTRA}"
                f"<style>{css}{PRINT_GUARD_CSS}</style></head><body><main><div class='top'><a href='/'>← 回到记一句</a><a href='/library'>关系图</a></div>"
                f"{body}</main></body></html>")

    def people_html(self) -> str:
        """人物列表：最近有记录的在前；点进去是档案，可直接“记一件与此人有关的事”。"""
        w = self._world()
        rd = Redactor(w, "masked" if self.masked else "code")
        last: dict = {}
        for o in w.observations.values():
            last[o.subject_person_id] = max(last.get(o.subject_person_id, 0), int(o.id.lstrip("O") or 0) if o.id.lstrip("O").isdigit() else 0)
        people = [p for p in w.persons.values() if not p.is_self and not p.is_placeholder and not p.merged_into_id]
        people.sort(key=lambda p: -last.get(p.id, -1))
        rows = "".join(f"<a class='pl' href='/person/{E(self.tok('who', p.id))}'><span>{E(rd.p(p.id) if self.masked else p.display_name)}</span>"
                       f"<small>{sum(1 for o in w.observations.values() if o.subject_person_id == p.id)} 条记录</small></a>" for p in people)
        return self._page("人物", "<h1>人物</h1>" + (rows or "<p class='mut'>还没有人。先记一句，用 @ 选一个人。</p>"))

    def person_html(self, tok: str) -> str:
        from .profile import build_profile, render_html as render_profile
        w = self._world()
        pid = next((p.id for p in w.persons.values() if not p.is_self and not p.is_placeholder and not p.merged_into_id
                    and hmac.compare_digest(self.tok("who", p.id), tok)), None)
        if pid is None:
            raise KeyError("who")
        rd = Redactor(w, "masked" if self.masked else "code")
        pf = build_profile(w, pid, self.lock.store)
        nopen = sum(1 for f in w.followups if f.kind == "watch" and f.target_ref == pid and f.status == "open")
        hint = f"<p class='mut'>有 {nopen} 条待核对的观察：下次记到这个人时留意。</p>" if nopen else ""
        head = hint + f"<p><a class='btn p' style='display:inline-block;text-decoration:none;padding:8px 16px' href='/say?who={E(tok)}'>记一件与此人有关的事</a></p>"
        return self._page("档案", head + render_profile(pf, rd))

    def library_html(self) -> str:
        store = self.lock.store
        return render_app(self._world(), store, mode="masked" if self.masked else "code", toggle_href="")

    # —— 会后结算：每一步都是服务端状态机的下一步；首份报告之前不出现任何候选答案 ——
    def current_mid(self) -> str:
        w = self._world()
        return list(w.matters)[-1] if w.matters else ""

    def _settleable(self) -> bool:
        """只有计划会谈上有锁定的预测，才出现“会后”：否则报告无法结算。"""
        return bool(self._groups())

    def _groups(self) -> list:
        """这场会上待结算的（行为，版本）组。"""
        got = self._settle_target()
        if got is None:
            return []
        try:
            ps = SettlementSession(self.intake, self.current_mid(), got[0], None).matched()
        except Exception:
            return []
        return sorted({(p.pre.behavior_key, p.pre.def_version) for p in ps})

    def _settle_target(self):
        """还有待结算预测的那场会谈：先结算的那一组把会谈标成 occurred 之后，剩下的组仍要能续上，所以不能只看 planned。"""
        w = self._world()
        for iid, x in reversed(list(w.interactions.items())):
            if x.get("with") and iid in w.engine.opps:
                try:
                    if SettlementSession(self.intake, self.current_mid(), iid, None).matched():
                        return iid, x
                except Exception:
                    continue
        return None

    def _planned(self):
        w = self._world()
        return next(((i, x) for i, x in reversed(list(w.interactions.items())) if x.get("status") == "planned" and x.get("with")), None)

    def _open_premises(self) -> list:
        s = self.sess
        return [q for q in s.premise_questions() if not all((pid, q["premise_id"]) in s.answers for pid in q["prediction_ids"])]

    def settle_steps(self) -> list:
        s = self.sess
        if s is None and self.settle_pick:
            eng = self._world().engine
            opts = [(eng.behaviors[g].description if g in eng.behaviors else g[0], "", f"第 {g[1]} 版定义") for g in self.settle_pick]
            return [ask("这次先结算哪一项？", opts, cue="同一场会上有几项预测，一次结算一项", selected=None,
                        act={"url": "/settle/pick", "fields": {"q": self.tok("pick", self.settle_pick)}})]
        if s is None:
            return []
        if s.state == "awaiting_report":
            if self.sess_hours is None:  # 先问“什么时候想起的”：这不含任何候选答案，只决定算当时记录还是事后回忆
                return [ask("这些是什么时候的事？", [("会谈刚结束（48 小时内）", "", ""), ("已经过了两天以上", "rec", "记为事后回忆，不计入比较")],
                            cue="先说明时间", selected=None, act={"url": "/settle/when", "fields": {}})]
            scr = s.first_screen()
            return [open_ask(scr["open_question"], cue=f"{scr['matter']} · {scr['meeting_time']}", act={"url": "/settle/report", "fields": {}})]
        if s.state == "premise_check":
            left = self._open_premises()
            if left:
                q = left[0]
                return [ask(q["question"], [(o, "", "") for o in q["options"]], cue="先核对前提", selected=None,
                            act={"url": "/settle/premise", "fields": {"q": self._ptok(q)}})]  # 只带带盐哈希：绑定到这道题，不含原文
            prop = s.propose_outcome()
            if prop.get("reused"):
                s.confirm_outcome()
            else:
                beh, _ = s._behavior_for()
                cats = list(beh.categories) if beh.categories else [True, False]
                sel = cats.index(prop["outcome"]) if prop.get("outcome") in cats else (len(cats) if prop.get("observable") is False else None)
                basis = prop.get("basis")
                return [ask("这次结果算哪一种？", [(str(c), "", "") for c in cats] + [("无法判断", "", "")],
                            cue=f"依据：{basis}" if basis else "", selected=sel, act={"url": "/settle/outcome", "fields": {}})]
        if s.state == "settled":
            pending = {d["id"] for d in self.lock.store.drafts(status="pending")}
            mine = self.pending_steps(only=set(s.report_drafts) & pending)
            if mine:
                return mine  # 报告里的草稿先由你逐条确认，系统不替你确认
            if self.sess_review is None:
                self.sess_review = s.review(self.model, confirm_drafts=False)
            cards = self.sess_review["cards"][:1]
            if cards:
                scr = {"open_question": "", "matter": "", "meeting_time": ""}
                done = build_settle_steps(scr, (), None, cards)[-1]
                done["act"] = {"url": "/settle/done", "fields": {}}
                return [done]
        return []

    def settle_done(self) -> None:
        if self.sess is None or self.sess.state != "settled":
            raise KeyError("done")
        self.sess, self.sess_hours, self.sess_review = None, None, None

    def settle_start(self, mid: str) -> None:
        got = self._settle_target()
        if got is None or mid not in self._world().matters or not self._settleable():
            raise KeyError(mid)
        groups = self._groups()
        self.sess_hours, self.sess_review = None, None
        if len(groups) == 1:
            self._open_session(groups[0])
        else:
            self.settle_pick = groups  # 多组：先选，不替你选

    def _open_session(self, group) -> None:
        iid, x = self._settle_target()
        self.sess = SettlementSession(self.intake, self.current_mid(), iid, self.model, x.get("label", ""),
                                      behavior_key=group[0], def_version=group[1])
        self.settle_pick = None

    def settle_pick_one(self, choice: str, q: str) -> None:
        g = self.settle_pick
        if not g or self.sess is not None or not hmac.compare_digest(q, self.tok("pick", g)) \
                or not choice.isdigit() or int(choice) >= len(g):
            raise KeyError("pick")
        self._open_session(g[int(choice)])

    def settle_when(self, choice: str) -> None:
        if self.sess is None or self.sess.state != "awaiting_report" or choice not in ("0", "1"):
            raise KeyError("when")
        self.sess_hours = 0.0 if choice == "0" else 72.0

    def settle_report(self, text: str) -> None:
        if self.sess is None or self.sess.state != "awaiting_report" or self.sess_hours is None or not text.strip():
            raise KeyError("report")
        self.sess.submit_report(self.CID, text.strip(), hours_since_event=self.sess_hours)

    def _ptok(self, q: dict) -> str:
        return self.tok("premise", q["premise_id"], q["anchor_event"], q["cutoff"])

    def settle_premise(self, choice: str, qtok: str = "") -> None:
        if self.sess is None or self.sess.state != "premise_check":
            raise KeyError("premise")
        q = next(iter(self._open_premises()), None)
        if q is None or not hmac.compare_digest(qtok, self._ptok(q)):  # 重复提交或旧页面：不替你回答下一题
            raise KeyError("premise")
        if q is None or not choice.isdigit() or int(choice) >= len(q["options"]):
            raise KeyError("premise")
        self.sess.answer_premise(q["premise_text"], q["options"][int(choice)], cutoff=q["cutoff"], anchor_event=q["anchor_event"])
        self.lock.store.sync_world(self._world())

    def settle_outcome(self, choice: str) -> None:
        s = self.sess
        if s is None or s.state != "premise_check" or self._open_premises():
            raise KeyError("outcome")
        beh, _ = s._behavior_for()
        cats = list(beh.categories) if beh.categories else [True, False]
        if not choice.isdigit() or int(choice) > len(cats):
            raise KeyError("outcome")
        i = int(choice)
        s.confirm_outcome(unobservable=True) if i == len(cats) else s.confirm_outcome(cats[i])

    # —— 锁定预测：类别 → 各解释指向哪类 → 倾向 → 把握 →（前提 → 截止）→ 看一眼再锁 ——
    def _lk_pm(self):
        lk = self.lk
        pm = self.pms.get(lk["mid"]) if lk else None
        if pm is None or pm.prep is None or pm.prep.category_draft is None:
            raise KeyError("lock")
        return lk, pm

    def _unformal(self, pm) -> list:
        w = self._world()
        return [h for h in pm.prep.hypothesis_for.values() if h in w.hypotheses and not w.hypotheses[h].formal]

    def lock_steps(self) -> list:
        if self.lk is None:
            return []
        lk, pm = self._lk_pm()
        w, cats = self._world(), list(pm.prep.category_draft.categories)
        crit = dict(pm.prep.category_draft.category_criteria)
        n = lambda k: {"url": "/lock/next", "fields": {"k": k}}  # noqa: E731
        if pm.behavior is None:
            return [ask("会后按这几类结算，可以吗？", [("按这几类", "", " / ".join(cats)), ("先不锁定", "", "")],
                        cue="；".join(f"{c}：{crit.get(c, '')}" for c in cats), selected=0, act=n("cats"))]
        left = [h for h in self._unformal(pm) if h not in lk["hyp"]]
        if left:
            h = w.hypotheses[left[0]]
            mark = "you" if h.proposed_by == "user" else "model"
            return [ask(f"如果“{h.statement}”是对的，他会先问哪一类？", [(c, "", crit.get(c, "")) for c in cats],
                        cue="你的判断" if mark == "you" else "另一种可能", selected=None, act={**n("hyp"), "fields": {"k": "hyp", "hid": h.id}})]
        if lk.get("lean") is None:
            return [ask("你觉得最可能是哪一种？", [(c, "", crit.get(c, "")) for c in cats], cue="凭直觉选", selected=None, act=n("lean"))]
        if lk.get("conf") is None:
            top = cats[lk["lean"]]
            opts = [(name, "", " · ".join(f"{c} {round(v * 100)}%" for c, v in profile(cats, top, p).items())) for name, p in CONFIDENCE]
            return [ask("你有多大把握？", opts, cue="其余的程序按比例分好，不用你算", selected=None, act=n("conf"))]
        po = pm.prep.premise_options
        if po and "premise" not in lk:
            return [ask("这个预测，在什么情况下才算数？", [(f"{x['anchor_event']}之前，我没有{x['action']}", "", "") for x in po] + [("没有前提", "", "任何情况下都算")],
                        cue="前提没成立，这次就不计分", selected=None, act=n("premise"))]
        if lk.get("premise") is not None and "until" not in lk:
            x = po[lk["premise"]]
            return [ask(f"如果{x['anchor_event']}一直没有发生，算到什么时候？", [("到会议结束", "", ""), ("到会后 24 小时", "", "")],
                        cue="写清截止点，会后才不会含糊", selected=None, act=n("until"))]
        dist, prem = self._lk_build(pm)
        beh = pm.prep.category_draft
        rows = [("把握", " · ".join(f"{c} {round(v * 100)}%" for c, v in dist.items())),
                ("前提", prem.text if prem else "没有前提"), ("截止", prem.cutoff if prem else "—"), ("怎么算", beh.description)]
        return [{"kind": "summary", "cue": "锁定前看一眼", "title": f"最可能：{cats[lk['lean']]}", "rows": rows,
                 "note": "锁定后不能改。会后按这里写的标准结算。",
                 "act": {"url": "/lock/commit", "fields": {}}, "back": {"url": "/lock/restart", "fields": {}}}]

    def _lk_build(self, pm):
        lk, cats = self.lk, list(pm.prep.category_draft.categories)
        dist = profile(cats, cats[lk["lean"]], CONFIDENCE[lk["conf"]][1])
        prem = None
        if lk.get("premise") is not None:
            x = pm.prep.premise_options[lk["premise"]]
            until = ("以会议结束", "以会后 24 小时")[lk["until"]]
            prem = make_premise(f"PR-{len(self._world().engine.preds) + 1}", x["action"], x["anchor_event"], until)
        return dist, prem

    def lock_start(self, mid: str) -> None:
        pm = self.pms.get(mid)
        if pm is None or pm.prep is None or pm.prep.category_draft is None:
            raise KeyError(mid)
        self.lk = {"mid": mid, "hyp": {}}

    def lock_next(self, k: str, choice: str, hid: str = "") -> None:
        lk, pm = self._lk_pm()
        cats = list(pm.prep.category_draft.categories)
        if not choice.isdigit():
            raise KeyError(k)
        i = int(choice)
        if k == "cats" and pm.behavior is None and i in (0, 1):
            if i == 1:
                self.lk = None
                return
            pm.confirm_categories()
        elif k == "hyp" and pm.behavior is not None and hid in self._unformal(pm) and hid not in lk["hyp"] and i < len(cats):
            lk["hyp"][hid] = cats[i]
            if not [h for h in self._unformal(pm) if h not in lk["hyp"]]:  # 都选完了才形式化，进入比较
                pm.formalize({h: profile(cats, c, RULE_P) for h, c in lk["hyp"].items()})
        elif k == "lean" and pm.behavior is not None and not self._unformal(pm) and i < len(cats):
            lk["lean"] = i
        elif k == "conf" and lk.get("lean") is not None and i < len(CONFIDENCE):
            lk["conf"] = i
        elif k == "premise" and lk.get("conf") is not None and "premise" not in lk and i <= len(pm.prep.premise_options):
            lk["premise"] = None if i == len(pm.prep.premise_options) else i
        elif k == "until" and lk.get("premise") is not None and "until" not in lk and i in (0, 1):
            lk["until"] = i
        else:
            raise KeyError(k)
        self.lock.store.sync_world(self._world())

    def lock_restart(self) -> None:
        lk, _ = self._lk_pm()
        self.lk = {"mid": lk["mid"], "hyp": lk["hyp"]}  # 类别与各解释已形式化（进入比较）不回退；其余重选

    def lock_commit(self) -> None:
        lk, pm = self._lk_pm()
        if lk.get("conf") is None or (pm.prep.premise_options and "premise" not in lk) or (lk.get("premise") is not None and "until" not in lk):
            raise KeyError("commit")
        dist, prem = self._lk_build(pm)
        pm.lock_prediction(dist, prem)
        self.lk = None

    # —— 写操作（每个都先确认已解锁；落库后 sync_world）——
    def clarify_steps(self) -> list:
        """模型上一轮留下的追问：显式呈现，可以回答也可以跳过；只有这里的回答才会被当成对它的回答。"""
        p = self.lock.store.get_pending(self.CID)
        if not p or not p.get("question"):
            return []
        return [open_ask(p["question"], cue="补充一下（可以跳过）", act={"url": "/say", "fields": {"answer": "1"}},
                         skip={"url": "/clarify/skip", "fields": {}})]

    def clarify_skip(self) -> None:
        self.lock.store.set_pending(self.CID, None)

    def say(self, text: str, answering: bool = False, who: str = "", newname: str = "", scenes: str = "", newscene: str = "") -> None:
        text = text.strip()
        if text and self.intake is not None:
            about = self._who(who, newname) if (who or newname.strip()) else None
            sc = self._scenes(scenes, newscene)
            if not answering:  # 独立的“记一句”不是回答：不续接上一个追问，也不并进同一个互动
                self.lock.store.set_pending(self.CID, None)
            r = self.intake.handle(self.CID, text, about=about, scenes=sc)
            self.lock.store.sync_world(self._world())
            if r.fallback:
                why = self.intake.last_error
                print(f"[say] 模型没能解析：{why}", file=__import__("sys").stderr, flush=True)  # 只记原因，不记原话
                self.notice = "模型没能解析这句话（原话已加密保存）。换种说法再试一次；如果反复出现，检查密钥或网络。" + (f"（{why}）" if why else "")
            elif not r.draft_ids:
                self.notice = "没有从这句话里整理出可以记下的内容。可以说得更具体一点。"

    def confirm(self, did: str, choice: str) -> None:
        store = self.lock.store
        if not any(d["id"] == did and d["status"] == "pending" for d in store.drafts()):
            raise KeyError(did)
        if choice == "0":
            d = next(x for x in store.drafts() if x["id"] == did)
            ref = self.intake.confirm(did)
            store.sync_world(self._world())
            self._start_reading([(d, ref)])
            return
        self.intake.reject(did)
        store.sync_world(self._world())

    def act(self, mid: str) -> None:
        pm = self.pms.get(mid)
        if pm is None or pm.prep is None:
            raise KeyError(mid)
        pm.record_action(self._world().matters[mid].purpose, [it["text"] for it in pm.prep.items])
        self.lock.store.sync_world(self._world())


def make_handler(app: App):
    class H(http.server.BaseHTTPRequestHandler):
        server_version = "pr"
        sys_version = ""

        def log_message(self, fmt, *a):  # 不记查询串与表单
            pass

        # —— 公共 ——
        def _send(self, code: int, body: str = "", ctype: str = "text/html; charset=utf-8", headers: tuple = ()):
            b = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            if not any(k == "Cache-Control" for k, _ in headers):
                self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")  # 不向外站泄露地址；同源请求照常带 Origin
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(b)

        def _redirect(self, to: str, headers: tuple = ()):
            self.send_response(303)
            self.send_header("Location", to)
            self.send_header("Cache-Control", "no-store")
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _host_ok(self) -> bool:
            h = (self.headers.get("Host") or "").lower()
            name = h.rsplit(":", 1)[0].strip("[]") if not h.startswith("[") else h.split("]")[0].strip("[")
            return is_loopback(name) or h in app.allowed_hosts or name in app.allowed_hosts

        def _same_origin(self) -> bool:
            """写请求必须来自本站。优先看浏览器自己填的 Sec-Fetch-Site（页面脚本改不了）：same-origin 才放行。
            没有这个头的旧浏览器，退回比较 Origin 与 Host；经反向代理时 Host 可能被改写成 localhost，Origin 在白名单里也放行。
            Origin 为 null（浏览器在某些隐私设置下会这样）且没有 Sec-Fetch-Site 时拒绝。"""
            sfs = self.headers.get("Sec-Fetch-Site")
            if sfs:
                return sfs == "same-origin"
            o = self.headers.get("Origin")
            if o is None:
                return True
            netloc = urlsplit(o).netloc.lower()
            host = (self.headers.get("Host") or "").lower()
            return netloc == host or netloc in app.allowed_hosts or netloc.rsplit(":", 1)[0] in app.allowed_hosts

        def _cookie(self) -> str:
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "sid":
                    return v
            return ""

        def _secure(self) -> str:
            return "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""

        # —— 路由 ——
        def do_GET(self):
            if not self._host_ok():
                return self._send(400, "bad host", "text/plain")
            path = urlsplit(self.path).path
            if path == "/healthz":
                return self._send(200, "ok", "text/plain")
            if path == "/manifest.webmanifest":
                return self._send(200, MANIFEST, "application/manifest+json")
            if path == "/fonts.css" or path.startswith("/fonts/"):  # 字体不含任何人物信息：可缓存，解锁前也可取
                import re as _re
                from .theme import FONTS_CSS_FILE, FONTS_DIR
                if path == "/fonts.css":
                    b, ct = FONTS_CSS_FILE.read_bytes(), "text/css; charset=utf-8"
                else:
                    name = path[len("/fonts/"):]
                    f = FONTS_DIR / name
                    if not _re.fullmatch(r"noto-serif-sc-(\d+|latin|latin-ext)-wght-normal\.woff2", name) or not f.is_file():
                        return self._send(404, "not found", "text/plain")
                    b, ct = f.read_bytes(), "font/woff2"
                self.send_response(200)
                for k, val in (("Content-Type", ct), ("Content-Length", str(len(b))), ("Cache-Control", "public, max-age=86400"),
                               ("X-Content-Type-Options", "nosniff"), ("Content-Security-Policy", CSP)):
                    self.send_header(k, val)
                self.end_headers()
                self.wfile.write(b)
                return
            if path == "/icon.svg":
                return self._send(200, ICON, "image/svg+xml")
            if path == "/unlock":
                return self._send(200, unlock_page())
            if not app.authed(self._cookie()):
                return self._redirect("/unlock")
            try:
                if path == "/":
                    with app.mutex:
                        body = app.flow_html()
                    return self._send(200, body)
                if path == "/say":
                    with app.mutex:
                        body = app.say_html((parse_qs(urlsplit(self.path).query).get("who") or [""])[0])
                    return self._send(200, body)
                if path == "/people":
                    with app.mutex:
                        body = app.people_html()
                    return self._send(200, body)
                if path.startswith("/person/"):
                    with app.mutex:
                        body = app.person_html(path[len("/person/"):])
                    return self._send(200, body)
                if path == "/library":
                    with app.mutex:
                        body = app.library_html()
                    return self._send(200, body)
            except Locked:
                return self._redirect("/unlock")
            except KeyError:
                return self._send(404, "not found", "text/plain")
            return self._send(404, "not found", "text/plain")

        def do_POST(self):
            if not self._host_ok():
                return self._send(400, "bad host", "text/plain")
            if not self._same_origin():
                return self._send(403, "cross-origin", "text/plain")
            path = urlsplit(self.path).path
            n = min(int(self.headers.get("Content-Length") or 0), 4096)
            form = parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
            if path == "/unlock":
                try:
                    with app.mutex:
                        app.lock.unlock((form.get("passphrase") or [""])[0])
                        app.on_unlock()
                        app.token = secrets.token_urlsafe(32)
                        tok = app.token
                except WrongPassphrase:
                    return self._send(401, unlock_page("口令不对"))
                except TooManyAttempts as e:
                    return self._send(429, unlock_page(str(e)))
                return self._redirect("/", (("Set-Cookie", f"sid={tok}; HttpOnly; SameSite=Strict; Path=/{self._secure()}"),))
            if not app.authed(self._cookie()):
                return self._send(401, "locked", "text/plain")
            if path == "/lock":
                with app.mutex:
                    app.lock.lock()
                    app.on_lock()
                    app.token = None
                return self._redirect("/unlock", (("Set-Cookie", "sid=; Max-Age=0; Path=/"),))
            if path == "/mask":
                app.masked = not app.masked
                return self._redirect("/")
            try:
                with app.mutex:
                    if path == "/say":
                        app.say((form.get("text") or [""])[0][:4000], answering=(form.get("answer") or [""])[0] == "1",
                                who=(form.get("who") or [""])[0], newname=(form.get("newname") or [""])[0],
                                scenes=(form.get("scenes") or [""])[0], newscene=(form.get("newscene") or [""])[0])
                    elif path == "/clarify/skip":
                        app.clarify_skip()
                    elif path == "/confirm_many":
                        app.confirm_many((form.get("msg") or [""])[0], (form.get("ids") or [""])[0])
                    elif path == "/confirm":
                        app.confirm((form.get("id") or [""])[0], (form.get("choice") or ["1"])[0])
                    elif path == "/act":
                        app.act(app.current_mid())
                    elif path == "/settle/start":
                        app.settle_start(app.current_mid())
                    elif path in ("/reading/next", "/reading/pick", "/reading/signal", "/reading/fact"):
                        app.reading_next({"/reading/fact": "fact", "/reading/next": "view", "/reading/pick": "pick", "/reading/signal": "signal"}[path],
                                         (form.get("choice") or [""])[0])
                    elif path == "/reading/start":
                        app.reading_start()
                    elif path == "/reading/watch":
                        app.reading_watch((form.get("choice") or [""])[0])
                    elif path == "/reading/who":
                        app.reading_who((form.get("choice") or [""])[0])
                    elif path == "/reading/done":
                        app.reading_done()
                    elif path == "/lock/start":
                        app.lock_start(app.current_mid())
                    elif path == "/lock/next":
                        app.lock_next((form.get("k") or [""])[0], (form.get("choice") or [""])[0], (form.get("hid") or [""])[0])
                    elif path == "/lock/restart":
                        app.lock_restart()
                    elif path == "/lock/commit":
                        app.lock_commit()
                    elif path == "/settle/done":
                        app.settle_done()
                    elif path == "/settle/when":
                        app.settle_when((form.get("choice") or [""])[0])
                    elif path == "/settle/report":
                        app.settle_report((form.get("text") or [""])[0][:4000])
                    elif path == "/settle/premise":
                        app.settle_premise((form.get("choice") or [""])[0], (form.get("q") or [""])[0])
                    elif path == "/settle/pick":
                        app.settle_pick_one((form.get("choice") or [""])[0], (form.get("q") or [""])[0])
                    elif path == "/settle/outcome":
                        app.settle_outcome((form.get("choice") or [""])[0])
                    else:
                        return self._send(404, "not found", "text/plain")
            except (KeyError, Locked, EngineError):
                return self._send(409, "not applicable", "text/plain")
            return self._redirect("/")
            return self._send(404, "not found", "text/plain")
    return H


def serve(path: str, host: str = "127.0.0.1", port: int = 8765, *, allow_remote: bool = False, allowed_hosts=(),
          idle_seconds: float = 300, model=None) -> http.server.ThreadingHTTPServer:
    """返回已绑定的服务器（调用方 serve_forever）。非回环地址必须 allow_remote=True，且要在 allowed_hosts 里列出访问用的主机名。"""
    if not is_loopback(host) and not allow_remote:
        raise ValueError("监听非本机地址需要显式 allow_remote=True（并放在 Tailscale 等私有加密通道之后）")
    app = App(path, allowed_hosts=allowed_hosts, idle_seconds=idle_seconds, model=model)
    srv = http.server.ThreadingHTTPServer((host, port), make_handler(app))
    srv.app = app  # type: ignore[attr-defined]
    return srv


if __name__ == "__main__":  # python -m people_reading.server --db my.db [--host 100.x.y.z --allow-remote --allow-host name:8765]
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--visible", action="store_true", help="--init 时口令回显（iPad 等输入看不见、容易输错的场景）")
    ap.add_argument("--init", action="store_true", help="创建新的加密库（口令用 getpass 输入，不进命令行历史），然后退出")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--allow-remote", action="store_true")
    ap.add_argument("--allow-host", action="append", default=[])
    a = ap.parse_args()
    import os
    if a.init:
        import getpass
        from .store import Store
        if os.path.exists(a.db):
            raise SystemExit("库已存在，不覆盖：" + a.db)
        if a.visible:
            p1 = input("设置口令（会显示出来，注意旁边没人，≥10 位）：").strip()
            print(f"你输入的是 {len(p1)} 位：{p1}")
            if input("确认无误请输入 y：").strip().lower() != "y":
                raise SystemExit("已取消")
            p2 = p1
        else:
            p1, p2 = getpass.getpass("设置口令："), getpass.getpass("再输一次：")
        if p1 != p2:
            raise SystemExit(f"两次不一致（第一次 {len(p1)} 位，第二次 {len(p2)} 位）。iPad 常见原因：首字母被自动大写、末尾多了空格；可加 --visible 让口令显示出来")
        if len(p1) < 10:
            raise SystemExit(f"口令只有 {len(p1)} 位，至少 10 位")
        Store(a.db, p1).close()
        os.chmod(a.db, 0o600)
        raise SystemExit("已创建 " + a.db)
    if not os.path.exists(a.db):  # 否则第一个打开页面的人就能给新库设口令
        raise SystemExit("库不存在。先运行：python -m people_reading.server --db %s --init" % a.db)
    mdl = make_model()  # PR_PROVIDER=local（默认）| deepseek；deepseek 还需 PR_ALLOW_EXTERNAL=1 与 DEEPSEEK_API_KEY
    cs = codespaces_host(dict(os.environ), a.port)
    if cs:
        a.allow_host.append(cs)  # 只放行这个 Codespace 的转发域名；端口默认私有，只有你登录的 GitHub 账号能打开
    s = serve(a.db, a.host, a.port, allow_remote=a.allow_remote, allowed_hosts=a.allow_host, model=mdl)
    if cs:
        print(f"打开： https://{cs}")
    print(f"http://{a.host}:{a.port}  （口令在浏览器里输入，不经命令行）  模型：{mdl!r}")
    s.serve_forever()
