from fastapi import APIRouter

from app.api.v1 import auth, behavior_profiler, chat, company, industry, nudges, prompts, sync, system, team, work

api_router = APIRouter()

api_router.include_router(system.router, prefix="/system", tags=["system"])
api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(team.router, prefix="/team", tags=["team"])
api_router.include_router(company.router, prefix="/company", tags=["company"])
api_router.include_router(work.router, prefix="/work", tags=["work"])
api_router.include_router(industry.router, prefix="/industry", tags=["industry"])
api_router.include_router(prompts.router, prefix="/prompts", tags=["prompts"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(nudges.router, prefix="/nudges", tags=["nudges"])
api_router.include_router(sync.router, prefix="/sync", tags=["sync"])
api_router.include_router(
    behavior_profiler.router,
    prefix="/ai/behavior-profiler",
    tags=["ai", "behavior-profiler"],
)
