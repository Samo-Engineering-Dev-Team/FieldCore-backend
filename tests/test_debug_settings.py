from app.api.v1 import router
from app.core.debug_middleware import DebugMiddleware
from app.services.auth import get_current_user


def test_debug_mode_is_master_switch_for_request_logging() -> None:
    settings = {
        "debug_mode": False,
        "enable_request_logging": True,
        "enable_performance_headers": True,
    }

    assert DebugMiddleware._is_request_logging_enabled(settings) is False
    assert DebugMiddleware._is_performance_headers_enabled(settings) is False


def test_debug_status_route_is_public() -> None:
    routes = [
        route
        for route in router.routes
        if getattr(route, "path", None) == "/v1/settings/debug/status"
    ]

    assert len(routes) == 1
    route = routes[0]
    dependencies = [dep.call for dep in route.dependant.dependencies]

    assert get_current_user not in dependencies


def test_debug_middleware_redacts_sensitive_nested_fields() -> None:
    middleware = DebugMiddleware(app=None)  # type: ignore[arg-type]

    redacted = middleware._redact_sensitive(
        {
            "query": "safe",
            "access_token": "secret-token",
            "nested": {
                "signed_url": "https://storage.example/private",
                "items": [{"password": "secret-password"}, {"name": "safe"}],
            },
        }
    )

    assert redacted["query"] == "safe"
    assert redacted["access_token"] == "[REDACTED]"
    assert redacted["nested"]["signed_url"] == "[REDACTED]"
    assert redacted["nested"]["items"][0]["password"] == "[REDACTED]"
    assert redacted["nested"]["items"][1]["name"] == "safe"
