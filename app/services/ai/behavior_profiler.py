from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.team import BehaviorTag, TagSource, TeamMember
from app.services.ai.base import BaseLLMService, get_llm_service

SESSION_TTL_HOURS = 24

_session_lock = threading.Lock()
_sessions: Dict[uuid.UUID, "ProfilerSession"] = {}


@dataclass
class ProfilerSession:
    session_id: uuid.UUID
    user_id: uuid.UUID
    member_id: uuid.UUID
    member_name: str
    member_role: str
    messages: List[Dict[str, str]] = field(default_factory=list)
    existing_tags_context: List[Dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def transcript(self) -> str:
        lines = []
        for m in self.messages:
            role = m["role"]
            content = m["content"]
            lines.append(f"{role.upper()}: {content}")
        return "\n".join(lines)


def _purge_stale_sessions() -> None:
    cutoff = datetime.now(timezone.utc).timestamp() - SESSION_TTL_HOURS * 3600
    stale = [
        sid
        for sid, s in _sessions.items()
        if s.created_at.timestamp() < cutoff
    ]
    for sid in stale:
        _sessions.pop(sid, None)


def _get_session(session_id: uuid.UUID, user_id: uuid.UUID) -> ProfilerSession:
    _purge_stale_sessions()
    sess = _sessions.get(session_id)
    if not sess or sess.user_id != user_id:
        raise KeyError("session not found")
    return sess


def _parse_json_object(text: str) -> dict:
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("No JSON object in model output")
    return json.loads(text[start : end + 1])


def _clamp_confidence(v: Any) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, x))


class BehaviorProfiler:
    """Multi-turn behavior profiling with LLM; sessions are in-memory per process."""

    CHAT_SYSTEM = (
        "[profiler:chat]\n"
        "You are Behavior_Profiler, a workplace coach helping the user describe "
        "a colleague's typical behaviors. Be concise and empathetic. Ask short follow-ups "
        "when useful.\n"
        "Respond with a single JSON object only, no markdown:\n"
        '{"reply": "<your message to the user>", "suggested_tags": [\n'
        '  {"label": "<short trait>", "category": "<e.g. communication|decision|risk>", '
        '"confidence": <0.0-1.0>}\n'
        "]}\n"
        "suggested_tags may be empty if you do not have enough evidence yet."
    )

    FINALIZE_SYSTEM = (
        "[profiler:finalize]\n"
        "From the conversation transcript, extract structured behavior trait tags "
        "for the colleague. Each tag needs label, category, confidence (0-1).\n"
        "Also choose update_mode: use \"increment\" if new tags should add to existing ones, "
        "or \"overwrite\" if the conversation fully redefines the profile.\n"
        "Respond with JSON only:\n"
        '{"tags": [...], "update_mode": "increment"|"overwrite"}'
    )

    def __init__(self, llm: Optional[BaseLLMService] = None):
        self._llm = llm

    def _llm_client(self) -> BaseLLMService:
        if self._llm is not None:
            return self._llm
        return get_llm_service()

    async def _complete(self, system: str, user: str) -> str:
        client = self._llm_client()
        return await client.generate_response(user, system_message=system)

    def start_session(
        self,
        db: Session,
        *,
        user_id: uuid.UUID,
        member_id: uuid.UUID,
    ) -> Tuple[uuid.UUID, str]:
        member = (
            db.query(TeamMember)
            .filter(TeamMember.id == member_id, TeamMember.user_id == user_id)
            .first()
        )
        if not member:
            raise ValueError("Team member not found")

        existing = [
            {
                "label": t.label,
                "category": t.category,
                "confidence": t.confidence,
                "source": t.source.value if hasattr(t.source, "value") else str(t.source),
            }
            for t in member.behavior_tags
        ]

        sid = uuid.uuid4()
        sess = ProfilerSession(
            session_id=sid,
            user_id=user_id,
            member_id=member_id,
            member_name=member.name,
            member_role=member.role,
            existing_tags_context=existing,
        )
        with _session_lock:
            _purge_stale_sessions()
            _sessions[sid] = sess

        if existing:
            greeting = (
                f"我们接着完善 **{member.name}**（{member.role}）的行为画像。"
                f"当前已有 {len(existing)} 个标签；你可以补充新观察，或纠正之前的理解。"
            )
        else:
            greeting = (
                f"你好。接下来请用自然语言描述 **{member.name}**（{member.role}）"
                "在协作、沟通或决策上的典型表现，我会帮你整理成结构化标签。"
            )
        return sid, greeting

    async def chat(
        self,
        *,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        user_message: str,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        sess = _get_session(session_id, user_id)
        text = user_message.strip()
        if not text:
            raise ValueError("userMessage must not be empty")

        sess.messages.append({"role": "user", "content": text})

        ctx = (
            f"Colleague: {sess.member_name}, role: {sess.member_role}.\n"
            f"Existing tags (context): {json.dumps(sess.existing_tags_context, ensure_ascii=False)}\n"
            f"Conversation so far:\n{sess.transcript()}"
        )

        raw = await self._complete(self.CHAT_SYSTEM, ctx)
        try:
            data = _parse_json_object(raw)
        except (json.JSONDecodeError, ValueError):
            reply = raw.strip() or "收到，请继续描述。"
            data = {"reply": reply, "suggested_tags": []}

        reply = str(data.get("reply", "")).strip() or "请再具体一点说说典型场景。"
        suggested: List[Dict[str, Any]] = []
        for item in data.get("suggested_tags") or []:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label", "")).strip()
            category = str(item.get("category", "")).strip() or "general"
            if not label:
                continue
            suggested.append(
                {
                    "label": label,
                    "category": category,
                    "confidence": _clamp_confidence(item.get("confidence")),
                    "source": "ai",
                }
            )

        sess.messages.append({"role": "assistant", "content": reply})
        return reply, suggested

    async def finalize(
        self, *, user_id: uuid.UUID, session_id: uuid.UUID
    ) -> Tuple[List[Dict[str, Any]], Literal["increment", "overwrite"]]:
        sess = _get_session(session_id, user_id)
        if not sess.messages:
            raise ValueError("No messages in session; send at least one user message first")

        prompt = (
            f"Colleague: {sess.member_name} ({sess.member_role}).\n"
            f"Prior tags: {json.dumps(sess.existing_tags_context, ensure_ascii=False)}\n\n"
            f"Transcript:\n{sess.transcript()}"
        )
        raw = await self._complete(self.FINALIZE_SYSTEM, prompt)
        try:
            data = _parse_json_object(raw)
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError("Model returned invalid JSON for finalize") from e

        mode = str(data.get("update_mode", "increment")).lower()
        if mode not in ("increment", "overwrite"):
            mode = "increment"

        tags_out: List[Dict[str, Any]] = []
        for item in data.get("tags") or []:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label", "")).strip()
            category = str(item.get("category", "")).strip() or "general"
            if not label:
                continue
            tags_out.append(
                {
                    "label": label,
                    "category": category,
                    "confidence": _clamp_confidence(item.get("confidence")),
                    "source": "ai",
                }
            )

        with _session_lock:
            _sessions.pop(session_id, None)

        return tags_out, mode  # type: ignore[return-value]


def persist_behavior_tags(
    db: Session,
    *,
    user_id: uuid.UUID,
    member_id: uuid.UUID,
    tags: List[Dict[str, Any]],
    update_mode: Literal["increment", "overwrite"],
) -> List[BehaviorTag]:
    member = (
        db.query(TeamMember)
        .filter(TeamMember.id == member_id, TeamMember.user_id == user_id)
        .first()
    )
    if not member:
        raise ValueError("Team member not found")

    if update_mode == "overwrite":
        db.query(BehaviorTag).filter(BehaviorTag.team_member_id == member_id).delete(
            synchronize_session=False
        )

    created: List[BehaviorTag] = []
    for item in tags:
        label = str(item.get("label", "")).strip()
        category = str(item.get("category", "")).strip() or "general"
        if not label:
            continue
        conf = _clamp_confidence(item.get("confidence"))
        src_raw = item.get("source", "ai")
        if isinstance(src_raw, TagSource):
            src = src_raw
        else:
            s = str(src_raw).lower()
            src = TagSource.MANUAL if s == "manual" else TagSource.AI

        if update_mode == "increment":
            exists = (
                db.query(BehaviorTag)
                .filter(
                    BehaviorTag.team_member_id == member_id,
                    BehaviorTag.label == label,
                )
                .first()
            )
            if exists:
                exists.category = category
                exists.confidence = conf
                exists.source = src
                created.append(exists)
                continue

        row = BehaviorTag(
            team_member_id=member_id,
            label=label,
            category=category,
            confidence=conf,
            source=src,
        )
        db.add(row)
        created.append(row)

    db.commit()
    for row in created:
        db.refresh(row)
    return created
