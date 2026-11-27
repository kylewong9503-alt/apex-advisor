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
from .flow import build_settle_steps, ask, build_prep_steps, open_ask, render_flow
from .intake import Intake
from .llm import RemoteModel, make_model
from .engine import EngineError
from .premeeting import CONFIDENCE, RULE_P, PreMeeting, make_premise, profile
from .settle_flow import SettlementSession
from .privacy import AppLock, Locked, Redactor, TooManyAttempts
from .shell import render_app
from .theme import BRAND, mark_svg

ICON = mark_svg()
from .workbench import workbench_view

E = html.escape
CSP = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data:; "
       "manifest-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
MANIFEST = ('{"name":"Kagami","short_name":"Kagami","start_url":"/","display":"standalone","background_color":"#f4f1e9",'
            '"theme_color":"#1f3b61","icons":[{"src":"/icon.svg","sizes":"any","type":"image/svg+xml","purpose":"any"}]}')
HEAD_EXTRA = "<link rel='manifest' href='/manifest.webmanifest'><meta name='theme-color' content='#1f3b61'><link rel='icon' href='/icon.svg'>"


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def _page(body: str, title: str = BRAND) -> str:
    from .flow import FLOW_CSS
    return (f"<!doctype html><html lang='zh'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
            f"<title>{E(title)}</title><meta name='privacy-capture' content='deny'>{HEAD_EXTRA}<style>{FLOW_CSS}"
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

    def pending_steps(self) -> list:
        """待确认的草稿：每条一个问题。原话片段由程序定位，这里展示的是原话，不是模型的转述。"""
        store, steps = self.lock.store, []
        for d in store.drafts(status="pending", conversation_id=self.CID):
            quote = " … ".join(store.quote(sp) for sp in d["source_spans"])
            steps.append(ask("记下这一条吗？", [("记下", "", ""), ("不记", "", "")], cue=f"原话：{quote}", selected=0,
                             act={"url": "/confirm", "fields": {"id": d["id"]}}))
        return steps

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
        steps = self.settle_steps() or self.lock_steps() or self.pending_steps()
        if not steps and w.matters:
            mid = list(w.matters)[-1]
            pm = self.pre_meeting(mid)
            v = workbench_view(w, mid, store, prep=pm.prep if pm else None)
            steps = build_prep_steps(v)
            if pm and steps and steps[-1]["kind"] == "answer":
                lockb = [{"label": "锁定预测", "act": {"url": "/lock/start", "fields": {}, "reload": True}}] \
                    if pm.prep.category_draft is not None else []
                steps[-1]["buttons"] = [{"label": "记为行动", "act": {"url": "/act", "fields": {}}}] + lockb + \
                    ([{"label": "会后", "act": {"url": "/settle/start", "fields": {}, "reload": True}}] if self._settleable() else [])
        steps = steps or [open_ask("今天想记一件什么事？", cue="直接说，不用先选人或填表", act={"url": "/say", "fields": {}})]
        return render_flow(steps, rd=rd, library_href="/library", head_extra=HEAD_EXTRA, banner=self.banner())

    def library_html(self) -> str:
        store = self.lock.store
        return render_app(self._world(), store, mode="masked" if self.masked else "code", toggle_href="")

    # —— 会后结算：每一步都是服务端状态机的下一步；首份报告之前不出现任何候选答案 ——
    def current_mid(self) -> str:
        w = self._world()
        return list(w.matters)[-1] if w.matters else ""

    def _settleable(self) -> bool:
        """只有计划会谈上有锁定的预测，才出现“会后”：否则报告无法结算。"""
        got = self._planned()
        if got is None or got[0] not in self._world().engine.opps:
            return False
        return bool(self._groups())

    def _groups(self) -> list:
        """这场会上待结算的（行为，版本）组。"""
        got = self._planned()
        if got is None or got[0] not in self._world().engine.opps:
            return []
        try:
            ps = SettlementSession(self.intake, self.current_mid(), got[0], None).matched()
        except Exception:
            return []
        return sorted({(p.pre.behavior_key, p.pre.def_version) for p in ps})

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
            mine = [x for x in self.pending_steps() if x["act"]["fields"]["id"] in pending and x["act"]["fields"]["id"] in s.report_drafts]
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
        got = self._planned()
        if got is None or mid not in self._world().matters or not self._settleable():
            raise KeyError(mid)
        groups = self._groups()
        self.sess_hours, self.sess_review = None, None
        if len(groups) == 1:
            self._open_session(groups[0])
        else:
            self.settle_pick = groups  # 多组：先选，不替你选

    def _open_session(self, group) -> None:
        iid, x = self._planned()
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
    def say(self, text: str) -> None:
        text = text.strip()
        if text and self.intake is not None:
            self.intake.handle(self.CID, text)
            self.lock.store.sync_world(self._world())

    def confirm(self, did: str, choice: str) -> None:
        store = self.lock.store
        if not any(d["id"] == did and d["status"] == "pending" for d in store.drafts()):
            raise KeyError(did)
        if choice == "0":
            self.intake.confirm(did)
        else:
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
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
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
            o = self.headers.get("Origin")
            return o is None or urlsplit(o).netloc.lower() == (self.headers.get("Host") or "").lower()

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
                if path == "/library":
                    with app.mutex:
                        body = app.library_html()
                    return self._send(200, body)
            except Locked:
                return self._redirect("/unlock")
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
                        app.say((form.get("text") or [""])[0][:4000])
                    elif path == "/confirm":
                        app.confirm((form.get("id") or [""])[0], (form.get("choice") or ["1"])[0])
                    elif path == "/act":
                        app.act(app.current_mid())
                    elif path == "/settle/start":
                        app.settle_start(app.current_mid())
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
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--allow-remote", action="store_true")
    ap.add_argument("--allow-host", action="append", default=[])
    a = ap.parse_args()
    mdl = make_model()  # PR_PROVIDER=local（默认）| deepseek；deepseek 还需 PR_ALLOW_EXTERNAL=1 与 DEEPSEEK_API_KEY
    s = serve(a.db, a.host, a.port, allow_remote=a.allow_remote, allowed_hosts=a.allow_host, model=mdl)
    print(f"http://{a.host}:{a.port}  （口令在浏览器里输入，不经命令行）  模型：{mdl!r}")
    s.serve_forever()
