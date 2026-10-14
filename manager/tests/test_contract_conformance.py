from pathlib import Path

import yaml
from fastapi.routing import APIRoute
from manager.app import app


def load_contract() -> dict:
    raw = Path(__file__).resolve().parents[2] / "contract" / "openapi.yaml"
    return yaml.safe_load(raw.read_text(encoding="utf-8"))


def test_app_routes_within_contract():
    spec = load_contract()
    contract = {(method, path) for path, item in spec["paths"].items() for method in item}

    app_surface = {
        (method.lower(), route.path)
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/v1")
        for method in route.methods
    }

    assert app_surface <= contract
