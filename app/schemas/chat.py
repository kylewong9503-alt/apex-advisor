from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class SourceAttribution(BaseModel):
    layer: str
    summary: str


class ProfileCompleteness(BaseModel):
    """Which layers have usable data (Requirement 9.9 onboarding)."""

    team: bool
    company: bool
    work: bool
    industry: bool


class ChatSessionCreate(BaseModel):
    title: Optional[str] = None


class ChatSessionResponse(BaseModel):
    id: UUID
    title: Optional[str]
    created_at: datetime
    updated_at: datetime
    profile_completeness: ProfileCompleteness


class ChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=32000)
    context_tags: List[str] = Field(
        default_factory=lambda: ["team", "company", "work", "industry"],
        description="Subset of: team, company, work, industry",
    )


class ChatMessageItem(BaseModel):
    id: UUID
    role: str
    content: str
    chain_of_thought: Optional[str] = None
    sources_used: Optional[List[SourceAttribution]] = None
    created_at: datetime


class ChatTurnResponse(BaseModel):
    """Result of POST /sessions/{id}/messages."""

    user_message: ChatMessageItem
    assistant_message: ChatMessageItem
    profile_completeness: ProfileCompleteness


class ChatHistoryResponse(BaseModel):
    session: ChatSessionResponse
    messages: List[ChatMessageItem]
    profile_completeness: ProfileCompleteness


class SlashCommandInfo(BaseModel):
    name: str
    description: str
    example: Optional[str] = None
    canonical: Optional[str] = None


class SlashCommandsResponse(BaseModel):
    commands: List[SlashCommandInfo]
    help_text: str = ""
