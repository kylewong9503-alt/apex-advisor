"""本地模型适配器。MVP 只接本地模型（默认 Ollama 的 OpenAI 兼容接口 + DeepSeek 系列）。

硬规则：真实人物信息不得发送到外部 LLM。base_url 的主机必须是回环地址（localhost / 127.x / ::1），
没有“允许外部”的开关。程序对模型输出一律做 schema 校验；失败重试一次后退回手动表单，不写入半成品。
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
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
