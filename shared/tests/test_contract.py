from pathlib import Path

import yaml

EXPECTED_SURFACE = {
    ("post", "/v1/sessions"),
    ("get", "/v1/sessions/{session_id}/events"),
    ("post", "/v1/sessions/{session_id}/runs"),
    ("get", "/v1/runs/{run_id}"),
    ("post", "/v1/runs/{run_id}/cancel"),
    ("get", "/v1/agent"),
}


def load_contract() -> dict:
    raw = Path(__file__).resolve().parents[2] / "contract" / "openapi.yaml"
    return yaml.safe_load(raw.read_text(encoding="utf-8"))


def test_contract_parses_and_paths():
    spec = load_contract()

    surface = {(method, path) for path, item in spec["paths"].items() for method in item}

    assert surface == EXPECTED_SURFACE
    assert spec["openapi"].startswith("3.")


def test_run_view_schema_status_enum():
    spec = load_contract()
    status = spec["components"]["schemas"]["RunView"]["properties"]["status"]

    assert set(status["enum"]) == {"pending", "running", "succeeded", "failed", "cancelled"}
