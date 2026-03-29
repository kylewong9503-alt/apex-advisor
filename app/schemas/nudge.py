from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class NudgeItem(BaseModel):
    """Single banner or card nudge."""

    id: str = Field(..., description="Stable key for dismissal")
    kind: Literal["banner", "card"]
    severity: Literal["info", "warning", "tip"]
    title: str
    body: str
    action_label: Optional[str] = None
    action_href: Optional[str] = None


class NudgesResponse(BaseModel):
    banners: List[NudgeItem]
    cards: List[NudgeItem]


class NudgeDismissBody(BaseModel):
    nudge_key: str = Field(..., min_length=1, max_length=160)
