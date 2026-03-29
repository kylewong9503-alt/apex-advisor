from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.database import get_db
from app.models.user import User
from app.schemas.behavior_profiler import (
    BehaviorProfilerChatBody,
    BehaviorProfilerChatResponse,
    BehaviorProfilerFinalizeBody,
    BehaviorProfilerFinalizeResponse,
    BehaviorProfilerStartBody,
    BehaviorProfilerStartResponse,
    SuggestedBehaviorTagSchema,
)
from app.services.ai.behavior_profiler import BehaviorProfiler

router = APIRouter()
_profiler = BehaviorProfiler()


@router.post(
    "/start",
    response_model=BehaviorProfilerStartResponse,
    status_code=status.HTTP_201_CREATED,
)
def behavior_profiler_start(
    body: BehaviorProfilerStartBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        session_id, greeting = _profiler.start_session(
            db,
            user_id=current_user.id,
            member_id=body.member_id,
        )
    except ValueError as e:
        if str(e) == "Team member not found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    return BehaviorProfilerStartResponse(session_id=session_id, greeting=greeting)


@router.post("/chat", response_model=BehaviorProfilerChatResponse)
async def behavior_profiler_chat(
    body: BehaviorProfilerChatBody,
    current_user: User = Depends(get_current_active_user),
):
    try:
        reply, suggested = await _profiler.chat(
            user_id=current_user.id,
            session_id=body.session_id,
            user_message=body.user_message,
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profiling session not found or expired",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    return BehaviorProfilerChatResponse(
        reply=reply,
        suggested_tags=[
            SuggestedBehaviorTagSchema(
                label=t["label"],
                category=t["category"],
                confidence=t["confidence"],
                source=t.get("source", "ai"),
            )
            for t in suggested
        ],
    )


@router.post("/finalize", response_model=BehaviorProfilerFinalizeResponse)
async def behavior_profiler_finalize(
    body: BehaviorProfilerFinalizeBody,
    current_user: User = Depends(get_current_active_user),
):
    try:
        tags, mode = await _profiler.finalize(
            user_id=current_user.id,
            session_id=body.session_id,
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profiling session not found or expired",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    return BehaviorProfilerFinalizeResponse(
        tags=[
            SuggestedBehaviorTagSchema(
                label=t["label"],
                category=t["category"],
                confidence=t["confidence"],
                source=t.get("source", "ai"),
            )
            for t in tags
        ],
        update_mode=mode,
    )
