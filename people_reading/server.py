"""本地服务：把加密存储、口令锁、一问一答页面连成一个能在手机浏览器里用的 app（可“添加到主屏幕”）。

安全模型（单用户）：
  - 默认只监听 127.0.0.1；监听其它地址必须显式 allow_remote=True（应放在 Tailscale 等私有加密通道之后，不要暴露公网）。
  - 解锁前任何页面都不返回数据；口令错误计数与冷却由 AppLock 负责；空闲超时自动上锁，上锁后会话失效。
  - 会话是随机令牌（HttpOnly、SameSite=Strict）；POST 还要求同源（Origin/Host 一致）；Host 头必须在白名单里（防 DNS 重绑定）。
  - 所有响应 Cache-Control: no-store；不注册 service worker（不在手机上缓存任何人物信息）；CSP 禁止一切外链。
  - 密钥与口令不进日志；访问日志只记方法与路径类别，不记查询串与表单。
"""
from __future__ import annotations

import html
import http.server
import ipaddress
import secrets
import threading
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from .crypto import WrongPassphrase
from .flow import build_prep_steps, open_ask, render_flow
from .privacy import AppLock, Locked, Redactor, TooManyAttempts
from .shell import render_app
from .workbench import workbench_view

E = html.escape
CSP = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data:; "
       "manifest-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
MANIFEST = ('{"name":"识人","short_name":"识人","start_url":"/","display":"standalone","background_color":"#f4f1e9",'
            '"theme_color":"#1f3b61","icons":[{"src":"/icon.svg","sizes":"any","type":"image/svg+xml","purpose":"any"}]}')
ICON = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><rect width='64' height='64' fill='#14243d'/>"
        "<g fill='none' stroke='#f4f1e9' stroke-width='2'><circle cx='32' cy='38' r='22'/><circle cx='32' cy='38' r='15'/><circle cx='32' cy='38' r='8'/></g></svg>")
HEAD_EXTRA = "<link rel='manifest' href='/manifest.webmanifest'><meta name='theme-color' content='#1f3b61'><link rel='icon' href='/icon.svg'>"


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def _page(body: str, title: str = "识人") -> str:
    from .flow import FLOW_CSS
    return (f"<!doctype html><html lang='zh'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
            f"<title>{E(title)}</title><meta name='privacy-capture' content='deny'>{HEAD_EXTRA}<style>{FLOW_CSS}"
            f".gate{{max-width:420px;margin:0 auto;padding:96px 24px;display:flex;flex-direction:column;gap:24px;height:auto}}"
            f"input{{font:inherit;font-size:20px;padding:14px 16px;border:1px solid var(--line-strong);border-radius:2px;background:var(--surface-raised);color:var(--ink);width:100%}}"
            f"</style></head><body>{body}</body></html>")


def unlock_page(msg: str = "") -> str:
    return _page("<form class='gate' method='post' action='/unlock'><span class='brand'>识人</span><h1 style='font-size:28px;margin:0'>输入口令</h1>"
                 "<input type='password' name='passphrase' autocomplete='current-password' aria-label='口令' autofocus>"
                 + (f"<p class='why'>{E(msg)}</p>" if msg else "") + "<button class='btn p' type='submit' style='justify-content:center'>解锁</button></form>")


class App:
    """进程内状态：一把锁、一个会话令牌、一个遮挡开关。"""

    def __init__(self, path: str, *, allowed_hosts=(), idle_seconds: float = 300, **kw) -> None:
        self.lock = AppLock(path, idle_seconds=idle_seconds, **kw)
        self.token: Optional[str] = None
        self.masked = False
        self.allowed_hosts = {h.lower() for h in allowed_hosts}
        self.mutex = threading.Lock()

    def authed(self, cookie: str) -> bool:
        with self.mutex:
            if self.lock.locked:  # 空闲超时 → 会话失效
                self.token = None
                return False
            return bool(self.token) and secrets.compare_digest(cookie or "", self.token)

    def flow_html(self) -> str:
        store = self.lock.store
        w, _ = store.load_world()
        rd = Redactor(w, "masked" if self.masked else "code")
        steps = []
        if w.matters:
            mid = list(w.matters)[-1]
            steps = build_prep_steps(workbench_view(w, mid, store))
        steps = steps or [open_ask("今天想记一件什么事？", cue="直接说，不用先选人或填表")]
        return render_flow(steps, rd=rd, library_href="/library", head_extra=HEAD_EXTRA)

    def library_html(self) -> str:
        store = self.lock.store
        w, _ = store.load_world()
        return render_app(w, store, mode="masked" if self.masked else "code", toggle_href="")


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
                    return self._send(200, app.flow_html())
                if path == "/library":
                    return self._send(200, app.library_html())
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
                    app.token = None
                return self._redirect("/unlock", (("Set-Cookie", "sid=; Max-Age=0; Path=/"),))
            if path == "/mask":
                app.masked = not app.masked
                return self._redirect("/")
            return self._send(404, "not found", "text/plain")
    return H


def serve(path: str, host: str = "127.0.0.1", port: int = 8765, *, allow_remote: bool = False, allowed_hosts=(),
          idle_seconds: float = 300) -> http.server.ThreadingHTTPServer:
    """返回已绑定的服务器（调用方 serve_forever）。非回环地址必须 allow_remote=True，且要在 allowed_hosts 里列出访问用的主机名。"""
    if not is_loopback(host) and not allow_remote:
        raise ValueError("监听非本机地址需要显式 allow_remote=True（并放在 Tailscale 等私有加密通道之后）")
    app = App(path, allowed_hosts=allowed_hosts, idle_seconds=idle_seconds)
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
    s = serve(a.db, a.host, a.port, allow_remote=a.allow_remote, allowed_hosts=a.allow_host)
    print(f"http://{a.host}:{a.port}  （口令在浏览器里输入，不经命令行）")
    s.serve_forever()
