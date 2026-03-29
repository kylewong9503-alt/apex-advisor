"""
Property tests for Prompt Engine (Requirements 6.1, 6.7, 7.1, 7.2).

- 9.2 / Property 13: Generated context bundle includes four-layer profile structure
  and concrete team facts when present.
- 9.3 / Property 16: When a team member is selected, Taboo text appears in the bundle.
- 9.5 / Property 15: After a profile-affecting update, saved templates report refresh.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import get_password_hash
from app.db.database import Base
from app.models.prompt_template import SavedPromptTemplate
from app.models.team import TeamMember
from app.models.user import User
from app.services.ai.prompt_engine import (
    PROMPT_SCENES,
    build_context_bundle,
    template_needs_refresh,
)
from app.services.profile_service import ProfileService


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def _seed_user_with_profiles(db):
    u = User(
        email=f"{uuid.uuid4().hex}@example.com",
        password_hash=get_password_hash("testpassword123"),
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    ProfileService(db).initialize_empty_profiles(u.id)
    return u


_safe_ascii = st.characters(min_codepoint=0x20, max_codepoint=0x7E, blacklist_characters='\\"')
_member_name = st.text(min_size=1, max_size=48, alphabet=_safe_ascii)
_taboo_text = st.text(min_size=1, max_size=160, alphabet=_safe_ascii)


@given(scene=st.sampled_from(sorted(PROMPT_SCENES.keys())), name=_member_name)
@settings(
    max_examples=120,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_prompt_bundle_includes_profile_content(db_session, scene, name):
    """Property 13: Context bundle carries scene label and team member facts (6.1)."""
    u = _seed_user_with_profiles(db_session)
    m = TeamMember(user_id=u.id, name=name, role="Lead")
    db_session.add(m)
    db_session.commit()

    bundle = build_context_bundle(db_session, user_id=u.id, scene=scene)
    assert PROMPT_SCENES[scene] in bundle
    assert name in bundle
    assert "【团队 Team】" in bundle
    assert "【公司 Company】" in bundle
    assert "【工作 Work】" in bundle
    assert "【行业 Industry】" in bundle


@given(scene=st.sampled_from(sorted(PROMPT_SCENES.keys())), taboo=_taboo_text)
@settings(
    max_examples=120,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_taboo_injected_when_member_scoped(db_session, scene, taboo):
    """Property 16: Taboo text is present in the bundle for the selected member (7.1, 7.2)."""
    assume(taboo.strip() != "")
    u = _seed_user_with_profiles(db_session)
    m = TeamMember(user_id=u.id, name="Alex", role="PM", taboo=taboo)
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)

    bundle = build_context_bundle(
        db_session,
        user_id=u.id,
        scene=scene,
        team_member_id=m.id,
    )
    assert "### 禁忌约束" in bundle
    assert taboo.strip() in bundle


@given(notes=st.text(min_size=1, max_size=80, alphabet=_safe_ascii))
@settings(
    max_examples=120,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_profile_update_triggers_template_refresh(db_session, notes):
    """Property 15: After a team profile change, template_needs_refresh is true (6.7)."""
    u = _seed_user_with_profiles(db_session)
    m = TeamMember(user_id=u.id, name="Lead", role="Mgr", taboo="x")
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)

    sync_time = datetime.now(timezone.utc)
    row = SavedPromptTemplate(
        user_id=u.id,
        name="T",
        scene="research",
        body="body",
        version=1,
        last_synced_at=sync_time,
    )
    db_session.add(row)
    db_session.commit()

    m.notes = notes
    m.updated_at = datetime.now(timezone.utc)
    db_session.commit()

    assert template_needs_refresh(db_session, u.id, row.last_synced_at)
