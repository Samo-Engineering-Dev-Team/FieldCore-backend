"""Small Prometheus text metrics collector with tenant labels."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from threading import Lock
from time import perf_counter
from typing import DefaultDict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


MetricLabels = tuple[tuple[str, str], ...]


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(values: dict[str, str]) -> MetricLabels:
    return tuple(sorted((key, str(value)) for key, value in values.items()))


def _render_labels(labels: MetricLabels) -> str:
    if not labels:
        return ""
    pairs = ",".join(f'{key}="{_escape_label(value)}"' for key, value in labels)
    return f"{{{pairs}}}"


@dataclass(frozen=True)
class RequestMetric:
    tenant_id: str
    method: str
    path: str
    status_code: int
    duration_seconds: float


class TenantMetrics:
    """Thread-safe counters for Prometheus/Grafana scraping."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._http_requests: DefaultDict[MetricLabels, int] = defaultdict(int)
        self._http_duration_sum: DefaultDict[MetricLabels, float] = defaultdict(float)
        self._rate_limit_hits: DefaultDict[MetricLabels, int] = defaultdict(int)
        self._rate_limit_bypasses: DefaultDict[MetricLabels, int] = defaultdict(int)

    def record_request(self, metric: RequestMetric) -> None:
        labels = _labels(
            {
                "tenant_id": metric.tenant_id,
                "method": metric.method,
                "path": metric.path,
                "status_code": str(metric.status_code),
            }
        )
        with self._lock:
            self._http_requests[labels] += 1
            self._http_duration_sum[labels] += metric.duration_seconds

    def record_rate_limit_hit(self, *, tenant_id: str, subject_type: str) -> None:
        labels = _labels({"tenant_id": tenant_id, "subject_type": subject_type})
        with self._lock:
            self._rate_limit_hits[labels] += 1

    def record_rate_limit_bypass(self, *, tenant_id: str, role: str) -> None:
        labels = _labels({"tenant_id": tenant_id, "role": role})
        with self._lock:
            self._rate_limit_bypasses[labels] += 1

    def reset(self) -> None:
        with self._lock:
            self._http_requests.clear()
            self._http_duration_sum.clear()
            self._rate_limit_hits.clear()
            self._rate_limit_bypasses.clear()

    def render_prometheus(self) -> str:
        with self._lock:
            request_items = list(self._http_requests.items())
            duration_items = list(self._http_duration_sum.items())
            hit_items = list(self._rate_limit_hits.items())
            bypass_items = list(self._rate_limit_bypasses.items())

        lines = [
            "# HELP fieldcore_http_requests_total HTTP requests partitioned by tenant.",
            "# TYPE fieldcore_http_requests_total counter",
        ]
        for labels, value in request_items:
            lines.append(f"fieldcore_http_requests_total{_render_labels(labels)} {value}")

        lines.extend(
            [
                "# HELP fieldcore_http_request_duration_seconds_sum HTTP request duration seconds.",
                "# TYPE fieldcore_http_request_duration_seconds_sum counter",
            ]
        )
        for labels, value in duration_items:
            lines.append(
                f"fieldcore_http_request_duration_seconds_sum{_render_labels(labels)} {value:.6f}"
            )

        lines.extend(
            [
                "# HELP fieldcore_rate_limit_hits_total Per-tenant rate-limit blocks.",
                "# TYPE fieldcore_rate_limit_hits_total counter",
            ]
        )
        for labels, value in hit_items:
            lines.append(f"fieldcore_rate_limit_hits_total{_render_labels(labels)} {value}")

        lines.extend(
            [
                "# HELP fieldcore_rate_limit_bypasses_total Emergency rate-limit bypasses.",
                "# TYPE fieldcore_rate_limit_bypasses_total counter",
            ]
        )
        for labels, value in bypass_items:
            lines.append(f"fieldcore_rate_limit_bypasses_total{_render_labels(labels)} {value}")

        return "\n".join(lines) + "\n"


tenant_metrics = TenantMetrics()


def tenant_label_from_request(request: Request) -> str:
    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id:
        return str(tenant_id)
    return "anonymous"


def route_path_from_request(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return str(path)
    return request.url.path


class TenantMetricsMiddleware(BaseHTTPMiddleware):
    """Record request count and latency with tenant labels."""

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path == "/metrics":
            return await call_next(request)

        started = perf_counter()
        response = await call_next(request)
        duration = perf_counter() - started
        tenant_metrics.record_request(
            RequestMetric(
                tenant_id=tenant_label_from_request(request),
                method=request.method,
                path=route_path_from_request(request),
                status_code=response.status_code,
                duration_seconds=duration,
            )
        )
        return response
