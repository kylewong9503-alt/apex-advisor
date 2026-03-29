"""
Task 14 — performance targets (development / CI).

Production goals from the plan: login 500ms, save 1s, prompt generation 3s, review 5s.
CI uses PERF_CI_MULTIPLIER (default 4) to absorb cold starts; override with PERF_CI_MULTIPLIER=1 locally for stricter checks.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient

# Spec targets (seconds)
TARGET_LOGIN = 0.5
TARGET_SAVE = 1.0
TARGET_PROMPT = 3.0
TARGET_REVIEW = 5.0

CI_MULT = float(os.environ.get("PERF_CI_MULTIPLIER", "4"))


def _budget(target: float) -> float:
    return target * CI_MULT


@pytest.fixture
def perf_user(client: TestClient) -> tuple[dict, str]:
    email = f"perf-{uuid.uuid4().hex}@example.com"
    client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    return {}, email


def test_perf_login_under_budget(client: TestClient, perf_user: tuple):
    _, email = perf_user
    t0 = time.perf_counter()
    r = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "testpassword123"},
    )
    elapsed = time.perf_counter() - t0
    assert r.status_code == 200
    assert elapsed <= _budget(TARGET_LOGIN), (
        f"login took {elapsed:.3f}s; budget {_budget(TARGET_LOGIN):.3f}s "
        f"(production target {TARGET_LOGIN}s)"
    )


def test_perf_team_member_save_under_budget(client: TestClient, perf_user: tuple):
    _, email = perf_user
    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "testpassword123"},
    )
    h = {"Authorization": f"Bearer {login.json()['access_token']}"}
    t0 = time.perf_counter()
    r = client.post(
        "/api/v1/team/members",
        headers=h,
        json={"name": "Perf", "role": "QA"},
    )
    elapsed = time.perf_counter() - t0
    assert r.status_code == 201
    assert elapsed <= _budget(TARGET_SAVE), (
        f"save took {elapsed:.3f}s; budget {_budget(TARGET_SAVE):.3f}s "
        f"(production target {TARGET_SAVE}s)"
    )


def test_perf_prompt_generation_under_budget(client: TestClient, perf_user: tuple):
    _, email = perf_user
    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "testpassword123"},
    )
    h = {"Authorization": f"Bearer {login.json()['access_token']}"}
    t0 = time.perf_counter()
    r = client.post(
        "/api/v1/prompts/generate",
        headers=h,
        json={"scene": "research"},
    )
    elapsed = time.perf_counter() - t0
    assert r.status_code == 200
    assert elapsed <= _budget(TARGET_PROMPT), (
        f"prompt took {elapsed:.3f}s; budget {_budget(TARGET_PROMPT):.3f}s "
        f"(production target {TARGET_PROMPT}s)"
    )


def test_perf_deliverable_review_under_budget(client: TestClient, perf_user: tuple):
    _, email = perf_user
    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "testpassword123"},
    )
    h = {"Authorization": f"Bearer {login.json()['access_token']}"}
    std = client.post(
        "/api/v1/work/deliverable-standards",
        headers=h,
        json={"work_scene": "perf", "format_requirements": "md"},
    )
    assert std.status_code == 201
    sid = std.json()["id"]
    draft = "评审草稿正文。" * 20
    t0 = time.perf_counter()
    r = client.post(
        f"/api/v1/work/deliverable-standards/{sid}/review",
        headers=h,
        json={"draft_text": draft},
    )
    elapsed = time.perf_counter() - t0
    assert r.status_code == 200
    assert elapsed <= _budget(TARGET_REVIEW), (
        f"review took {elapsed:.3f}s; budget {_budget(TARGET_REVIEW):.3f}s "
        f"(production target {TARGET_REVIEW}s)"
    )
