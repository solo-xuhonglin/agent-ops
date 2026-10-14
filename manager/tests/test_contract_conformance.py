from pathlib import Path

import yaml
from fastapi.routing import APIRoute
from manager.api import router

# include_router no longer flattens routes into app.routes (it nests an
# _IncludedRouter), so the surface is read from the api router itself; the
# API tests prove the app actually mounts it.


def load_contract() -> dict:
    raw = Path(__file__).resolve().parents[2] / "contract" / "openapi.yaml"
    return yaml.safe_load(raw.read_text(encoding="utf-8"))


def test_contract_matches_app_routes():
    spec = load_contract()
    contract = {(method, path) for path, item in spec["paths"].items() for method in item}

    app_surface = {
        (method.lower(), route.path)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }

    assert app_surface == contract
