"""
Property 3: Invalid credentials rejection (Requirements 1.3).

*For any* login attempt with a wrong password for an existing user, or with an email
that does not belong to any user, the API must reject authentication (no successful
login / no tokens).
"""

import uuid

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st


def _bcrypt_byte_safe(s: str) -> bool:
    """bcrypt only considers the first 72 bytes; keep generated secrets distinct in that range."""
    return 0 < len(s.encode("utf-8")) <= 72


# Printable ASCII without space-only edge cases that collide with "empty" semantics
_password_chars = st.characters(
    whitelist_categories=("Lu", "Ll", "Nd"),
    blacklist_characters="\x00",
)

valid_password = st.text(min_size=8, max_size=48, alphabet=_password_chars).filter(
    _bcrypt_byte_safe
)


@given(
    registered_password=valid_password,
    attempted_password=valid_password,
)
@settings(
    max_examples=120,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_wrong_password_never_logs_in(
    client, registered_password, attempted_password
):
    """Existing user + incorrect password => 401, no tokens."""
    assume(registered_password != attempted_password)
    # Fresh email per Hypothesis example: DB persists across examples on one client.
    email = f"prop-pw-{uuid.uuid4().hex}@example.com"
    reg = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": registered_password},
    )
    assert reg.status_code == 201

    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": attempted_password},
    )
    assert login.status_code == 401
    body = login.json()
    assert "detail" in body
    assert "Incorrect" in body["detail"] or "incorrect" in body["detail"].lower()
    assert "access_token" not in body


@given(password=valid_password)
@settings(
    max_examples=120,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_unknown_email_never_logs_in(client, password):
    """Non-existent email => 401 even if password matches another user's password."""
    registered_email = f"prop-a-{uuid.uuid4().hex}@example.com"
    unknown_email = f"prop-b-{uuid.uuid4().hex}@example.com"

    reg = client.post(
        "/api/v1/auth/register",
        json={"email": registered_email, "password": password},
    )
    assert reg.status_code == 201

    login = client.post(
        "/api/v1/auth/login",
        json={"email": unknown_email, "password": password},
    )
    assert login.status_code == 401
    body = login.json()
    assert "detail" in body
    assert "Incorrect" in body["detail"] or "incorrect" in body["detail"].lower()
    assert "access_token" not in body
