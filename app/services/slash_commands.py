"""
Slash command parser and registry (Task 10.2, Requirements 9.5, 9.6).

Canonical commands: behavior (prediction), review (draft), prompt (generation),
industry (insights), help.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# Canonical command name -> primary handler key used by ChatboxService.run_turn
CANONICAL = frozenset({"help", "behavior", "review", "prompt", "industry"})

# User-typed name -> canonical (after lowercasing)
_ALIAS_TO_CANONICAL: Dict[str, str] = {
    "help": "help",
    "predict": "behavior",
    "behavior": "behavior",
    "draft": "review",
    "review": "review",
    "prompt": "prompt",
    "industry": "industry",
    "insights": "industry",
}

_SLASH = re.compile(r"^/([a-zA-Z][a-zA-Z0-9_-]*)(?:\s+(.*))?$", re.DOTALL)


@dataclass(frozen=True)
class SlashCommandMeta:
    """Metadata for help + autocomplete (9.6)."""

    name: str  # e.g. "/behavior"
    canonical: str
    description: str
    example: str


SLASH_COMMAND_METAS: Tuple[SlashCommandMeta, ...] = (
    SlashCommandMeta(
        name="/help",
        canonical="help",
        description="显示命令列表与说明",
        example="/help",
    ),
    SlashCommandMeta(
        name="/behavior",
        canonical="behavior",
        description="行为与沟通预测（团队档案）",
        example="/behavior",
    ),
    SlashCommandMeta(
        name="/predict",
        canonical="behavior",
        description="同 /behavior",
        example="/predict",
    ),
    SlashCommandMeta(
        name="/review",
        canonical="review",
        description="交付物草稿五维评审要点",
        example="/review <粘贴草稿>",
    ),
    SlashCommandMeta(
        name="/draft",
        canonical="review",
        description="同 /review",
        example="/draft <粘贴草稿>",
    ),
    SlashCommandMeta(
        name="/prompt",
        canonical="prompt",
        description="场景提示模板生成",
        example="/prompt research",
    ),
    SlashCommandMeta(
        name="/industry",
        canonical="industry",
        description="行业情报要点汇总",
        example="/industry",
    ),
    SlashCommandMeta(
        name="/insights",
        canonical="industry",
        description="同 /industry",
        example="/insights",
    ),
)


def build_help_text() -> str:
    """Static help body for /help and docs."""
    lines = [
        "可用命令（消息以 / 开头）：",
        "/help — 显示本帮助",
        "/behavior 或 /predict — 基于团队成员档案做行为与沟通预测",
        "/review 或 /draft — 交付物草稿五维评审要点（后可跟草稿正文）",
        "/prompt [scene] — 生成场景提示模板；scene：communication_report, meeting_presentation, "
        "materials_proposal, research, project_planning（默认 communication_report）",
        "/industry 或 /insights — 汇总行业情报要点",
        "",
        "在界面勾选 Context_Tag 可选择注入档案层：团队 / 公司 / 工作 / 行业。",
    ]
    return "\n".join(lines)


def normalize_command_token(raw: str) -> Optional[str]:
    """Map parsed slash token (no leading /) to canonical command, or None if unknown."""
    key = (raw or "").lower().strip()
    return _ALIAS_TO_CANONICAL.get(key)


def parse_slash_command(text: str) -> Optional[Tuple[str, str]]:
    """
    Return (canonical_command, rest) if the first line is a known /command, else None.
    Unknown /foo still returns None from this function — caller treats as plain chat
    unless we extend to pass through; ChatboxService passes unknown as normal chat.
    """
    raw = (text or "").strip()
    if not raw.startswith("/"):
        return None
    first_line = raw.split("\n", 1)[0].strip()
    m = _SLASH.match(first_line)
    if not m:
        return None
    token = m.group(1).lower()
    rest = (m.group(2) or "").strip()
    canon = normalize_command_token(token)
    if canon is None:
        return None
    return canon, rest


def list_slash_commands_meta(prefix: Optional[str] = None) -> List[SlashCommandMeta]:
    """All registered commands, optionally filtered by name prefix (autocomplete)."""
    if not prefix:
        return list(SLASH_COMMAND_METAS)
    p = prefix.strip().lower()
    if not p.startswith("/"):
        p = "/" + p.lstrip("/")
    out: List[SlashCommandMeta] = []
    for m in SLASH_COMMAND_METAS:
        if m.name.lower().startswith(p):
            out.append(m)
    return out


def meta_to_api_dict(m: SlashCommandMeta) -> Dict[str, Any]:
    return {
        "name": m.name,
        "canonical": m.canonical,
        "description": m.description,
        "example": m.example,
    }
