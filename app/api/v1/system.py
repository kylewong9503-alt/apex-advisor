"""
System metadata: AI ↔ ProfileService integration map (Task 13.1 documentation surface).
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter

from app.core.config import settings

router = APIRouter()


@router.get("/capabilities", response_model=dict[str, Any])
def integration_capabilities() -> Dict[str, Any]:
    """
    Describes how AI modules consume the four-layer profile via `ProfileService`
    and where they are exposed on the API.
    """
    layers = ["team", "company", "work", "industry"]
    modules: List[Dict[str, Any]] = [
        {
            "name": "Behavior_Profiler",
            "profile_layers": ["team"],
            "api_prefix": "/api/v1/ai/behavior-profiler",
        },
        {
            "name": "Intelligence_Classifier",
            "profile_layers": ["company"],
            "api_prefix": "/api/v1/company",
        },
        {
            "name": "Deliverable_Review",
            "profile_layers": ["work"],
            "api_prefix": "/api/v1/work",
        },
        {
            "name": "FollowUp_Extractor",
            "profile_layers": ["work"],
            "api_prefix": "/api/v1/work",
        },
        {
            "name": "Industry_Classifier",
            "profile_layers": ["industry"],
            "api_prefix": "/api/v1/industry",
        },
        {
            "name": "Prompt_Engine",
            "profile_layers": layers,
            "api_prefix": "/api/v1/prompts",
        },
        {
            "name": "Chatbox",
            "profile_layers": layers,
            "api_prefix": "/api/v1/chat",
        },
    ]
    return {
        "profile_service": {
            "description": "Central access to four-layer profiles (Team / Company / Work / Industry).",
            "layers": layers,
        },
        "ai_modules": modules,
        "documentation": {
            "openapi_json": f"{settings.API_V1_STR}/openapi.json"
            if settings.DEBUG
            else None,
            "note": "All authenticated AI routes resolve data through SQLAlchemy models scoped by user_id.",
        },
    }
