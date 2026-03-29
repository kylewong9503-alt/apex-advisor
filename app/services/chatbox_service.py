from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.services.ai.base import BaseLLMService, get_llm_service
from app.services.ai.prompt_engine import PROMPT_SCENES, PromptEngine
from app.services.profile_service import ProfileService
from app.services.slash_commands import build_help_text, parse_slash_command

CHATBOX_SYSTEM = (
    "[chatbox]\n"
    "You are Apex Advisor Chatbox: a workplace copilot. Use the profile context and "
    "the user's message. If SLASH_MODE is set, follow that mode (behavior prediction, "
    "draft review, or industry synthesis). Be concise in the main reply; put reasoning "
    "steps in chain_of_thought.\n"
    "Respond with JSON only:\n"
    "{\n"
    '  "reply": "<main user-facing answer>",\n'
    '  "chain_of_thought": "<step-by-step reasoning for the side panel>",\n'
    '  "sources_used": [{"layer": "team"|"company"|"work"|"industry", "summary": "<what was used>"}]\n'
    "}\n"
)

VALID_TAGS = frozenset({"team", "company", "work", "industry"})


def _parse_json_object(text: str) -> dict:
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("No JSON object in model output")
    return json.loads(text[start : end + 1])


def profile_completeness(profiles: Dict[str, Any]) -> Dict[str, bool]:
    team = (profiles.get("team_profile") or {}).get("members") or []
    team_ok = len(team) > 0

    cprof = profiles.get("company_profile") or {}
    cintel = cprof.get("intelligence") or {}
    company_ok = any(bool(v) for v in cintel.values())

    wprof = profiles.get("work_profile") or {}
    work_ok = bool(
        (wprof.get("deliverable_standards") or [])
        or (wprof.get("projects") or [])
        or (wprof.get("meeting_notes") or [])
    )

    iprof = profiles.get("industry_profile") or {}
    iintel = iprof.get("intelligence") or {}
    industry_ok = any(bool(v) for v in iintel.values())

    return {
        "team": team_ok,
        "company": company_ok,
        "work": work_ok,
        "industry": industry_ok,
    }


def _normalize_tags(tags: Optional[List[str]]) -> List[str]:
    if not tags:
        return ["team", "company", "work", "industry"]
    out = [t.lower().strip() for t in tags if t and t.lower().strip() in VALID_TAGS]
    return out or ["team", "company", "work", "industry"]


def _summarize_team(profiles: Dict[str, Any], max_chars: int = 4000) -> str:
    members = (profiles.get("team_profile") or {}).get("members") or []
    if not members:
        return "[团队：暂无成员]"
    lines = []
    for m in members[:20]:
        tags = ", ".join(
            f"{t.get('label', '')}" for t in (m.get("behavior_tags") or [])[:8]
        )
        taboo = (m.get("taboo") or "").strip()
        lines.append(
            f"- {m.get('name')} ({m.get('role')}): 沟通偏好={m.get('communication_preference') or '—'}; "
            f"标签: {tags or '—'}; 禁忌: {taboo or '—'}"
        )
    blob = "\n".join(lines)
    return blob[:max_chars]


def _summarize_company(profiles: Dict[str, Any], max_chars: int = 4000) -> str:
    intel = (profiles.get("company_profile") or {}).get("intelligence") or {}
    parts: List[str] = []
    for dim, items in intel.items():
        if not items:
            continue
        blob = "; ".join((x.get("content") or "")[:240] for x in items[:4])
        parts.append(f"- {dim}: {blob}")
    if not parts:
        return "[公司：暂无情报条目]"
    return "\n".join(parts)[:max_chars]


def _summarize_work(profiles: Dict[str, Any], max_chars: int = 4000) -> str:
    wp = profiles.get("work_profile") or {}
    stds = wp.get("deliverable_standards") or []
    if not stds:
        return "[工作：暂无交付物标准]"
    lines = []
    for s in stds[:8]:
        lines.append(
            f"- {s.get('work_scene')}: 格式={s.get('format_requirements') or '—'}; "
            f"体例={s.get('style_requirements') or '—'}"
        )
    return "\n".join(lines)[:max_chars]


def _summarize_industry(profiles: Dict[str, Any], max_chars: int = 4000) -> str:
    intel = (profiles.get("industry_profile") or {}).get("intelligence") or {}
    parts: List[str] = []
    for dim, items in intel.items():
        if not items:
            continue
        blob = "; ".join((x.get("content") or "")[:240] for x in items[:4])
        parts.append(f"- {dim}: {blob}")
    if not parts:
        return "[行业：暂无情报条目]"
    return "\n".join(parts)[:max_chars]


def build_profile_context(profiles: Dict[str, Any], tags: List[str]) -> str:
    """Build a text block for selected layers (Requirement 9.1–9.4)."""
    tags = _normalize_tags(tags)
    chunks: List[str] = []
    if "team" in tags:
        chunks.append("【团队 Team】\n" + _summarize_team(profiles))
    if "company" in tags:
        chunks.append("【公司 Company】\n" + _summarize_company(profiles))
    if "work" in tags:
        chunks.append("【工作 Work】\n" + _summarize_work(profiles))
    if "industry" in tags:
        chunks.append("【行业 Industry】\n" + _summarize_industry(profiles))
    return "\n\n".join(chunks)


def compute_source_attributions(
    profiles: Dict[str, Any], tags: List[str]
) -> List[Dict[str, str]]:
    """Structured source list for API (Requirement 9.9)."""
    tags = _normalize_tags(tags)
    out: List[Dict[str, str]] = []
    if "team" in tags:
        n = len((profiles.get("team_profile") or {}).get("members") or [])
        out.append(
            {
                "layer": "team",
                "summary": f"团队成员 {n} 人" if n else "团队层未填充",
            }
        )
    if "company" in tags:
        c = profiles.get("company_profile") or {}
        intel = c.get("intelligence") or {}
        n = sum(len(v or []) for v in intel.values())
        out.append(
            {
                "layer": "company",
                "summary": f"公司情报条目 {n} 条" if n else "公司层未填充",
            }
        )
    if "work" in tags:
        w = profiles.get("work_profile") or {}
        n = len(w.get("deliverable_standards") or [])
        out.append(
            {
                "layer": "work",
                "summary": f"交付物标准 {n} 条" if n else "工作层未填充",
            }
        )
    if "industry" in tags:
        ip = profiles.get("industry_profile") or {}
        intel = ip.get("intelligence") or {}
        n = sum(len(v or []) for v in intel.values())
        out.append(
            {
                "layer": "industry",
                "summary": f"行业情报条目 {n} 条" if n else "行业层未填充",
            }
        )
    return out


class ChatboxService:
    def __init__(self, llm: Optional[BaseLLMService] = None):
        self._llm = llm
        self._prompt_engine = PromptEngine(llm=llm)

    def _client(self) -> BaseLLMService:
        return self._llm if self._llm is not None else get_llm_service()

    async def _complete_chat(self, user_prompt: str) -> Tuple[str, str, List[Dict[str, str]]]:
        raw = await self._client().generate_response(user_prompt, system_message=CHATBOX_SYSTEM)
        data = _parse_json_object(raw)
        reply = str(data.get("reply") or "").strip() or "（无回复）"
        cot = str(data.get("chain_of_thought") or "").strip()
        src = data.get("sources_used") or []
        sources: List[Dict[str, str]] = []
        if isinstance(src, list):
            for x in src:
                if isinstance(x, dict) and x.get("layer") and x.get("summary"):
                    sources.append(
                        {"layer": str(x["layer"]), "summary": str(x["summary"])}
                    )
        if not sources:
            sources = []
        return reply, cot, sources

    async def run_turn(
        self,
        db: Session,
        *,
        user_id: uuid.UUID,
        user_message: str,
        context_tags: Optional[List[str]],
    ) -> Tuple[str, str, List[Dict[str, str]], Optional[str]]:
        """
        Returns (reply, chain_of_thought, sources, prompt_scene_if_any).
        prompt_scene is set when /prompt delegates to PromptEngine (caller may merge).
        """
        ps = ProfileService(db)
        profiles = ps.get_user_profiles(user_id)
        tags = _normalize_tags(context_tags)
        ctx = build_profile_context(profiles, tags)
        base_sources = compute_source_attributions(profiles, tags)

        slash = parse_slash_command(user_message)
        if slash and slash[0] == "help":
            return (
                build_help_text(),
                "（命令帮助为静态文本，无需模型推理）",
                base_sources,
                None,
            )

        if slash and slash[0] == "prompt":
            rest = slash[1].strip()
            scene = rest.split()[0] if rest else "communication_report"
            if scene not in PROMPT_SCENES:
                scene = "communication_report"
            template, hints = await self._prompt_engine.generate(
                db, user_id=user_id, scene=scene
            )
            reply = f"已生成场景「{PROMPT_SCENES[scene]}」提示模板：\n\n{template}"
            cot = "\n".join(f"- {h}" for h in hints) if hints else "（无占位提示）"
            return reply, cot, base_sources, scene

        mode = "chat"
        if slash:
            cmd, rest = slash
            if cmd == "behavior":
                mode = "behavior"
                user_message = rest or "请基于团队档案给出沟通与协作预测。"
            elif cmd == "review":
                mode = "review"
                user_message = rest or "（请粘贴需要评审的草稿）"
            elif cmd == "industry":
                mode = "industry"
                user_message = rest or "请汇总行业情报要点。"
            else:
                user_message = user_message.strip()

        bundle = (
            f"SLASH_MODE: {mode}\n"
            f"CONTEXT_TAGS: {', '.join(tags)}\n\n"
            f"PROFILE_CONTEXT:\n{ctx}\n\n"
            f"USER_MESSAGE:\n{user_message.strip()}"
        )
        reply, cot, sources = await self._complete_chat(bundle)
        if not sources:
            sources = base_sources
        return reply, cot, sources, None

