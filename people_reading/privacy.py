"""隐私（6.4）。

显示模式：
  plain   不处理（旧的单页渲染器默认，保持原行为）
  code    全局默认：人物显示代号，自由文本里出现的姓名也换成代号
  names   用户明确选择显示姓名
  masked  一键遮挡：姓名/代号、角色、关系标签、议题标题、原话预览、判断内容全部隐藏（默认拒绝：
          渲染器里每个动态字符串都要经过 Redactor，遮挡时一律不输出原文，而不是靠 CSS 盖住）

解锁：应用重新打开需要口令；空闲超时自动上锁；连续输错有冷却。
防截屏：页面带 data-no-capture 标记与打印时隐藏；系统级禁截屏需要原生外壳（见 NATIVE_FLAGS），网页本身做不到。
"""
from __future__ import annotations

import re
import time
from typing import Callable, Optional

from .crypto import WrongPassphrase
from .store import Store
from .world import SELF, World

MASK = "●●●"
NATIVE_FLAGS = {"android": "WindowManager.LayoutParams.FLAG_SECURE", "ios": "监听 UIScreen.isCaptured 并遮盖",
                "macos": "NSWindow.sharingType = .none", "windows": "SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)",
                "electron": "win.setContentProtection(true)"}
NO_CAPTURE_META = '<meta name="privacy-capture" content="deny">'
PRINT_GUARD_CSS = "@media print{body *{display:none!important}body::before{content:'隐私视图禁止打印';display:block}}"


class Redactor:
    def __init__(self, w: World, mode: str = "code") -> None:
        if mode not in ("plain", "code", "names", "masked"):
            raise ValueError("mode 必须是 plain/code/names/masked")
        self.w, self.mode = w, mode
        self._ord = {pid: chr(ord("A") + i) if i < 26 else str(i)
                     for i, pid in enumerate(sorted(p for p in w.persons if p != SELF))}
        names = sorted(((p.display_name, p.shown_code) for p in w.persons.values()
                        if p.id != SELF and p.display_name and p.display_name != p.shown_code), key=lambda x: -len(x[0]))
        self._mord = {m: i + 1 for i, m in enumerate(sorted(w.matters))}
        self._pord = {m: i + 1 for i, m in enumerate(sorted(w.engine.preds))}
        self._pat = re.compile("|".join(re.escape(n) for n, _ in names)) if names else None
        self._to_code = dict(names)

    @property
    def masked(self) -> bool:
        return self.mode == "masked"

    def p(self, pid: str) -> str:
        """人物。"""
        if pid == SELF:
            return "我"
        person = self.w.persons.get(pid)
        if self.mode == "masked":
            return f"●{self._ord.get(pid, '')}"
        if person is None:
            return pid
        if self.mode in ("names", "plain"):
            return person.display_name
        return person.shown_code

    def ref(self, pid: str) -> str:
        """写进 URL / data 属性的人物引用。遮挡时用不透明引用，避免 ID（常常就是代号）泄漏进 DOM。"""
        return f"p{self._ord.get(pid, '')}" if self.mode == "masked" else pid

    def mref(self, matter_id: str) -> str:
        """议题 ID（常常就是议题名）。遮挡时用不透明引用。"""
        return f"mx{self._mord.get(matter_id, 0)}" if self.mode == "masked" else matter_id

    def pref(self, prediction_id: str) -> str:
        """预测 ID。遮挡时用不透明引用。"""
        return f"px{self._pord.get(prediction_id, 0)}" if self.mode == "masked" else prediction_id

    def t(self, s: str) -> str:
        """自由文本（判断内容、原话预览、角色表述、议题标题、关系标签……）。"""
        if s is None:
            return ""
        if self.mode == "masked":
            return MASK
        if self.mode == "code" and self._pat:
            return self._pat.sub(lambda m: self._to_code[m.group(0)], s)
        return s

    # 语义别名：调用处一眼看出遮挡的是哪类内容（遮挡规则相同）
    m = role = rel = quote = t


# ───────── 解锁 ─────────
class Locked(Exception):
    pass


class TooManyAttempts(Exception):
    pass


class AppLock:
    """应用重新打开需要解锁；空闲超过 idle_seconds 自动上锁；连续输错 max_attempts 次后冷却 cooldown 秒。"""

    def __init__(self, path: str, *, idle_seconds: float = 300, max_attempts: int = 5, cooldown: float = 30,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.path, self.idle, self.max_attempts, self.cooldown, self.clock = path, idle_seconds, max_attempts, cooldown, clock
        self._store: Optional[Store] = None
        self._last = 0.0
        self._fails = 0
        self._blocked_until = 0.0

    def unlock(self, passphrase: str) -> Store:
        now = self.clock()
        if now < self._blocked_until:
            raise TooManyAttempts(f"请 {int(self._blocked_until - now) + 1} 秒后再试")
        try:
            self._store = Store(self.path, passphrase)
        except WrongPassphrase:
            self._fails += 1
            if self._fails >= self.max_attempts:
                self._blocked_until, self._fails = now + self.cooldown, 0
            raise
        self._fails, self._last = 0, now
        return self._store

    @property
    def locked(self) -> bool:
        if self._store is not None and self.clock() - self._last > self.idle:
            self.lock()
        return self._store is None

    @property
    def store(self) -> Store:
        if self.locked:
            raise Locked("应用已上锁，请先解锁")
        self._last = self.clock()  # 使用即视为活跃
        return self._store

    def lock(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store.key = b""  # 不再持有密钥
            self._store = None
