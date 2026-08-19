#!/usr/bin/env python3
"""Generate trybe-grafana dashboards (Grafana dashboard schema v2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

PROM = "grafanacloud-prom"
LOKI = "grafanacloud-logs"
VIZ_VERSION = "13.2.0-30402795349"
OUT = Path(__file__).parent

UID_PLATFORM = "dbfvo1lxkhqi9sd"
UID_REQUESTS = "ddfvo1r1k0xczka"
UID_QUEUES = "dffvo1phlivqwwc"
UID_REDIS = "trybe-redis-valkey"
UID_COMMERCE = "trybe-commerce-funnel"
UID_SITE = "trybe-site-health"
UID_FORENSICS = "trybe-performance-forensics"
UID_ERRORS = "trybe-errors-exceptions"

# (title, Prometheus scrape_job, Loki deployment_environment / APP_ENV)
# Staging scrape job is stage-; APP_ENV may be staging or stage.
ENVIRONMENTS = (
    ("Playground", "playground-metrics-scrape", ("playground",)),
    ("Staging", "stage-metrics-scrape", ("staging", "stage")),
    ("Production", "production-metrics-scrape", ("production",)),
)

# Drop high-cardinality stream labels before unwrap / count_over_time so Loki
# stays under the 500-series metric query cap.
LOKI_DROP = (
    "instance, k8s_pod_name, k8s_container_name, exported_instance, "
    "detected_level, exporter, telemetry_sdk_name, telemetry_sdk_language, "
    "telemetry_sdk_version, service_version, service_namespace"
)


def env(**labels: str) -> str:
    parts = ['scrape_job=~"$environment"'] + [f'{k}="{v}"' for k, v in labels.items()]
    return "{" + ",".join(parts) + "}"


def loki_stream() -> str:
    return '{service_name=~"$service", deployment_environment=~"$deployment_environment"}'


def loki_event(event: str, extra: str = "") -> str:
    q = f'{loki_stream()} |= "{event}"'
    if extra:
        q += f" {extra}"
    return f"{q} | drop {LOKI_DROP}"


def loki_unwrap(event: str, field: str = "duration_ms", extra: str = "") -> str:
    return f"{loki_event(event, extra)} | unwrap {field}"


class DashboardBuilder:
    def __init__(self) -> None:
        self.panel_id = 0
        self.ref_id = 0
        self.elements: dict[str, Any] = {}

    def _next_panel_id(self) -> int:
        self.panel_id += 1
        return self.panel_id

    def _next_ref(self) -> str:
        self.ref_id += 1
        return f"Q{self.ref_id}"

    def prom_data_query(self, expr: str, legend: str | None = None) -> dict[str, Any]:
        spec: dict[str, Any] = {"expr": expr}
        if legend is not None:
            spec["legendFormat"] = legend
        return {
            "datasource": {"name": PROM},
            "group": "prometheus",
            "kind": "DataQuery",
            "spec": spec,
            "version": "v0",
        }

    def loki_data_query(
        self,
        expr: str,
        legend: str | None = None,
        *,
        instant: bool = False,
    ) -> dict[str, Any]:
        spec: dict[str, Any] = {
            "expr": expr,
            "queryType": "instant" if instant else "range",
            "maxLines": 100,
        }
        if legend is not None:
            spec["legendFormat"] = legend
        return {
            "datasource": {"name": LOKI},
            "group": "loki",
            "kind": "DataQuery",
            "spec": spec,
            "version": "v0",
        }

    def query_group(
        self,
        queries: list[tuple[dict[str, Any], str | None]],
    ) -> dict[str, Any]:
        return {
            "kind": "QueryGroup",
            "spec": {
                "queries": [
                    {
                        "kind": "PanelQuery",
                        "spec": {
                            "hidden": False,
                            "query": q,
                            "refId": self._next_ref(),
                        },
                    }
                    for q, _ in queries
                ],
                "queryOptions": {},
                "transformations": [],
            },
        }

    def panel(
        self,
        title: str,
        viz: str,
        queries: list[tuple[dict[str, Any], str | None]],
        *,
        description: str = "",
        width: int = 12,
        height: int = 8,
        field_defaults: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        name = f"panel-{self._next_panel_id()}"
        defaults = field_defaults or {}
        viz_options = options or {}

        if viz == "stat":
            viz_options.setdefault("colorMode", "value")
            viz_options.setdefault("graphMode", "area")
            viz_options.setdefault(
                "reduceOptions",
                {"calcs": ["lastNotNull"], "fields": "", "values": False},
            )
        elif viz == "timeseries":
            defaults.setdefault("custom", {"drawStyle": "line", "fillOpacity": 10, "lineWidth": 2, "showPoints": "never"})
            viz_options.setdefault(
                "legend",
                {"displayMode": "list", "placement": "bottom", "showLegend": True},
            )
            viz_options.setdefault("tooltip", {"mode": "multi", "sort": "desc"})
        elif viz == "gauge":
            viz_options.setdefault("showThresholdLabels", False)
            viz_options.setdefault("showThresholdMarkers", True)
        elif viz == "table":
            defaults.setdefault("custom", {"align": "auto"})
            viz_options.setdefault("showHeader", True)
        elif viz == "logs":
            viz_options.setdefault("dedupStrategy", "none")
            viz_options.setdefault("enableLogDetails", True)
            viz_options.setdefault("showTime", True)
            viz_options.setdefault("sortOrder", "Descending")
            viz_options.setdefault("wrapLogMessage", True)

        self.elements[name] = {
            "kind": "Panel",
            "spec": {
                "data": self.query_group(queries),
                "description": description,
                "id": self.panel_id,
                "links": [],
                "title": title,
                "vizConfig": {
                    "group": viz,
                    "kind": "VizConfig",
                    "spec": {
                        "fieldConfig": {"defaults": defaults, "overrides": []},
                        "options": viz_options,
                    },
                    "version": VIZ_VERSION if viz != "table" else "",
                },
            },
        }
        return name, {"width": width, "height": height}

    def stat(
        self,
        title: str,
        expr: str,
        *,
        unit: str = "short",
        decimals: int = 2,
        thresholds: list[tuple[float, str]] | None = None,
        description: str = "",
        width: int = 4,
        height: int = 4,
    ) -> tuple[str, dict[str, Any]]:
        steps = [{"color": "green", "value": 0}]
        if thresholds:
            steps = [{"color": color, "value": value} for value, color in thresholds]
        return self.panel(
            title,
            "stat",
            [(self.prom_data_query(expr), None)],
            description=description,
            width=width,
            height=height,
            field_defaults={
                "color": {"mode": "thresholds"},
                "decimals": decimals,
                "thresholds": {"mode": "absolute", "steps": steps},
                "unit": unit,
            },
        )

    def loki_stat(
        self,
        title: str,
        expr: str,
        *,
        unit: str = "short",
        decimals: int = 0,
        description: str = "",
        width: int = 4,
        height: int = 4,
    ) -> tuple[str, dict[str, Any]]:
        return self.panel(
            title,
            "stat",
            [(self.loki_data_query(expr, instant=True), None)],
            description=description,
            width=width,
            height=height,
            field_defaults={
                "color": {"mode": "thresholds"},
                "decimals": decimals,
                "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": 0}]},
                "unit": unit,
            },
        )

    def timeseries(
        self,
        title: str,
        series: list[tuple[str, str]],
        *,
        description: str = "",
        unit: str = "short",
        width: int = 12,
        height: int = 8,
        loki: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        queries = []
        for expr, legend in series:
            q = self.loki_data_query(expr, legend) if loki else self.prom_data_query(expr, legend)
            queries.append((q, legend))
        return self.panel(
            title,
            "timeseries",
            queries,
            description=description,
            width=width,
            height=height,
            field_defaults={"unit": unit},
        )

    def gauge(
        self,
        title: str,
        expr: str,
        *,
        min_val: float = 0,
        max_val: float = 100,
        unit: str = "percent",
        thresholds: list[tuple[float, str]] | None = None,
        description: str = "",
        width: int = 6,
        height: int = 6,
    ) -> tuple[str, dict[str, Any]]:
        steps = [{"color": "red", "value": 0}, {"color": "yellow", "value": 20}, {"color": "green", "value": 40}]
        if thresholds:
            steps = [{"color": color, "value": value} for value, color in thresholds]
        return self.panel(
            title,
            "gauge",
            [(self.prom_data_query(expr), None)],
            description=description,
            width=width,
            height=height,
            field_defaults={
                "color": {"mode": "thresholds"},
                "decimals": 1,
                "max": max_val,
                "min": min_val,
                "thresholds": {"mode": "absolute", "steps": steps},
                "unit": unit,
            },
        )

    def table(
        self,
        title: str,
        series: list[tuple[str, str]],
        *,
        description: str = "",
        width: int = 24,
        height: int = 10,
        loki: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        queries = []
        for expr, legend in series:
            q = self.loki_data_query(expr, legend) if loki else self.prom_data_query(expr, legend)
            queries.append((q, legend))
        return self.panel(title, "table", queries, description=description, width=width, height=height)

    def logs(
        self,
        title: str,
        expr: str,
        *,
        description: str = "",
        width: int = 24,
        height: int = 12,
    ) -> tuple[str, dict[str, Any]]:
        return self.panel(
            title,
            "logs",
            [(self.loki_data_query(expr), None)],
            description=description,
            width=width,
            height=height,
        )

    def layout(self, rows: list[tuple[str, list[tuple[str, dict[str, Any], int, int]]]]) -> dict[str, Any]:
        layout_rows = []
        for title, panels in rows:
            items = []
            for name, size, x, y in panels:
                items.append(
                    {
                        "kind": "GridLayoutItem",
                        "spec": {
                            "element": {"kind": "ElementReference", "name": name},
                            "height": size["height"],
                            "width": size["width"],
                            "x": x,
                            "y": y,
                        },
                    }
                )
            layout_rows.append(
                {
                    "kind": "RowsLayoutRow",
                    "spec": {
                        "collapse": False,
                        "layout": {"kind": "GridLayout", "spec": {"items": items}},
                        "title": title,
                    },
                }
            )
        return {"kind": "RowsLayout", "spec": {"rows": layout_rows}}


def environment_variable() -> dict[str, Any]:
    return {
        "datasource": {"name": PROM},
        "group": "prometheus",
        "kind": "QueryVariable",
        "spec": {
            "allowCustomValue": True,
            "allValue": ".*",
            "current": {"text": "All", "value": "$__all"},
            "hide": "dontHide",
            "includeAll": True,
            "label": "Environment",
            "multi": True,
            "name": "environment",
            "options": [],
            "query": {
                "datasource": {"name": PROM},
                "group": "prometheus",
                "kind": "DataQuery",
                "spec": {"query": "label_values(app_requests_completed_total, scrape_job)"},
                "version": "v0",
            },
            "refresh": "onDashboardLoad",
            "regex": "",
            "skipUrlSync": False,
            "sort": "alphabeticalAsc",
        },
    }


def service_variable() -> dict[str, Any]:
    return {
        "datasource": {"name": LOKI},
        "group": "loki",
        "kind": "QueryVariable",
        "spec": {
            "allowCustomValue": True,
            "allValue": ".*shop-api",
            "current": {"text": "All", "value": "$__all"},
            "hide": "dontHide",
            "includeAll": True,
            "label": "Service",
            "multi": True,
            "name": "service",
            "options": [],
            "query": {
                "datasource": {"name": LOKI},
                "group": "loki",
                "kind": "DataQuery",
                "spec": {
                    "label": "service_name",
                    "stream": '{service_name=~".*shop-api"}',
                    "type": "label_values",
                },
                "version": "v0",
            },
            "refresh": "onDashboardLoad",
            "regex": "",
            "skipUrlSync": False,
            "sort": "alphabeticalAsc",
        },
    }


def deployment_environment_variable() -> dict[str, Any]:
    return {
        "datasource": {"name": LOKI},
        "group": "loki",
        "kind": "QueryVariable",
        "spec": {
            "allowCustomValue": True,
            "allValue": ".*",
            "current": {"text": "All", "value": "$__all"},
            "hide": "dontHide",
            "includeAll": True,
            "label": "Deployment",
            "multi": True,
            "name": "deployment_environment",
            "options": [],
            "query": {
                "datasource": {"name": LOKI},
                "group": "loki",
                "kind": "DataQuery",
                "spec": {
                    "label": "deployment_environment",
                    "stream": '{service_name=~".*shop-api"}',
                    "type": "label_values",
                },
                "version": "v0",
            },
            "refresh": "onDashboardLoad",
            "regex": "",
            "skipUrlSync": False,
            "sort": "alphabeticalAsc",
        },
    }


def site_id_variable() -> dict[str, Any]:
    return {
        "datasource": {"name": LOKI},
        "group": "loki",
        "kind": "QueryVariable",
        "spec": {
            "allowCustomValue": True,
            "allValue": ".+",
            "current": {"text": "All", "value": "$__all"},
            "hide": "dontHide",
            "includeAll": True,
            "label": "Site ID",
            "multi": True,
            "name": "site_id",
            "options": [],
            "query": {
                "datasource": {"name": LOKI},
                "group": "loki",
                "kind": "DataQuery",
                "spec": {
                    "label": "site_id",
                    "stream": loki_stream(),
                    "type": "label_values",
                },
                "version": "v0",
            },
            "refresh": "onDashboardLoad",
            "regex": "",
            "skipUrlSync": False,
            "sort": "alphabeticalAsc",
        },
    }


def default_variables() -> list[dict[str, Any]]:
    return [environment_variable(), service_variable(), deployment_environment_variable()]


def uid_link(title: str, uid: str) -> dict[str, Any]:
    return {
        "title": title,
        "type": "link",
        "icon": "dashboard",
        "tooltip": title,
        "url": f"/d/{uid}",
        "tags": [],
        "asDropdown": False,
        "targetBlank": False,
        "includeVars": True,
        "keepTime": True,
    }


def grafana_slug(title: str) -> str:
    """Match Grafana Cloud's /d/{uid}/{slug} encoding (non-ASCII → UTF-8 hex)."""
    parts: list[str] = []
    for ch in title:
        if ch.isalnum():
            parts.append(ch.lower())
        elif ch in " -":
            parts.append("-")
        else:
            parts.append(quote(ch, safe="").replace("%", "").lower())
    return "".join(parts)


def env_link(
    title: str,
    scrape_job: str,
    uid: str,
    slug: str,
    deployment_envs: tuple[str, ...],
) -> dict[str, Any]:
    dep_qs = "&".join(f"var-deployment_environment={value}" for value in deployment_envs)
    return {
        "title": title,
        "type": "link",
        "icon": "cloud",
        "tooltip": f"This dashboard scoped to {title} ({scrape_job})",
        "url": f"/d/{uid}/{slug}?var-environment={scrape_job}&var-service=$__all&{dep_qs}",
        "tags": [],
        "asDropdown": False,
        "targetBlank": False,
        "includeVars": False,
        "keepTime": True,
    }


def shared_links(uid: str, title: str) -> list[dict[str, Any]]:
    slug = grafana_slug(title)
    env_links = [env_link(name, job, uid, slug, dep_envs) for name, job, dep_envs in ENVIRONMENTS]
    nav = [
        uid_link("Platform Health", UID_PLATFORM),
        uid_link("Requests — Full Overview", UID_REQUESTS),
        uid_link("Queues & Jobs", UID_QUEUES),
        uid_link("Redis / Valkey", UID_REDIS),
        uid_link("Commerce Funnel", UID_COMMERCE),
        uid_link("Site Health", UID_SITE),
        uid_link("Performance Forensics", UID_FORENSICS),
        uid_link("Errors & Exceptions", UID_ERRORS),
    ]
    return env_links + nav


def build_dashboard(
    *,
    uid: str,
    title: str,
    description: str,
    tags: list[str],
    builder: DashboardBuilder,
    rows: list[tuple[str, list[tuple[str, dict[str, Any], int, int]]]],
    variables: list[dict[str, Any]] | None = None,
    links: list[dict[str, Any]] | None = None,
    time_from: str = "now-1h",
) -> dict[str, Any]:
    return {
        "apiVersion": "dashboard.grafana.app/v2",
        "kind": "Dashboard",
        "metadata": {"name": uid},
        "spec": {
            "annotations": [
                {
                    "kind": "AnnotationQuery",
                    "spec": {
                        "builtIn": True,
                        "enable": True,
                        "hide": True,
                        "iconColor": "rgba(0, 211, 255, 1)",
                        "legacyOptions": {"type": "dashboard"},
                        "name": "Annotations & Alerts",
                        "query": {
                            "datasource": {"name": "-- Grafana --"},
                            "group": "grafana",
                            "kind": "DataQuery",
                            "spec": {},
                            "version": "v0",
                        },
                    },
                }
            ],
            "cursorSync": "Off",
            "description": description,
            "editable": True,
            "elements": builder.elements,
            "layout": builder.layout(rows),
            "links": links if links is not None else shared_links(uid, title),
            "liveNow": False,
            "preload": False,
            "tags": tags,
            "timeSettings": {
                "autoRefresh": "30s",
                "autoRefreshIntervals": ["5s", "10s", "30s", "1m", "5m", "15m", "30m", "1h", "2h", "1d"],
                "fiscalYearStartMonth": 0,
                "from": time_from,
                "hideTimepicker": False,
                "timezone": "browser",
                "to": "now",
            },
            "title": title,
            "variables": variables or default_variables(),
        },
    }


def place_row(panels: list[tuple[str, dict[str, Any]]], cols: int = 24) -> list[tuple[str, dict[str, Any], int, int]]:
    placed: list[tuple[str, dict[str, Any], int, int]] = []
    x = y = 0
    row_height = 0
    for name, size in panels:
        w, h = size["width"], size["height"]
        if x + w > cols:
            x = 0
            y += row_height
            row_height = 0
        placed.append((name, size, x, y))
        x += w
        row_height = max(row_height, h)
    return placed


def build_platform_health() -> dict[str, Any]:
    b = DashboardBuilder()
    e = env

    stats = [
        b.stat("Request Rate", f"sum(rate(app_requests_completed_total{e()}[5m]))", unit="reqps", description="Completed HTTP requests/sec"),
        b.stat("Error Rate (5xx)", f'(100 * sum(rate(app_requests_completed_total{e(status_class="5xx")}[5m])) / clamp_min(sum(rate(app_requests_completed_total{e()}[5m])),0.0001)) or vector(0)', unit="percent", thresholds=[(0, "green"), (1, "yellow"), (5, "red")]),
        b.stat("Client Errors (4xx)", f'(100 * sum(rate(app_requests_completed_total{e(status_class="4xx")}[5m])) / clamp_min(sum(rate(app_requests_completed_total{e()}[5m])),0.0001)) or vector(0)', unit="percent", thresholds=[(0, "green"), (10, "yellow"), (25, "red")]),
        b.stat("Avg Latency", f'sum(rate(app_requests_duration_total{e()}[5m])) / clamp_min(sum(rate(app_requests_completed_total{e()}[5m])),0.0001)', unit="ms", decimals=0),
        b.stat("Server Errors (range)", f'sum(increase(app_requests_server_error_total{e()}[$__range])) or vector(0)', unit="short", decimals=0),
        b.stat("Success Rate (2xx)", f'(100 * sum(rate(app_requests_completed_total{e(status_class="2xx")}[5m])) / clamp_min(sum(rate(app_requests_completed_total{e()}[5m])),0.0001)) or vector(0)', unit="percent", thresholds=[(0, "red"), (90, "yellow"), (95, "green")]),
    ]
    charts = [
        b.timeseries("Request Rate by Status Class", [(f'sum by (status_class) (rate(app_requests_completed_total{e()}[5m]))', "{{status_class}}")], unit="reqps", width=12),
        b.timeseries("Avg Latency by Status Class", [(f'sum by (status_class)(rate(app_requests_duration_total{e()}[5m])) / clamp_min(sum by (status_class)(rate(app_requests_completed_total{e()}[5m])),0.0001)', "{{status_class}}")], unit="ms", width=12),
    ]
    queue_stats = [
        b.stat("Jobs Dispatched /s", f"sum(rate(app_jobs_dispatched_total{e()}[5m]))", unit="ops"),
        b.stat("Jobs Processed /s", f"sum(rate(app_jobs_processed_total{e()}[5m]))", unit="ops"),
        b.stat("Jobs Failed (range)", f'sum(increase(app_jobs_failed_total{e()}[$__range])) or vector(0)', unit="short", decimals=0),
        b.stat("Queue Size", f"sum(app_queue_size{e()})", unit="short", decimals=0, thresholds=[(0, "green"), (100, "yellow"), (1000, "red")]),
        b.stat("Pending Jobs", f"sum(app_queue_pending_jobs{e()})", unit="short", decimals=0, thresholds=[(0, "green"), (100, "yellow"), (1000, "red")]),
        b.stat("Oldest Pending Job", f"max(app_queue_oldest_pending_job_age_seconds{e()})", unit="s", decimals=0),
    ]
    redis_stats = [
        b.stat("Redis Hit Rate", "rate(app_redis_keyspace_hits_total[1m]) / clamp_min(rate(app_redis_keyspace_hits_total[1m]) + rate(app_redis_keyspace_misses_total[1m]), 1e-9)", unit="percentunit", decimals=2, thresholds=[(0, "red"), (0.8, "yellow"), (0.95, "green")]),
        b.stat("Redis Ops / sec", "app_redis_instantaneous_ops_per_sec", unit="ops", decimals=0),
        b.stat("Redis Used Memory", "app_redis_used_memory_bytes", unit="bytes", decimals=1),
        b.stat("Redis Clients", "app_redis_connected_clients", unit="short", decimals=0),
    ]
    commerce = [
        b.stat("Baskets Created /min", f"sum(rate(app_baskets_created_total{e()}[5m]))*60", unit="cpm", decimals=1),
        b.stat("Baskets Submitted /min", f"sum(rate(app_baskets_submitted_total{e()}[5m]))*60", unit="cpm", decimals=1),
        b.gauge("Checkout Conversion", f'100 * sum(increase(app_baskets_submitted_total{e()}[$__range])) / clamp_min(sum(increase(app_baskets_created_total{e()}[$__range])),1)', width=8, height=4),
        b.stat("Metrics APCu Enabled", "app_metrics_apcu_enabled", unit="short", decimals=0, description="1 = counter aggregation via APCu is active on this scrape target"),
    ]

    return build_dashboard(
        uid=UID_PLATFORM,
        title="Platform Health",
        description="One-screen shop-api health: API golden signals, queue depth, Redis, checkout funnel, and metrics pipeline status.",
        tags=["trybe", "shop-api", "trybe-overview"],
        builder=b,
        rows=[
            ("Golden Signals — API Health", place_row(stats)),
            ("Traffic & Latency", place_row(charts)),
            ("Background Jobs & Queue Health", place_row(queue_stats + [b.timeseries("Job Throughput", [(f"sum(rate(app_jobs_dispatched_total{e()}[5m]))", "dispatched"), (f"sum(rate(app_jobs_processed_total{e()}[5m]))", "processed"), (f"sum(rate(app_jobs_failed_total{e()}[5m]))", "failed")], width=24, height=8)])),
            ("Redis at a Glance", place_row(redis_stats)),
            ("Product & Business Metrics", place_row(commerce)),
        ],
    )


def build_redis() -> dict[str, Any]:
    b = DashboardBuilder()
    e = env

    stats = [
        b.stat("Ops / sec", f"app_redis_instantaneous_ops_per_sec{e()}", unit="ops", decimals=0, thresholds=[(0, "green"), (20000, "yellow"), (80000, "red")]),
        b.stat("Connected Clients", f"app_redis_connected_clients{e()}", unit="short", decimals=0),
        b.stat("Used Memory", f"app_redis_used_memory_bytes{e()}", unit="bytes", decimals=1),
        b.stat("Hit Rate", "rate(app_redis_keyspace_hits_total[1m]) / clamp_min(rate(app_redis_keyspace_hits_total[1m]) + rate(app_redis_keyspace_misses_total[1m]), 1e-9)", unit="percentunit", decimals=2, thresholds=[(0, "red"), (0.8, "yellow"), (0.95, "green")]),
    ]
    charts = [
        b.timeseries("Keyspace Hits / Misses", [("rate(app_redis_keyspace_hits_total[1m])", "hits"), ("rate(app_redis_keyspace_misses_total[1m])", "misses")], unit="ops", width=12),
        b.timeseries("Ops / sec", [("app_redis_instantaneous_ops_per_sec", "ops/s")], unit="ops", width=12),
        b.timeseries("Used Memory", [("app_redis_used_memory_bytes", "memory")], unit="bytes", width=12),
        b.timeseries("Evictions / Clients", [("rate(app_redis_evicted_keys_total[1m])", "evictions/s"), ("app_redis_connected_clients", "clients")], width=12),
    ]
    transport = [
        b.stat("Metrics APCu Enabled", "app_metrics_apcu_enabled", unit="short", decimals=0, description="Counter aggregation layer — reduces Redis write volume from PHP workers"),
        b.timeseries("Counter Write Pressure (requests + jobs)", [
            (f"sum(rate(app_requests_completed_total{e()}[5m]))", "requests/s"),
            (f"sum(rate(app_jobs_processed_total{e()}[5m]))", "jobs/s"),
        ], description="High traffic with APCu off increases direct Redis counter writes", width=16),
    ]

    return build_dashboard(
        uid=UID_REDIS,
        title="Redis / Valkey",
        description="Live Redis INFO from /shop/internal/metrics — hits, misses, memory, clients, evictions, and ops/sec.",
        tags=["trybe", "shop-api", "trybe-redis"],
        builder=b,
        rows=[
            ("Redis Health", place_row(stats)),
            ("Trends", place_row(charts)),
            ("Metrics Transport", place_row(transport)),
        ],
    )


def build_requests() -> dict[str, Any]:
    b = DashboardBuilder()
    e = env
    ri = "$__rate_interval"

    stats = [
        b.stat("Request Rate", f"sum(rate(app_requests_completed_total{e()}[{ri}]))", unit="reqps"),
        b.stat("Success Rate (2xx)", f'100 * sum(rate(app_requests_completed_total{e(status_class="2xx")}[{ri}])) / clamp_min(sum(rate(app_requests_completed_total{e()}[{ri}])),0.0001)', unit="percent"),
        b.stat("Client Errors (4xx %)", f'(100 * sum(rate(app_requests_completed_total{e(status_class="4xx")}[{ri}])) / clamp_min(sum(rate(app_requests_completed_total{e()}[{ri}])),0.0001)) or vector(0)', unit="percent"),
        b.stat("Error Rate (5xx %)", f'(100 * sum(rate(app_requests_completed_total{e(status_class="5xx")}[{ri}])) / clamp_min(sum(rate(app_requests_completed_total{e()}[{ri}])),0.0001)) or vector(0)', unit="percent"),
        b.stat("Avg Latency", f'sum(rate(app_requests_duration_total{e()}[{ri}])) / clamp_min(sum(rate(app_requests_completed_total{e()}[{ri}])),0.0001)', unit="ms", decimals=0),
        b.stat("Server Errors (range)", f'sum(increase(app_requests_server_error_total{e()}[$__range])) or vector(0)', unit="short", decimals=0),
    ]

    return build_dashboard(
        uid=UID_REQUESTS,
        title="Requests — Full Overview",
        description="HTTP request rates, error classes, latency by route, and Loki-backed p95/p99 where Prometheus averages fall short.",
        tags=["trybe", "shop-api", "trybe-requests"],
        builder=b,
        time_from="now-1h",
        rows=[
            ("Request Health Overview", place_row(stats)),
            ("Traffic & Errors Over Time", place_row([
                b.timeseries("Request Rate by Status Class", [(f'sum by (status_class)(rate(app_requests_completed_total{e()}[{ri}]))', "{{status_class}}")], width=12),
                b.timeseries("Error Rate % (4xx / 5xx)", [
                    (f'(100 * sum(rate(app_requests_completed_total{e(status_class="4xx")}[{ri}])) / clamp_min(sum(rate(app_requests_completed_total{e()}[{ri}])),0.0001)) or vector(0)', "4xx"),
                    (f'(100 * sum(rate(app_requests_completed_total{e(status_class="5xx")}[{ri}])) / clamp_min(sum(rate(app_requests_completed_total{e()}[{ri}])),0.0001)) or vector(0)', "5xx"),
                ], unit="percent", width=12),
                b.timeseries("Avg Latency Over Time", [(f'sum(rate(app_requests_duration_total{e()}[{ri}])) / clamp_min(sum(rate(app_requests_completed_total{e()}[{ri}])),0.0001)', "avg ms")], unit="ms", width=24),
            ])),
            ("Route Breakdown", place_row([
                b.table("Top Routes by Traffic", [
                    (f'topk(20, sum by (route)(rate(app_requests_completed_total{e()}[{ri}])))', "req/s"),
                    (f'sum by (route)(rate(app_requests_duration_total{e()}[{ri}])) / clamp_min(sum by (route)(rate(app_requests_completed_total{e()}[{ri}])),0.0001)', "avg ms"),
                    (f'100 * sum by (route)(rate(app_requests_completed_total{e(status_class="5xx")}[{ri}])) / clamp_min(sum by (route)(rate(app_requests_completed_total{e()}[{ri}])),0.0001)', "5xx %"),
                ], height=12),
                b.timeseries("Slowest Routes (avg latency)", [(f'topk(10, sum by (route)(rate(app_requests_duration_total{e()}[{ri}])) / clamp_min(sum by (route)(rate(app_requests_completed_total{e()}[{ri}])),0.0001))', "{{route}}")], unit="ms", width=12, height=8),
                b.timeseries("Busiest Routes (req/s)", [(f'topk(10, sum by (route)(rate(app_requests_completed_total{e()}[{ri}])))', "{{route}}")], unit="reqps", width=12, height=8),
            ])),
            ("Loki Latency Percentiles", place_row([
                b.timeseries("p95 Latency by Route", [(f'topk(10, quantile_over_time(0.95, {loki_unwrap("query.monitor_active")} [5m]) by (route))', "{{route}}")], loki=True, unit="ms", width=12),
                b.timeseries("p99 Latency by Route", [(f'topk(10, quantile_over_time(0.99, {loki_unwrap("query.monitor_active")} [5m]) by (route))', "{{route}}")], loki=True, unit="ms", width=12),
            ])),
            ("Live 5xx Logs", place_row([b.logs("Recent Server Errors", loki_event("request.server_error"), height=14)])),
        ],
    )


def build_queues() -> dict[str, Any]:
    b = DashboardBuilder()
    e = env
    ri = "$__rate_interval"

    stats = [
        b.stat("Queue Size", f"sum(app_queue_size{e()})", unit="short", decimals=0, thresholds=[(0, "green"), (100, "yellow"), (1000, "red")]),
        b.stat("Pending Jobs", f"sum(app_queue_pending_jobs{e()})", unit="short", decimals=0),
        b.stat("Reserved Jobs", f"sum(app_queue_reserved_jobs{e()})", unit="short", decimals=0),
        b.stat("Delayed Jobs", f"sum(app_queue_delayed_jobs{e()})", unit="short", decimals=0),
        b.stat("Oldest Pending Job", f"max(app_queue_oldest_pending_job_age_seconds{e()})", unit="s", decimals=0),
        b.stat("Failed Jobs (range)", f'sum(increase(app_jobs_failed_total{e()}[$__range])) or vector(0)', unit="short", decimals=0),
        b.stat("Dispatched /s", f"sum(rate(app_jobs_dispatched_total{e()}[{ri}]))", unit="ops"),
        b.stat("Processed /s", f"sum(rate(app_jobs_processed_total{e()}[{ri}]))", unit="ops"),
        b.stat("Backlog Drain ETA", f'sum(app_queue_pending_jobs{e()}) / clamp_min(sum(rate(app_jobs_processed_total{e()}[5m])), 0.001)', unit="s", decimals=0, description="Pending jobs divided by current processed rate"),
    ]

    return build_dashboard(
        uid=UID_QUEUES,
        title="Queues & Jobs",
        description="SQS queue depth, job throughput, failures by class/exception, and dispatch-vs-process imbalance.",
        tags=["trybe", "shop-api", "trybe-queues"],
        builder=b,
        time_from="now-6h",
        rows=[
            ("Queue Health Overview", place_row(stats)),
            ("Throughput", place_row([
                b.timeseries("Job Throughput", [
                    (f"sum(rate(app_jobs_dispatched_total{e()}[{ri}]))", "dispatched"),
                    (f"sum(rate(app_jobs_processed_total{e()}[{ri}]))", "processed"),
                    (f"sum(rate(app_jobs_failed_total{e()}[{ri}]))", "failed"),
                ], width=24),
                b.timeseries("Dispatched vs Processed by Queue", [
                    (f"sum by (queue)(rate(app_jobs_dispatched_total{e()}[{ri}]))", "dispatched {{queue}}"),
                    (f"sum by (queue)(rate(app_jobs_processed_total{e()}[{ri}]))", "processed {{queue}}"),
                ], width=12),
                b.timeseries("Dispatch − Process Imbalance by Queue", [
                    (f'sum by (queue)(rate(app_jobs_dispatched_total{e()}[{ri}])) - sum by (queue)(rate(app_jobs_processed_total{e()}[{ri}]))', "{{queue}}"),
                ], width=12, description="Positive = backlog growing"),
            ])),
            ("Queue Depth", place_row([
                b.timeseries("Queue Size by Queue", [(f"sum by (queue)(app_queue_size{e()})", "{{queue}}")], width=12),
                b.timeseries("Pending / Reserved / Delayed", [
                    (f"sum(app_queue_pending_jobs{e()})", "pending"),
                    (f"sum(app_queue_reserved_jobs{e()})", "reserved"),
                    (f"sum(app_queue_delayed_jobs{e()})", "delayed"),
                ], width=12),
                b.timeseries("Oldest Pending Job Age by Queue", [(f"max by (queue)(app_queue_oldest_pending_job_age_seconds{e()})", "{{queue}}")], unit="s", width=24),
            ])),
            ("Failures & Throughput by Class", place_row([
                b.timeseries("Job Failures by Class", [(f"sum by (job_class)(rate(app_jobs_failed_total{e()}[{ri}]))", "{{job_class}}")], width=12),
                b.timeseries("Job Failures by Exception", [(f"sum by (exception_class)(rate(app_jobs_failed_total{e()}[{ri}]))", "{{exception_class}}")], width=12),
                b.table("Top Job Classes by Throughput", [(f"topk(15, sum by (job_class)(rate(app_jobs_processed_total{e()}[{ri}])))", "processed/s")], height=10),
                b.table("Top Job Classes by Failures (range)", [(f"topk(15, sum by (job_class)(increase(app_jobs_failed_total{e()}[$__range])))", "failures")], height=10),
            ])),
            ("Live Job Failure Logs", place_row([b.logs("Recent Job Failures", loki_event("job.failed"), height=14)])),
        ],
    )


def build_commerce() -> dict[str, Any]:
    b = DashboardBuilder()
    e = env

    stats = [
        b.stat("Baskets Created /min", f"sum(rate(app_baskets_created_total{e()}[5m]))*60", unit="cpm", decimals=1),
        b.stat("Baskets Reserved /min", f"sum(rate(app_baskets_reserved_total{e()}[5m]))*60", unit="cpm", decimals=1),
        b.stat("Baskets Submitted /min", f"sum(rate(app_baskets_submitted_total{e()}[5m]))*60", unit="cpm", decimals=1),
        b.stat("Submit Failures (range)", f'sum(increase(app_baskets_submit_failed_total{e()}[$__range])) or vector(0)', unit="short", decimals=0, thresholds=[(0, "green"), (1, "red")]),
        b.stat("Payment Failures (range)", f'sum(increase(app_payments_failed_total{e()}[$__range])) or vector(0)', unit="short", decimals=0, thresholds=[(0, "green"), (1, "red")]),
        b.gauge("Conversion Rate", f'100 * sum(increase(app_baskets_submitted_total{e()}[$__range])) / clamp_min(sum(increase(app_baskets_created_total{e()}[$__range])),1)', width=6, height=4),
    ]
    product = [
        b.stat("Vouchers Purchased (range)", f'sum(increase(app_vouchers_purchased_total{e()}[$__range])) or vector(0)', unit="short", decimals=0),
        b.stat("Memberships Activated (range)", f'sum(increase(app_memberships_activated_total{e()}[$__range])) or vector(0)', unit="short", decimals=0),
        b.stat("Refunds (range)", f'sum(increase(app_refunds_created_total{e()}[$__range])) or vector(0)', unit="short", decimals=0),
    ]

    return build_dashboard(
        uid=UID_COMMERCE,
        title="Commerce Funnel",
        description="Checkout funnel from basket creation through submission, including failure arms and product-line events.",
        tags=["trybe", "shop-api", "trybe-commerce"],
        builder=b,
        rows=[
            ("Checkout Funnel", place_row(stats)),
            ("Funnel Over Time", place_row([
                b.timeseries("Funnel Stages /min", [
                    (f"sum(rate(app_baskets_created_total{e()}[5m]))*60", "created"),
                    (f"sum(rate(app_baskets_reserved_total{e()}[5m]))*60", "reserved"),
                    (f"sum(rate(app_baskets_submitted_total{e()}[5m]))*60", "submitted"),
                ], unit="cpm", width=12),
                b.timeseries("Failure Arms /min", [
                    (f"sum(rate(app_baskets_submit_failed_total{e()}[5m]))*60", "submit failed"),
                    (f"sum(rate(app_payments_failed_total{e()}[5m]))*60", "payment failed"),
                ], unit="cpm", width=12),
            ])),
            ("Product Lines & Revenue Events", place_row([
                *product,
                b.timeseries("Product Events /min", [
                    (f"sum(rate(app_vouchers_purchased_total{e()}[5m]))*60", "vouchers"),
                    (f"sum(rate(app_memberships_activated_total{e()}[5m]))*60", "memberships"),
                    (f"sum(rate(app_refunds_created_total{e()}[5m]))*60", "refunds"),
                ], width=24),
            ])),
        ],
    )


def build_site_health() -> dict[str, Any]:
    b = DashboardBuilder()
    site_filter = '| site_id=~"$site_id"'

    return build_dashboard(
        uid=UID_SITE,
        title="Site Health",
        description="Per-site breakdowns from structured Loki logs — only available when site_id is present in event context.",
        tags=["trybe", "shop-api", "trybe-logs"],
        builder=b,
        variables=[*default_variables(), site_id_variable()],
        rows=[
            ("Basket Activity by Site", place_row([
                b.timeseries("Top Sites — Baskets Created", [(f'topk(15, sum by (site_id) (count_over_time({loki_event("basket.created")}{site_filter} [5m])))', "{{site_id}}")], loki=True, width=12),
                b.timeseries("Top Sites — Baskets Submitted", [(f'topk(15, sum by (site_id) (count_over_time({loki_event("basket.submitted")}{site_filter} [5m])))', "{{site_id}}")], loki=True, width=12),
            ])),
            ("Checkout & Reliability by Site", place_row([
                b.timeseries("Broken Checkout Detector", [
                    (f'topk(15, sum by (site_id) (count_over_time({loki_event("basket.created")}{site_filter} [1h])) - sum by (site_id) (count_over_time({loki_event("basket.submitted")}{site_filter} [1h])))', "{{site_id}}"),
                ], loki=True, description="Positive gap = created but not submitted in the last hour", width=12),
                b.timeseries("Per-site Conversion Proxy /h", [(f'topk(15, sum by (site_id) (count_over_time({loki_event("basket.submitted")}{site_filter} [1h])))', "{{site_id}}")], loki=True, width=12),
            ])),
            ("Errors & Payments by Site", place_row([
                b.timeseries("Per-site 5xx Events", [(f'topk(15, sum by (site_id) (count_over_time({loki_event("request.server_error")}{site_filter} [5m])))', "{{site_id}}")], loki=True, width=12),
                b.timeseries("Per-site Payment Failures", [(f'topk(15, sum by (site_id) (count_over_time({loki_event("payment.failed")}{site_filter} [5m])))', "{{site_id}}")], loki=True, width=12),
                b.timeseries("Per-site Submit Failures", [(f'topk(15, sum by (site_id) (count_over_time({loki_event("basket.submit_failed")}{site_filter} [5m])))', "{{site_id}}")], loki=True, width=24),
            ])),
        ],
    )


def build_forensics() -> dict[str, Any]:
    b = DashboardBuilder()

    return build_dashboard(
        uid=UID_FORENSICS,
        title="Performance Forensics",
        description="Query monitor findings: N+1 patterns, slow queries, latency percentiles, and monitored-vs-unmonitored A/B overhead.",
        tags=["trybe", "shop-api", "trybe-forensics"],
        builder=b,
        time_from="now-1h",
        rows=[
            ("Latency Percentiles (Loki)", place_row([
                b.timeseries("p50 / p95 / p99 — All Routes", [
                    (f'sum(quantile_over_time(0.50, {loki_unwrap("query.monitor_active")} [5m]))', "p50"),
                    (f'sum(quantile_over_time(0.95, {loki_unwrap("query.monitor_active")} [5m]))', "p95"),
                    (f'sum(quantile_over_time(0.99, {loki_unwrap("query.monitor_active")} [5m]))', "p99"),
                ], loki=True, unit="ms", width=24),
                b.timeseries("p95 by Route", [(f'topk(10, quantile_over_time(0.95, {loki_unwrap("query.monitor_active")} [5m]) by (route))', "{{route}}")], loki=True, unit="ms", width=24, height=10),
            ])),
            ("Query Monitor A/B", place_row([
                b.timeseries("Avg Latency — Monitored vs Unmonitored", [
                    (f'sum(avg_over_time({loki_unwrap("query.monitor_active", extra="| active=\"true\"")} [5m]))', "monitored"),
                    (f'sum(avg_over_time({loki_unwrap("query.monitor_active", extra="| active=\"false\"")} [5m]))', "unmonitored"),
                ], loki=True, unit="ms", width=12, description="Confirms sample fraction and overhead of query monitoring"),
                b.timeseries("Request Volume — Monitored vs Unmonitored", [
                    (f'sum(count_over_time({loki_event("query.monitor_active", extra="| active=\"true\"")} [5m]))', "monitored"),
                    (f'sum(count_over_time({loki_event("query.monitor_active", extra="| active=\"false\"")} [5m]))', "unmonitored"),
                ], loki=True, width=12),
            ])),
            ("Query Issues", place_row([
                b.timeseries("N+1 Detections by Route", [(f'topk(10, sum by (route) (count_over_time({loki_event("query.n_plus_one")} [5m])))', "{{route}}")], loki=True, width=12),
                b.timeseries("Slow Requests by Route", [(f'topk(10, sum by (route) (count_over_time({loki_event("request.slow")} [5m])))', "{{route}}")], loki=True, width=12),
                b.table("Top Slow Query Patterns", [(f'topk(15, sum by (query_pattern) (count_over_time({loki_event("query.slow")} [5m])))', "count")], loki=True, height=10),
                b.logs("Recent N+1 Events", loki_event("query.n_plus_one"), height=12),
            ])),
        ],
    )


def build_errors() -> dict[str, Any]:
    b = DashboardBuilder()
    e = env
    ri = "$__rate_interval"

    return build_dashboard(
        uid=UID_ERRORS,
        title="Errors & Exceptions",
        description="5xx rates by route, exception class breakdown, and live error logs with exception messages.",
        tags=["trybe", "shop-api", "trybe-errors"],
        builder=b,
        rows=[
            ("HTTP 5xx", place_row([
                b.stat("5xx Rate", f'(100 * sum(rate(app_requests_completed_total{e(status_class="5xx")}[{ri}])) / clamp_min(sum(rate(app_requests_completed_total{e()}[{ri}])),0.0001)) or vector(0)', unit="percent"),
                b.stat("Server Errors (range)", f'sum(increase(app_requests_server_error_total{e()}[$__range])) or vector(0)', unit="short", decimals=0),
                b.timeseries("5xx Rate by Route", [(f'100 * sum by (route)(rate(app_requests_completed_total{e(status_class="5xx")}[{ri}])) / clamp_min(sum by (route)(rate(app_requests_completed_total{e()}[{ri}])),0.0001)', "{{route}}")], unit="percent", width=24),
                b.timeseries("Server Error Count by Route", [(f'sum by (route)(increase(app_requests_server_error_total{e()}[{ri}]))', "{{route}}")], width=24),
            ])),
            ("Job Failures", place_row([
                b.timeseries("Job Failures by Exception", [(f'sum by (exception_class)(rate(app_jobs_failed_total{e()}[{ri}]))', "{{exception_class}}")], width=12),
                b.timeseries("Job Failures by Class", [(f'sum by (job_class)(rate(app_jobs_failed_total{e()}[{ri}]))', "{{job_class}}")], width=12),
            ])),
            ("Live Error Logs", place_row([
                b.logs("Recent 5xx Server Errors", loki_event("request.server_error"), height=12),
                b.logs("Recent Job Failures", loki_event("job.failed"), height=12),
            ])),
        ],
    )


DASHBOARDS: list[tuple[str, Any]] = [
    ("platform-health-overview.json", build_platform_health),
    ("redis-valkey.json", build_redis),
    ("production-requests-full-overview.json", build_requests),
    ("production-queues-full-overview.json", build_queues),
    ("commerce-funnel.json", build_commerce),
    ("site-health.json", build_site_health),
    ("performance-forensics.json", build_forensics),
    ("errors-exceptions.json", build_errors),
]


def main() -> None:
    for filename, builder_fn in DASHBOARDS:
        path = OUT / filename
        dashboard = builder_fn()
        path.write_text(json.dumps(dashboard, indent=2) + "\n")
        print(f"Wrote {path.name} ({len(dashboard['spec']['elements'])} panels)")


if __name__ == "__main__":
    main()
