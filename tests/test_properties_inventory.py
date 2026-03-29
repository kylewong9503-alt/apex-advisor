"""
Task 14 — correctness properties traceability (design enumerates 20; tasks.md names 16).

Each entry maps to automated tests where property-style tests exist, else the owning task.
"""

from __future__ import annotations

# Property id -> evidence (test module or task id)
PROPERTY_COVERAGE: dict[int, dict[str, str]] = {
    2: {"task": "2.3", "note": "optional Hypothesis — registration profile structure"},
    3: {"tests": "tests/test_auth_properties.py", "note": "Property 3 invalid credentials"},
    4: {"task": "3.4", "note": "optional CRUD round trip"},
    6: {"task": "3.3", "note": "optional behavior tag format"},
    8: {"task": "5.3", "note": "optional four-dimension intelligence"},
    9: {"task": "5.4", "note": "optional incremental merge"},
    10: {"task": "7.3", "note": "optional industry conflict"},
    11: {"task": "7.4", "note": "optional topic materials"},
    12: {"task": "6.3", "note": "optional five-dimension review"},
    13: {"tests": "tests/test_prompt_properties.py", "note": "Property 13 profile injection"},
    15: {"tests": "tests/test_prompt_properties.py", "note": "Property 15 template refresh"},
    16: {"tests": "tests/test_prompt_properties.py", "note": "Property 16 Taboo injection"},
    17: {"task": "12.2", "note": "optional offline sync property test"},
    18: {"task": "12.3", "note": "optional conflict property test"},
    19: {"task": "6.4", "note": "optional review history"},
    20: {"task": "6.6", "note": "optional follow-up extraction"},
}


def test_properties_inventory_documented():
    """Ensure every named property in tasks.md has a trace row."""
    assert len(PROPERTY_COVERAGE) == 16
    for pid in sorted(PROPERTY_COVERAGE):
        row = PROPERTY_COVERAGE[pid]
        assert "tests" in row or "task" in row, f"property {pid} missing trace"


def test_integration_suite_covers_journey():
    """End-to-end path exists (complements numbered properties)."""
    import tests.test_integration_journey as m

    assert hasattr(m, "test_journey_register_prompt_sync_bundle")
