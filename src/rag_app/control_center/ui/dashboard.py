"""Modern Textual dashboard for project service orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Label,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from .presentation import (
    as_mapping,
    compact_text,
    human_bytes,
    human_duration,
    normalized_status,
    overall_status,
    percent,
    safe_log_line,
    service_snapshots,
)
from .protocols import ControlCenterFacade

STATUS_STYLE: dict[str, tuple[str, str]] = {
    "running": ("●", "#3DDC97"),
    "starting": ("●", "#F6C85F"),
    "error": ("●", "#FF657A"),
    "stopped": ("○", "#8490A4"),
    "unknown": ("?", "#8490A4"),
}


class MetricCard(Static):
    """Small dashboard card with a label, primary value, and supporting text."""

    def __init__(self, title: str, *, id: str) -> None:
        super().__init__(id=id, classes="metric-card")
        self._title = title
        self.set_metric("—", "Waiting for telemetry")

    def set_metric(self, value: str, detail: str = "") -> None:
        content = Text()
        content.append(self._title.upper(), style="bold #7F8CA3")
        content.append("\n")
        content.append(value, style="bold #F4F7FB")
        if detail:
            content.append("\n")
            content.append(compact_text(detail, 50), style="#8490A4")
        self.update(content)


class SectionTitle(Static):
    """Consistent title used above dashboard panels."""

    def __init__(self, title: str, subtitle: str = "", *, id: str | None = None) -> None:
        text = Text(title.upper(), style="bold #AEB8C8")
        if subtitle:
            text.append(f"  {subtitle}", style="#667085")
        super().__init__(text, id=id, classes="section-title")


class ControlCenterApp(App[None]):
    """Interactive dashboard backed by an async control-center facade."""

    CSS_PATH = "dashboard.tcss"
    TITLE = "Project Control Center"
    SUB_TITLE = "prod-rag"

    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("r", "refresh", "Refresh"),
        Binding("ctrl+s", "start_all", "Start all"),
        Binding("ctrl+x", "stop_all", "Stop all"),
        Binding("ctrl+r", "restart_all", "Restart all"),
        Binding("f", "restart_failed", "Recover failed"),
        Binding("o", "show_overview", "Overview", show=False),
        Binding("l", "show_logs", "Logs", show=False),
        Binding("a", "show_ai", "AI Engine", show=False),
        Binding("d", "show_diagnostics", "Diagnostics", show=False),
        Binding("c", "show_config", "Config", show=False),
    ]

    def __init__(
        self,
        center: ControlCenterFacade,
        *,
        config_path: str | Path | None = None,
        refresh_interval: float = 2.0,
        log_refresh_interval: float = 1.0,
    ) -> None:
        super().__init__()
        self.center = center
        self.config_path = Path(config_path) if config_path is not None else None
        self.refresh_interval = max(0.5, refresh_interval)
        self.log_refresh_interval = max(0.5, log_refresh_interval)
        self._snapshots: list[dict[str, Any]] = []
        self._metrics: dict[str, Any] = {}
        self._ai_metrics: dict[str, Any] = {}
        self._selected_service: str | None = None
        self._log_service: str | None = None
        self._rendered_log_lines: list[str] = []
        self._refresh_running = False
        self._log_refresh_running = False
        self._diagnostics_running = False
        self._operation_running = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="dashboard-shell"):
            with Horizontal(id="project-bar"):
                yield Static(self._brand_text(), id="project-brand")
                yield Static(
                    self._status_text("starting", "Connecting to services"), id="project-status"
                )
            with TabbedContent(initial="overview", id="main-tabs"):
                with TabPane("Overview", id="overview"), VerticalScroll(classes="view-scroll"):
                    yield SectionTitle(
                        "System", "Live resource utilization and orchestration state"
                    )
                    with Grid(classes="metrics-grid"):
                        yield MetricCard("CPU usage", id="metric-cpu")
                        yield MetricCard("RAM usage", id="metric-ram")
                        yield MetricCard("GPU usage", id="metric-gpu")
                        yield MetricCard("Services", id="metric-services")
                    yield SectionTitle("Services", "Select a row to manage a single service")
                    yield DataTable(id="services-table", zebra_stripes=True)
                    yield Label("No service selected", id="selection-label")
                    with HorizontalScroll(id="service-actions", classes="action-row"):
                        yield Button(
                            "Start selected", id="start-service", variant="success", disabled=True
                        )
                        yield Button(
                            "Stop selected", id="stop-service", variant="error", disabled=True
                        )
                        yield Button(
                            "Restart selected",
                            id="restart-service",
                            variant="warning",
                            disabled=True,
                        )
                    yield SectionTitle("Project actions", "Dependency-aware and graceful")
                    with HorizontalScroll(id="global-actions", classes="action-row"):
                        yield Button("Start all services", id="start-all", variant="success")
                        yield Button("Stop all services", id="stop-all", variant="error")
                        yield Button("Restart services", id="restart-all", variant="warning")
                        yield Button("Recover failed", id="restart-failed", variant="primary")
                        yield Button("View logs", id="view-logs", variant="primary")
                        yield Button("Open config", id="open-config")
                        yield Button("System check", id="system-check")
                with TabPane("Live logs", id="logs"), Vertical(classes="panel-view"):
                    yield SectionTitle("Live log view", "ANSI-safe tail of the selected service")
                    with Horizontal(id="log-toolbar"):
                        yield Select([], prompt="Choose service", id="log-service-select")
                        yield Button("Refresh", id="refresh-logs", variant="primary")
                        yield Button("Clear view", id="clear-logs")
                    yield RichLog(
                        id="live-log",
                        highlight=False,
                        markup=False,
                        wrap=False,
                        auto_scroll=True,
                    )
                with TabPane("AI Engine", id="ai"), VerticalScroll(classes="view-scroll"):
                    yield SectionTitle("AI Engine", "LM Studio Desktop and optional API telemetry")
                    yield Static(
                        self._status_text("stopped", "LM Studio status unavailable"),
                        id="ai-status",
                        classes="feature-status",
                    )
                    with Grid(classes="metrics-grid ai-metrics-grid"):
                        yield MetricCard("Model", id="ai-model")
                        yield MetricCard("GPU", id="ai-gpu")
                        yield MetricCard("VRAM", id="ai-vram")
                        yield MetricCard("Throughput", id="ai-throughput")
                    with Grid(classes="metrics-grid ai-secondary-grid"):
                        yield MetricCard("Latency", id="ai-latency")
                        yield MetricCard("Temperature", id="ai-temperature")
                        yield MetricCard("Uptime", id="ai-uptime")
                        yield MetricCard("Endpoint", id="ai-endpoint")
                    yield Static(
                        "Throughput and latency appear when the inference server exposes "
                        "those metrics; unavailable values are never estimated.",
                        classes="hint",
                    )
                with TabPane("Diagnostics", id="diagnostics"), Vertical(classes="panel-view"):
                    yield SectionTitle(
                        "System check", "Runtime, tools, endpoints, GPU, and configuration"
                    )
                    with Horizontal(classes="toolbar"):
                        yield Button("Run system check", id="run-diagnostics", variant="primary")
                        yield Static("Not run yet", id="diagnostic-summary")
                    yield DataTable(id="diagnostics-table", zebra_stripes=True)
                with TabPane("Config", id="config"), VerticalScroll(classes="view-scroll"):
                    yield SectionTitle(
                        "Configuration", "Manifest overview; secrets are intentionally hidden"
                    )
                    yield Static("Loading configuration…", id="config-summary")
                    yield DataTable(id="config-table", zebra_stripes=True)
        yield Footer()

    async def on_mount(self) -> None:
        """Configure tables and start non-blocking polling workers."""

        self._configure_tables()
        self._populate_config_view()
        self._apply_responsive_classes(self.size.width)
        self.set_interval(self.refresh_interval, self.action_refresh)
        self.set_interval(self.log_refresh_interval, self._poll_logs_if_visible)
        self.action_refresh()

    def on_resize(self, event: events.Resize) -> None:
        self._apply_responsive_classes(event.size.width)

    def _apply_responsive_classes(self, width: int) -> None:
        self.screen.set_class(width < 115, "compact")
        self.screen.set_class(width < 75, "narrow")

    def _configure_tables(self) -> None:
        services = self.query_one("#services-table", DataTable)
        services.cursor_type = "row"
        services.add_columns("Service", "Status", "PID", "Uptime", "Driver", "Detail")

        diagnostics = self.query_one("#diagnostics-table", DataTable)
        diagnostics.cursor_type = "row"
        diagnostics.add_columns("Check", "Status", "Severity", "Detail", "Solution")

        config = self.query_one("#config-table", DataTable)
        config.cursor_type = "row"
        config.add_columns("Service", "Driver", "Dependencies", "Enabled", "Description")

    def _populate_config_view(self) -> None:
        config = self.center.config
        services = list(getattr(config, "services", ()) or ())
        options: list[tuple[str, str]] = []
        table = self.query_one("#config-table", DataTable)
        table.clear(columns=False)
        for service in services:
            name = str(getattr(service, "name", "unknown"))
            display_name = str(getattr(service, "display_name", None) or name)
            dependencies_value = getattr(service, "dependencies", ())
            dependencies = ", ".join(str(value) for value in dependencies_value or ()) or "—"
            enabled = bool(getattr(service, "enabled", True))
            table.add_row(
                display_name,
                str(getattr(service, "driver", "—")),
                dependencies,
                "Yes" if enabled else "No",
                compact_text(getattr(service, "description", ""), 70),
                key=name,
            )
            if enabled:
                options.append((display_name, name))

        selector = self.query_one("#log-service-select", Select)
        selector.set_options(options)
        if options:
            selector.value = options[0][1]
            self._log_service = options[0][1]

        project_name = str(getattr(config, "project_name", None) or "prod-rag")
        config_path = (
            self.config_path
            or getattr(config, "source_path", None)
            or getattr(config, "config_path", None)
            or "ops/control-center/settings.yaml"
        )
        self.query_one("#config-summary", Static).update(
            Text.assemble(
                (f"{project_name}\n", "bold #F4F7FB"),
                (f"Manifest: {config_path}\n", "#8490A4"),
                (
                    f"{len(services)} configured service(s). Environment values and secrets "
                    "are not rendered in this view.",
                    "#8490A4",
                ),
            )
        )

    @work(group="dashboard-refresh")
    async def _refresh_dashboard(self) -> None:
        """Fetch service and host state concurrently without blocking input handling."""

        try:
            ai_metrics_method = getattr(self.center, "ai_metrics", None)
            if callable(ai_metrics_method):
                status_result, metrics_result, ai_metrics_result = await asyncio.gather(
                    self.center.status_all(), self.center.system_metrics(), ai_metrics_method()
                )
                self._ai_metrics = as_mapping(ai_metrics_result)
            else:
                status_result, metrics_result = await asyncio.gather(
                    self.center.status_all(), self.center.system_metrics()
                )
                self._ai_metrics = {}
        except Exception as exc:  # facade converts known failures; this is the UI safety boundary
            self._show_connection_error(exc)
        else:
            self._snapshots = service_snapshots(status_result)
            self._metrics = as_mapping(metrics_result)
            self._render_services()
            self._render_metrics()
            self._render_ai_engine()
            self._render_project_status()
        finally:
            self._refresh_running = False

    def action_refresh(self) -> None:
        """Start a refresh only when the previous read-only poll has completed."""

        if self._refresh_running:
            return
        self._refresh_running = True
        self._refresh_dashboard()

    async def action_quit(self) -> None:
        """Do not cancel a partially completed lifecycle mutation on normal quit."""

        if self._operation_running:
            self.notify(
                "A service operation is still running. Wait for it to finish before quitting.",
                severity="warning",
                markup=False,
            )
            return
        self.exit()

    def action_start_all(self) -> None:
        self.run_operation("start_all")

    def action_stop_all(self) -> None:
        self.run_operation("stop_all")

    def action_restart_all(self) -> None:
        self.run_operation("restart_all")

    def action_restart_failed(self) -> None:
        self.run_operation("restart_failed")

    def action_show_overview(self) -> None:
        self._activate_tab("overview")

    def action_show_logs(self) -> None:
        self._activate_tab("logs")
        self.refresh_logs()

    def action_show_ai(self) -> None:
        self._activate_tab("ai")

    def action_show_diagnostics(self) -> None:
        self._activate_tab("diagnostics")
        self.run_diagnostics()

    def action_show_config(self) -> None:
        self._activate_tab("config")

    def _activate_tab(self, tab_id: str) -> None:
        self.query_one("#main-tabs", TabbedContent).active = tab_id

    def on_button_pressed(self, event: Button.Pressed) -> None:
        action_map: dict[str, tuple[str, str | None]] = {
            "start-all": ("start_all", None),
            "stop-all": ("stop_all", None),
            "restart-all": ("restart_all", None),
            "restart-failed": ("restart_failed", None),
            "start-service": ("start", self._selected_service),
            "stop-service": ("stop", self._selected_service),
            "restart-service": ("restart", self._selected_service),
        }
        button_id = event.button.id or ""
        if button_id in action_map:
            action, service_name = action_map[button_id]
            self.run_operation(action, service_name)
            return
        if button_id == "view-logs":
            if self._selected_service:
                self._select_log_service(self._selected_service)
            self.action_show_logs()
        elif button_id == "open-config":
            self.action_show_config()
        elif button_id in {"system-check", "run-diagnostics"}:
            self.action_show_diagnostics()
        elif button_id == "refresh-logs":
            self.refresh_logs()
        elif button_id == "clear-logs":
            self.query_one("#live-log", RichLog).clear()
            self._rendered_log_lines = []

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "services-table" or event.row_key is None:
            return
        self._set_selected_service(str(event.row_key.value))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "services-table" or event.row_key is None:
            return
        service_name = str(event.row_key.value)
        self._set_selected_service(service_name)
        self._select_log_service(service_name)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "log-service-select" or event.select.is_blank():
            return
        next_service = str(event.value)
        if next_service != self._log_service:
            self._log_service = next_service
            self._rendered_log_lines = []
            self.query_one("#live-log", RichLog).clear()
        self.refresh_logs()

    def _set_selected_service(self, service_name: str) -> None:
        self._selected_service = service_name
        snapshot = next(
            (item for item in self._snapshots if str(item.get("name")) == service_name), None
        )
        display_name = str((snapshot or {}).get("display_name") or service_name)
        self.query_one("#selection-label", Label).update(f"Selected: {display_name}")
        for button_id in ("#start-service", "#stop-service", "#restart-service"):
            self.query_one(button_id, Button).disabled = self._operation_running

    def _select_log_service(self, service_name: str) -> None:
        selector = self.query_one("#log-service-select", Select)
        try:
            selector.value = service_name
        except Exception:
            return
        self._log_service = service_name

    def run_operation(self, action: str, service_name: str | None = None) -> None:
        """Reserve and dispatch one non-cancelling lifecycle operation."""

        if service_name is None and action in {"start", "stop", "restart"}:
            self.notify("Select a service first", severity="warning", markup=False)
            return
        if self._operation_running:
            self.notify(
                "A service operation is already running; the duplicate request was ignored.",
                severity="warning",
                markup=False,
            )
            return
        # Reserve synchronously. No await may occur before this flag is set, otherwise two
        # hotkeys handled in the same event-loop turn could both mutate service state.
        self._set_operation_state(True, action)
        self._execute_operation(action, service_name)

    @work(group="service-operation")
    async def _execute_operation(self, action: str, service_name: str | None = None) -> None:
        """Execute the lifecycle mutation without exclusive-worker cancellation."""

        target = service_name or "all services"
        try:
            method = getattr(self.center, action)
            result = await method(service_name) if service_name is not None else await method()
            result_data = as_mapping(result)
            success = bool(result_data.get("success", True))
            errors = result_data.get("errors") or ()
            if success:
                self.notify(
                    f"{action.replace('_', ' ').title()}: {target}",
                    severity="information",
                    markup=False,
                )
            else:
                detail = compact_text("; ".join(str(error) for error in errors), 140)
                self.notify(
                    detail or f"Operation failed for {target}",
                    severity="error",
                    timeout=8,
                    markup=False,
                )
        except Exception as exc:
            self.notify(
                f"{action.replace('_', ' ').title()} failed: {compact_text(exc, 120)}",
                severity="error",
                timeout=8,
                markup=False,
            )
        finally:
            self._set_operation_state(False, action)
            self.action_refresh()

    def _set_operation_state(self, running: bool, action: str) -> None:
        self._operation_running = running
        for button in self.query(".action-row Button"):
            button.disabled = running
        if not running and self._selected_service is None:
            for button_id in ("#start-service", "#stop-service", "#restart-service"):
                self.query_one(button_id, Button).disabled = True
        if running:
            label = action.replace("_", " ").title()
            self.query_one("#project-status", Static).update(
                self._status_text("starting", f"{label} in progress")
            )

    def run_diagnostics(self) -> None:
        """Dispatch one diagnostics pass without cancelling an active probe."""

        if self._diagnostics_running:
            return
        self._diagnostics_running = True
        summary = self.query_one("#diagnostic-summary", Static)
        button = self.query_one("#run-diagnostics", Button)
        button.disabled = True
        summary.update(Text("● Checking environment…", style="#F6C85F"))
        self._execute_diagnostics()

    @work(group="diagnostics")
    async def _execute_diagnostics(self) -> None:
        summary = self.query_one("#diagnostic-summary", Static)
        button = self.query_one("#run-diagnostics", Button)
        try:
            result = await self.center.system_check()
            checks = result if isinstance(result, Sequence) and not isinstance(result, str) else []
            table = self.query_one("#diagnostics-table", DataTable)
            table.clear(columns=False)
            required_failures = 0
            optional_warnings = 0
            for check in checks:
                item = as_mapping(check)
                ok = bool(item.get("ok"))
                severity = str(item.get("severity") or "required").lower()
                if ok:
                    outcome = Text("✓ Passed", style="#3DDC97")
                elif severity == "optional":
                    optional_warnings += 1
                    outcome = Text("⚠ Warning", style="#F6C85F")
                else:
                    required_failures += 1
                    outcome = Text("✕ Failed", style="#FF657A")
                table.add_row(
                    str(item.get("name") or "Unknown check"),
                    outcome,
                    severity.upper(),
                    compact_text(item.get("detail"), 90),
                    compact_text(item.get("solution"), 90) or "—",
                )
            if required_failures:
                failure_label = "check" if required_failures == 1 else "checks"
                warning_label = "warning" if optional_warnings == 1 else "warnings"
                warning_suffix = (
                    f"; {optional_warnings} optional {warning_label}" if optional_warnings else ""
                )
                summary.update(
                    Text(
                        f"● {required_failures} required {failure_label} failed{warning_suffix}",
                        style="#FF657A",
                    )
                )
            elif optional_warnings:
                warning_label = "warning" if optional_warnings == 1 else "warnings"
                summary.update(
                    Text(
                        f"● All required checks passed; {optional_warnings} optional {warning_label}",
                        style="#F6C85F",
                    )
                )
            else:
                summary.update(Text(f"● All {len(checks)} checks passed", style="#3DDC97"))
        except Exception as exc:
            summary.update(Text("● Diagnostics failed", style="#FF657A"))
            self.notify(
                f"System check failed: {compact_text(exc, 120)}",
                severity="error",
                markup=False,
            )
        finally:
            self._diagnostics_running = False
            button.disabled = False

    def _poll_logs_if_visible(self) -> None:
        tabs = self.query_one("#main-tabs", TabbedContent)
        if tabs.active == "logs" and self._log_service:
            self.refresh_logs()

    def refresh_logs(self) -> None:
        """Start one log poll and coalesce timer/select refresh requests."""

        service_name = self._log_service
        if not service_name or self._log_refresh_running:
            return
        self._log_refresh_running = True
        self._refresh_logs(service_name)

    @work(group="log-refresh")
    async def _refresh_logs(self, service_name: str) -> None:
        try:
            result = await self.center.tail_logs(service_name, lines=300)
            if isinstance(result, str):
                lines = result.splitlines()
            elif isinstance(result, Sequence):
                lines = [str(line).rstrip("\r\n") for line in result]
            else:
                lines = []
            if service_name == self._log_service:
                self._render_log_lines(lines)
        except Exception as exc:
            if service_name == self._log_service:
                log = self.query_one("#live-log", RichLog)
                detail = safe_log_line(compact_text(exc, 150))
                log.write(Text(f"Unable to read logs: {detail}", style="#FF657A"))
        finally:
            self._log_refresh_running = False
            if self._log_service and service_name != self._log_service:
                self.refresh_logs()

    def _render_log_lines(self, lines: list[str]) -> None:
        log = self.query_one("#live-log", RichLog)
        old = self._rendered_log_lines
        if len(lines) >= len(old) and lines[: len(old)] == old:
            additions = lines[len(old) :]
        else:
            overlap = min(len(old), len(lines))
            while overlap and old[-overlap:] != lines[:overlap]:
                overlap -= 1
            if overlap:
                additions = lines[overlap:]
            else:
                log.clear()
                additions = lines
        for line in additions:
            log.write(Text(safe_log_line(line)))
        self._rendered_log_lines = list(lines)

    def _render_services(self) -> None:
        table = self.query_one("#services-table", DataTable)
        selected = self._selected_service
        table.clear(columns=False)
        for snapshot in self._snapshots:
            name = str(snapshot.get("name") or "unknown")
            status = normalized_status(snapshot.get("status"))
            table.add_row(
                str(snapshot.get("display_name") or name),
                self._status_text(status, status.title()),
                str(snapshot.get("pid") or "—"),
                human_duration(snapshot.get("uptime_seconds")),
                str(snapshot.get("driver") or "—"),
                compact_text(snapshot.get("detail"), 70) or "—",
                key=name,
            )
        if selected and any(str(item.get("name")) == selected for item in self._snapshots):
            self._set_selected_service(selected)

    def _render_metrics(self) -> None:
        metrics = self._metrics
        cpu = metrics.get("cpu_percent")
        used = metrics.get("memory_used_bytes")
        total = metrics.get("memory_total_bytes")
        self.query_one("#metric-cpu", MetricCard).set_metric(percent(cpu), "Host utilization")
        self.query_one("#metric-ram", MetricCard).set_metric(
            percent(metrics.get("memory_percent")), f"{human_bytes(used)} / {human_bytes(total)}"
        )

        gpus = metrics.get("gpu") or []
        gpu = as_mapping(gpus[0]) if gpus else {}
        if gpu:
            self.query_one("#metric-gpu", MetricCard).set_metric(
                percent(gpu.get("utilization_percent")), str(gpu.get("name") or "GPU 0")
            )
        else:
            self.query_one("#metric-gpu", MetricCard).set_metric("N/A", "No GPU telemetry")

        running = sum(
            normalized_status(item.get("status")) == "running" for item in self._snapshots
        )
        errors = sum(normalized_status(item.get("status")) == "error" for item in self._snapshots)
        detail = f"{errors} error(s)" if errors else "No service errors"
        self.query_one("#metric-services", MetricCard).set_metric(
            f"{running}/{len(self._snapshots)}", detail
        )

    def _render_project_status(self) -> None:
        status, detail = overall_status(self._snapshots)
        self.query_one("#project-status", Static).update(self._status_text(status, detail))

    def _render_ai_engine(self) -> None:
        lm_studio = next(
            (
                snapshot
                for snapshot in self._snapshots
                if str(snapshot.get("name", "")).lower() in {"lm_studio", "ai", "ai_model"}
            ),
            {},
        )
        metadata = as_mapping(lm_studio.get("metadata"))
        health = as_mapping(lm_studio.get("health"))
        combined = {**health, **metadata, **self._ai_metrics}
        status = normalized_status(lm_studio.get("status"))
        if combined.get("loaded") is True:
            status = "running"
        elif combined.get("loaded") is False and status == "unknown":
            status = "stopped"
        detail = (
            compact_text(combined.get("detail"), 100)
            or compact_text(lm_studio.get("detail"), 100)
            or "LM Studio service"
        )
        self.query_one("#ai-status", Static).update(self._status_text(status, detail))

        model = combined.get("model") or combined.get("model_id") or "N/A"
        endpoint = combined.get("endpoint") or combined.get("base_url")
        if endpoint is None:
            lm_config = next(
                (
                    service
                    for service in getattr(self.center.config, "services", ())
                    if str(getattr(service, "name", "")).lower() == "lm_studio"
                ),
                None,
            )
            host = getattr(lm_config, "host", "127.0.0.1")
            port = getattr(lm_config, "port", 1234)
            endpoint = f"{host}:{port}"
        self.query_one("#ai-model", MetricCard).set_metric(str(model), status.title())

        ai_gpu_value = combined.get("gpu")
        if isinstance(ai_gpu_value, Sequence) and not isinstance(ai_gpu_value, (str, bytes)):
            ai_gpu = as_mapping(ai_gpu_value[0]) if ai_gpu_value else {}
        else:
            ai_gpu = as_mapping(ai_gpu_value)
        gpus = self._metrics.get("gpu") or []
        system_gpu = as_mapping(gpus[0]) if gpus else {}
        gpu = {**system_gpu, **ai_gpu}
        gpu_name = combined.get("gpu_name") or gpu.get("name") or "N/A"
        gpu_utilization = combined.get("gpu_utilization_percent")
        if gpu_utilization is None:
            gpu_utilization = gpu.get("utilization_percent")
        self.query_one("#ai-gpu", MetricCard).set_metric(
            str(gpu_name),
            f"{percent(gpu_utilization)} utilization" if gpu else "Not detected",
        )
        vram_used = combined.get("vram_used_bytes")
        if vram_used is None:
            vram_used = gpu.get("memory_used_bytes")
        vram_total = combined.get("vram_total_bytes")
        if vram_total is None:
            vram_total = gpu.get("memory_total_bytes")
        self.query_one("#ai-vram", MetricCard).set_metric(
            human_bytes(vram_used) if vram_used is not None else "N/A",
            f"of {human_bytes(vram_total)}" if vram_total is not None else "No telemetry",
        )

        throughput = combined.get("requests_per_minute", combined.get("requests_per_min"))
        throughput_text = (
            f"{float(throughput):.0f}/min" if isinstance(throughput, int | float) else "N/A"
        )
        latency = combined.get("latency_ms")
        if latency is None:
            latency = combined.get("latency_last_ms", combined.get("latency_average_ms"))
        latency_text = f"{float(latency):.0f} ms" if isinstance(latency, int | float) else "N/A"
        self.query_one("#ai-throughput", MetricCard).set_metric(
            throughput_text, "Inference requests"
        )
        self.query_one("#ai-latency", MetricCard).set_metric(latency_text, "Observed latency")

        temperature = gpu.get("temperature_c")
        temperature_text = (
            f"{float(temperature):.0f} °C" if isinstance(temperature, int | float) else "N/A"
        )
        self.query_one("#ai-temperature", MetricCard).set_metric(temperature_text, "GPU sensor")
        uptime = lm_studio.get("uptime_seconds")
        self.query_one("#ai-uptime", MetricCard).set_metric(
            human_duration(uptime) if uptime is not None else "N/A", "Inference server"
        )
        self.query_one("#ai-endpoint", MetricCard).set_metric(str(endpoint), "OpenAI compatible")

    def _show_connection_error(self, exc: Exception) -> None:
        self.query_one("#project-status", Static).update(
            self._status_text("error", f"Refresh failed: {compact_text(exc, 90)}")
        )

    @staticmethod
    def _brand_text() -> Text:
        return Text.assemble(
            ("PROJECT CONTROL CENTER", "bold #F4F7FB"),
            ("  /  PROD-RAG", "bold #77839A"),
        )

    @staticmethod
    def _status_text(status: str, label: str) -> Text:
        icon, color = STATUS_STYLE.get(normalized_status(status), STATUS_STYLE["unknown"])
        return Text.assemble((f"{icon} ", f"bold {color}"), (label, color))
