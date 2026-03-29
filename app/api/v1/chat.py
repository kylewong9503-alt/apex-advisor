from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.database import get_db
from app.models.chat import ChatMessage, ChatSession
from app.models.user import User
from app.schemas.chat import (
    ChatHistoryResponse,
    ChatMessageItem,
    ChatMessageRequest,
    ChatSessionCreate,
    ChatSessionResponse,
    ChatTurnResponse,
    ProfileCompleteness,
    SlashCommandInfo,
    SlashCommandsResponse,
    SourceAttribution,
)
from app.services.chatbox_service import ChatboxService, profile_completeness
from app.services.slash_commands import build_help_text, list_slash_commands_meta, meta_to_api_dict

router = APIRouter()
_service = ChatboxService()


def _pc(db: Session, user_id: uuid.UUID) -> ProfileCompleteness:
    from app.services.profile_service import ProfileService

    p = ProfileService(db).get_user_profiles(user_id)
    c = profile_completeness(p)
    return ProfileCompleteness(
        team=c["team"],
        company=c["company"],
        work=c["work"],
        industry=c["industry"],
    )


def _msg_item(row: ChatMessage) -> ChatMessageItem:
    src = row.sources_used
    sources = None
    if isinstance(src, list):
        sources = [
            SourceAttribution(layer=str(x.get("layer", "")), summary=str(x.get("summary", "")))
            for x in src
            if isinstance(x, dict) and x.get("layer") is not None
        ]
        if not sources:
            sources = None
    return ChatMessageItem(
        id=row.id,
        role=row.role,
        content=row.content,
        chain_of_thought=row.chain_of_thought,
        sources_used=sources,
        created_at=row.created_at,
    )


@router.post("/sessions", response_model=ChatSessionResponse, status_code=status.HTTP_201_CREATED)
def create_session(
    body: ChatSessionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    row = ChatSession(
        user_id=current_user.id,
        title=(body.title or "").strip() or None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    pc = _pc(db, current_user.id)
    return ChatSessionResponse(
        id=row.id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
        profile_completeness=pc,
    )


@router.get("/sessions/{session_id}", response_model=ChatHistoryResponse)
def get_session_history(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    row = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == current_user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    pc = _pc(db, current_user.id)
    return ChatHistoryResponse(
        session=ChatSessionResponse(
            id=row.id,
            title=row.title,
            created_at=row.created_at,
            updated_at=row.updated_at,
            profile_completeness=pc,
        ),
        messages=[_msg_item(m) for m in msgs],
        profile_completeness=pc,
    )


@router.post("/sessions/{session_id}/messages", response_model=ChatTurnResponse)
async def post_message(
    session_id: uuid.UUID,
    body: ChatMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    row = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == current_user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    um = ChatMessage(
        session_id=session_id,
        role="user",
        content=body.message.strip(),
        chain_of_thought=None,
        sources_used=None,
    )
    db.add(um)
    db.flush()

    reply_text: str
    cot: str
    sources: list
    try:
        reply_text, cot, sources, _scene = await _service.run_turn(
            db,
            user_id=current_user.id,
            user_message=body.message,
            context_tags=body.context_tags,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    am = ChatMessage(
        session_id=session_id,
        role="assistant",
        content=reply_text,
        chain_of_thought=cot or None,
        sources_used=sources or None,
    )
    db.add(am)
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(um)
    db.refresh(am)

    pc = _pc(db, current_user.id)
    return ChatTurnResponse(
        user_message=_msg_item(um),
        assistant_message=_msg_item(am),
        profile_completeness=pc,
    )


@router.get("/commands", response_model=SlashCommandsResponse)
def list_slash_commands(
    prefix: str | None = Query(
        None,
        description="Filter command names by prefix (e.g. /pr) for autocomplete.",
    ),
    current_user: User = Depends(get_current_active_user),
):
    """Slash command registry + optional prefix filter (Requirement 9.6)."""
    metas = list_slash_commands_meta(prefix)
    return SlashCommandsResponse(
        commands=[
            SlashCommandInfo(
                name=m["name"],
                description=m["description"],
                example=m.get("example"),
                canonical=m.get("canonical"),
            )
            for m in (meta_to_api_dict(x) for x in metas)
        ],
        help_text=build_help_text(),
    )
