"""CLI Management Interface for Text-to-Printer Multi-Service Supervisor.

Usage:
    uv run python -m src.cli.supervisor                 # Run supervisor daemon
    uv run python -m src.cli.supervisor --status        # Show status of services
    uv run python -m src.cli.supervisor --restarts      # Show recent restart/crash events
    uv run python -m src.cli.supervisor --logs backend  # View service log tail
    uv run python -m src.cli.supervisor --reset-breaker telegram_bot
    uv run python -m src.cli.supervisor --only telegram_bot
"""

import argparse
import json
import sys

from src.core.auto_updater import AutoUpdater
from src.core.config_loader import load_services_config
from src.core.supervisor import ProcessSupervisor
from src.core.supervisor_logging import (
    read_lifecycle_events,
    read_service_log_tail,
)


def format_bytes(n_bytes: int) -> str:
    """Format bytes into MB or KB string."""
    if n_bytes > 1024 * 1024:
        return f"{n_bytes / (1024 * 1024):.1f} MB"
    if n_bytes > 1024:
        return f"{n_bytes / 1024:.1f} KB"
    return f"{n_bytes} B"


def show_status(config: dict) -> None:
    """Display tabular status of configured services."""
    sup = ProcessSupervisor(config=config)
    status_data = sup.get_status()

    print("=" * 76)
    print("               TEXT-TO-PRINTER SUPERVISOR STATUS")
    print("=" * 76)
    print(f"{'SERVICE':<18} {'STATUS':<12} {'PID':<8} {'UPTIME':<10} {'CIRCUIT BREAKER'}")
    print("-" * 76)

    for name, info in status_data.items():
        pid_str = str(info["pid"]) if info["pid"] else "-"
        uptime_str = f"{info['uptime_seconds']}s" if info["uptime_seconds"] > 0 else "-"
        cb = info["circuit_breaker"]
        cb_str = (
            "TRIPPED (PAUSED)"
            if cb["tripped"]
            else f"OK ({cb['restart_count_window']} crashes in window)"
        )
        print(f"{name:<18} {info['status']:<12} {pid_str:<8} {uptime_str:<10} {cb_str}")
    print("=" * 76)


def show_restarts(limit: int = 25) -> None:
    """Display recent crash, restart, and update events from the lifecycle journal."""
    events = read_lifecycle_events(limit=limit)

    print("=" * 86)
    print("                 RECENT LIFECYCLE & RESTART EVENTS")
    print("=" * 86)
    print(f"{'LOCAL TIME':<20} {'EVENT TYPE':<22} {'SERVICE':<16} {'DETAILS'}")
    print("-" * 86)

    if not events:
        print("  No lifecycle events recorded yet.")
    else:
        for ev in events:
            time_str = ev.get("local_time", "")
            ev_type = ev.get("event_type", "")
            svc = ev.get("service") or "-"
            reason = ev.get("reason", "")
            print(f"{time_str:<20} {ev_type:<22} {svc:<16} {reason[:26]}")
    print("=" * 86)


def show_logs(service_name: str, lines: int = 50) -> None:
    """Display tail of a specific service log."""
    tail = read_service_log_tail(service_name, max_lines=lines)
    print(f"--- Log tail for {service_name} (last {len(tail)} lines) ---")
    for line in tail:
        print(line)


def main() -> int:
    parser = argparse.ArgumentParser(description="Text-to-Printer Multi-Service Supervisor & CLI")
    parser.add_argument("--status", action="store_true", help="Print status of all services")
    parser.add_argument(
        "--restarts", action="store_true", help="Show recent restart and crash events"
    )
    parser.add_argument(
        "--logs", metavar="SERVICE", type=str, help="Print log tail for specified service"
    )
    parser.add_argument(
        "--lines", type=int, default=50, help="Number of log lines to show (default: 50)"
    )
    parser.add_argument(
        "--reset-breaker", metavar="SERVICE", type=str, help="Reset circuit breaker for service"
    )
    parser.add_argument(
        "--only", metavar="SERVICE", type=str, help="Run only the specified service"
    )
    parser.add_argument(
        "--services",
        metavar="CSV",
        type=str,
        help="Comma-separated list of services to enable (e.g. backend,telegram)",
    )
    parser.add_argument(
        "--skip-updater", action="store_true", help="Disable background auto-updater"
    )
    parser.add_argument(
        "--update-now", action="store_true", help="Force immediate git update check and pull"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output status/events in machine-readable JSON"
    )

    args = parser.parse_args()
    config = load_services_config()

    if args.status:
        if args.json:
            sup = ProcessSupervisor(config=config)
            print(json.dumps(sup.get_status(), indent=2))
        else:
            show_status(config)
        return 0

    if args.restarts:
        if args.json:
            events = read_lifecycle_events(limit=args.lines)
            print(json.dumps(events, indent=2))
        else:
            show_restarts(limit=args.lines)
        return 0

    if args.logs:
        show_logs(args.logs, lines=args.lines)
        return 0

    if args.reset_breaker:
        sup = ProcessSupervisor(config=config)
        sup.circuit_breaker.reset(args.reset_breaker)
        print(f"Circuit breaker reset for '{args.reset_breaker}'.")
        return 0

    if args.update_now:
        updater = AutoUpdater(config=config)
        print("Checking for updates and applying immediately...")
        success, msg = updater.apply_update(force=True)
        print(f"Result: {msg}")
        return 0 if success else 1

    # Configure service filters
    if args.only:
        target = args.only.strip()
        for k in config.get("services", {}):
            config["services"][k] = (k == target) or (k.startswith(target))

    if args.services:
        selected = {s.strip() for s in args.services.split(",")}
        for k in config.get("services", {}):
            config["services"][k] = any(s in k for s in selected)

    if args.skip_updater:
        config.setdefault("auto_updater", {})["enabled"] = False

    # Run supervisor daemon
    supervisor = ProcessSupervisor(config=config)
    updater = None
    if config.get("auto_updater", {}).get("enabled", True):
        updater = AutoUpdater(config=config, supervisor=supervisor)
        updater.start_background_loop()

    try:
        supervisor.run_forever()
    finally:
        if updater:
            updater.stop_background_loop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
