from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.work import FollowUpItem, FollowUpStatus, MeetingNote, WorkProfile, Workstream
from app.services.ai.base import BaseLLMService, get_llm_service

EXTRACTOR_SYSTEM = (
    "[followup_extractor]\n"
    "Extract concrete follow-up action items from a meeting note. Each item: description, "
    "optional owner, optional due_date (ISO8601 or null), optional suggested_workstream_name.\n"
    "Respond with JSON only:\n"
    '{"items": [\n'
    "  {\n"
    '    "description": "<action>",\n'
    '    "owner": "<name or null>",\n'
    '    "due_date": "<ISO8601 or null>",\n'
    '    "suggested_workstream_name": "<short label or null>"\n'
    "  }\n"
    "]}\n"
)


def _parse_json_object(text: str) -> dict:
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("No JSON object in model output")
    return json.loads(text[start : end + 1])


def _parse_dt(val: Any) -> Optional[datetime]:
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val
    s = str(val).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _match_workstream(
    db: Session, work_profile_id: uuid.UUID, suggested_name: Optional[str]
) -> Optional[uuid.UUID]:
    if not suggested_name or not suggested_name.strip():
        return None
    name_lower = suggested_name.strip().lower()
    rows = (
        db.query(Workstream)
        .filter(Workstream.work_profile_id == work_profile_id)
        .all()
    )
    for ws in rows:
        if name_lower in (ws.name or "").lower() or (ws.name or "").lower() in name_lower:
            return ws.id
    return None


class FollowUpExtractor:
    def __init__(self, llm: Optional[BaseLLMService] = None):
        self._llm = llm

    def _client(self) -> BaseLLMService:
        return self._llm if self._llm is not None else get_llm_service()

    async def _complete(self, user_prompt: str) -> str:
        return await self._client().generate_response(user_prompt, system_message=EXTRACTOR_SYSTEM)

    def _get_note(
        self, db: Session, user_id: uuid.UUID, meeting_note_id: uuid.UUID
    ) -> Tuple[MeetingNote, WorkProfile]:
        note = (
            db.query(MeetingNote)
            .join(WorkProfile)
            .filter(
                MeetingNote.id == meeting_note_id,
                WorkProfile.user_id == user_id,
            )
            .first()
        )
        if not note:
            raise ValueError("Meeting note not found")
        return note, note.work_profile

    async def extract_drafts(
        self, db: Session, *, user_id: uuid.UUID, meeting_note_id: uuid.UUID
    ) -> List[Dict[str, Any]]:
        note, wp = self._get_note(db, user_id, meeting_note_id)
        user_prompt = (
            f"TITLE: {note.title}\nDATE: {note.meeting_date.isoformat()}\n"
            f"PARTICIPANTS: {note.participants or '—'}\n\nCONTENT:\n{note.content}"
        )
        raw = await self._complete(user_prompt)
        data = _parse_json_object(raw)
        items = data.get("items") or []
        if not isinstance(items, list):
            items = []

        out: List[Dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            desc = str(it.get("description") or "").strip()
            if not desc:
                continue
            sug = it.get("suggested_workstream_name")
            sug_s = str(sug).strip() if sug else None
            ws_id = _match_workstream(db, wp.id, sug_s)
            own = it.get("owner")
            own_s = str(own).strip() if own not in (None, "") else None
            out.append(
                {
                    "description": desc,
                    "owner": own_s,
                    "due_date": _parse_dt(it.get("due_date")),
                    "suggested_workstream_name": sug_s,
                    "suggested_workstream_id": ws_id,
                }
            )
        return out

    def persist_confirmed(
        self,
        db: Session,
        *,
        user_id: uuid.UUID,
        meeting_note_id: uuid.UUID,
        items: List[Dict[str, Any]],
    ) -> List[FollowUpItem]:
        note, wp = self._get_note(db, user_id, meeting_note_id)
        created: List[FollowUpItem] = []
        for it in items:
            desc = str(it.get("description") or "").strip()
            if not desc:
                continue
            ws_id = it.get("suggested_workstream_id") or it.get("workstream_id")
            if ws_id is not None and not isinstance(ws_id, uuid.UUID):
                try:
                    ws_id = uuid.UUID(str(ws_id))
                except ValueError:
                    ws_id = None
            if ws_id:
                ws = (
                    db.query(Workstream)
                    .filter(
                        Workstream.id == ws_id,
                        Workstream.work_profile_id == wp.id,
                    )
                    .first()
                )
                if not ws:
                    ws_id = None
            ow = it.get("owner")
            ow_s = str(ow).strip() if ow not in (None, "") else None
            row = FollowUpItem(
                work_profile_id=wp.id,
                meeting_note_id=note.id,
                workstream_id=ws_id,
                description=desc,
                owner=ow_s,
                due_date=_parse_dt(it.get("due_date")),
                status=FollowUpStatus.PENDING,
            )
            db.add(row)
            created.append(row)
        db.commit()
        for r in created:
            db.refresh(r)
        return created
