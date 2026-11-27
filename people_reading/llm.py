"""本地模型适配器。MVP 只接本地模型（默认 Ollama 的 OpenAI 兼容接口 + DeepSeek 系列）。

硬规则：真实人物信息不得发送到外部 LLM。base_url 的主机必须是回环地址（localhost / 127.x / ::1），
没有“允许外部”的开关。程序对模型输出一律做 schema 校验；失败重试一次后退回手动表单，不写入半成品。
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import urllib.error
import urllib.request
from typing import Callable, Optional
from urllib.parse import urlparse

DEFAULT_BASE_URL = os.environ.get("PR_MODEL_BASE_URL", "http://127.0.0.1:11434/v1")
DEFAULT_MODEL = os.environ.get("PR_MODEL", "deepseek-r1:14b")


class ExternalModelRefused(Exception):
    """试图把数据发往非本机地址。"""


class ModelOutputInvalid(Exception):
    """重试后输出仍不合 schema；调用方应退回纯文本 + 手动表单。"""


def assert_local(base_url: str) -> None:
    host = urlparse(base_url).hostname or ""
    if host == "localhost":
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise ExternalModelRefused(f"拒绝连接 {host!r}：真实人物信息只能发给本机模型")


def clean_output(text: str) -> str:
    """去掉 DeepSeek-R1 的 <think>…</think>，取第一个完整 JSON 对象。"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start = text.find("{")
    if start < 0:
        raise ValueError("没有 JSON")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            esc = (not esc and c == "\\")
            if c == '"' and not esc:
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise ValueError("JSON 不完整")


class LocalModel:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL, timeout: float = 120.0) -> None:
        assert_local(base_url)
        self.base_url, self.model, self.timeout = base_url.rstrip("/"), model, timeout
        self.calls = 0

    def chat(self, system: str, user: str) -> str:
        body = json.dumps({"model": self.model, "temperature": 0, "stream": False,
                           "response_format": {"type": "json_object"},
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
        req = urllib.request.Request(self.base_url + "/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        self.calls += 1
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 本机回环，绝不经代理
        with opener.open(req, timeout=self.timeout) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]

    def structured(self, system: str, user: str, validate: Callable[[dict], list], retries: int = 1) -> dict:
        """调用并校验；校验失败带着错误重试 retries 次，仍失败抛 ModelOutputInvalid。"""
        last: list = []
        for attempt in range(retries + 1):
            prompt = user if not last else user + "\n\n上次输出有问题：" + "；".join(last) + "。请只输出符合要求的 JSON。"
            try:
                data = json.loads(clean_output(self.chat(system, prompt)))
            except (ValueError, KeyError) as e:
                last = [f"不是合法 JSON（{e}）"]
                continue
            errs = validate(data)
            if not errs:
                return data
            last = errs
        raise ModelOutputInvalid("；".join(last))


class MissingApiKey(Exception):
    pass


class RemoteModelError(OSError):
    """远程调用失败。信息里只有状态码，不含密钥、请求体或响应体。继承 OSError，录入流程会自动退回手动表单。"""


class RemoteModel:
    """DeepSeek 官方 API（OpenAI 兼容）。⚠ 会把用户原话发到外部服务，与 v4.6 “只接本地模型”的隐私规则冲突。

    因此必须同时满足：环境变量 PR_ALLOW_EXTERNAL=1（明确知情并开启）、DEEPSEEK_API_KEY 已设置、
    主机在白名单内且为 https。密钥只来自环境变量，不落盘、不进日志、不进异常信息。
    不做“替换姓名”的伪脱敏：规格已说明仅替换姓名不足以去标识。
    """

    DEFAULT_URL = "https://api.deepseek.com"
    ALLOWED_HOSTS = ("api.deepseek.com",)  # 类常量：调用方不能通过构造参数放宽（测试可以子类覆盖）
    REQUIRE_HTTPS = True

    def __init__(self, model: str = "deepseek-chat", base_url: str = DEFAULT_URL, timeout: float = 120.0) -> None:
        # 开关与密钥只来自进程环境变量，不接受构造参数，避免调用方绕过用户的明确开启
        if os.environ.get("PR_ALLOW_EXTERNAL") != "1":
            raise ExternalModelRefused("外部模型默认关闭。确认接受“原话会发送到外部服务”后，设置 PR_ALLOW_EXTERNAL=1")
        u = urlparse(base_url)
        if u.hostname not in self.ALLOWED_HOSTS or (self.REQUIRE_HTTPS and u.scheme != "https"):
            raise ExternalModelRefused(f"主机 {u.hostname!r} 不在白名单，或不是 https")
        self._key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not self._key:
            raise MissingApiKey("未设置 DEEPSEEK_API_KEY")
        if not (self._key.isascii() and self._key.isprintable() and " " not in self._key):
            raise MissingApiKey("DEEPSEEK_API_KEY 含有非法字符（检查是否粘贴了多余内容）")  # 不回显密钥
        self.model, self.base_url, self.timeout, self.calls = model, base_url.rstrip("/"), timeout, 0

    def __repr__(self) -> str:  # 避免密钥经 repr 泄漏
        return f"RemoteModel(model={self.model!r}, base_url={self.base_url!r})"

    def chat(self, system: str, user: str) -> str:
        payload = {"model": self.model, "temperature": 0, "stream": False,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user + "\n\n请只输出 json。"}]}  # DeepSeek 要求提示里含 json
        if self.model != "deepseek-reasoner":  # reasoner 不支持 response_format
            payload["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(self.base_url + "/chat/completions", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._key}"})
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            raise RemoteModelError(f"远程模型返回 HTTP {e.code}") from None
        except urllib.error.URLError as e:
            raise RemoteModelError(f"无法连接远程模型（{type(e.reason).__name__}）") from None

    structured = LocalModel.structured


def make_model():
    """按环境变量 PR_PROVIDER 选择：local（默认）| deepseek。"""
    provider = os.environ.get("PR_PROVIDER", "local")
    if provider == "local":
        return LocalModel(os.environ.get("PR_MODEL_BASE_URL", DEFAULT_BASE_URL), os.environ.get("PR_MODEL", DEFAULT_MODEL))
    if provider == "deepseek":
        return RemoteModel(os.environ.get("PR_MODEL", "deepseek-chat"))
    raise ValueError("PR_PROVIDER 只能是 local 或 deepseek")


class ScriptedModel:
    """测试替身：按顺序返回预置的 JSON（或原始文本），与 LocalModel 同接口。"""

    def __init__(self, outputs: list) -> None:
        self.outputs, self.calls, self.prompts = list(outputs), 0, []

    def chat(self, system: str, user: str) -> str:
        self.calls += 1
        self.prompts.append(user)
        o = self.outputs.pop(0)
        return o if isinstance(o, str) else json.dumps(o, ensure_ascii=False)

    structured = LocalModel.structured
