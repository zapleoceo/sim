"""The OpenAPI schema and Swagger UI are not public in production."""
from __future__ import annotations

from sim.api.main import create_app
from tests.conftest import make_settings


def test_docs_are_public_outside_prod_and_hidden_in_prod(tmp_path):
    dev = create_app(make_settings(tmp_path))
    assert dev.docs_url == "/api/docs" and dev.openapi_url == "/api/openapi.json"
    prod_settings = make_settings(tmp_path)
    prod_settings.env = "prod"
    prod = create_app(prod_settings)
    assert prod.docs_url is None and prod.redoc_url is None and prod.openapi_url is None
