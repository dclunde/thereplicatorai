"""FastAPI routes for Multi-Service Supervisor and Observability."""

from fastapi import APIRouter, HTTPException, Query

from src.core.auto_updater import AutoUpdater
from src.core.config_loader import load_services_config
from src.core.supervisor import ProcessSupervisor, get_global_supervisor
from src.core.supervisor_logging import (
    read_lifecycle_events,
    read_service_log_tail,
)

router = APIRouter(prefix="/api/supervisor", tags=["supervisor"])


@router.get("/status")
def get_status():
    """Return real-time diagnostic status of all managed services and circuit breakers."""
    supervisor = get_global_supervisor()
    if supervisor:
        return supervisor.get_status()

    # Standalone mode: inspect config and default state
    cfg = load_services_config()
    sup = ProcessSupervisor(config=cfg)
    return sup.get_status()


@router.get("/restarts")
def get_restarts(
    service: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
):
    """Return chronological lifecycle and crash events from JSONL journal."""
    events = read_lifecycle_events(service=service, limit=limit)
    return {"events": events, "count": len(events)}


@router.get("/logs/{service}")
def get_service_logs(
    service: str,
    lines: int = Query(default=100, ge=1, le=1000),
):
    """Return trailing log lines for a specific service."""
    tail = read_service_log_tail(service_name=service, max_lines=lines)
    return {"service": service, "lines": tail, "count": len(tail)}


@router.post("/restart/{service}")
def restart_service_endpoint(service: str):
    """Request a graceful restart of a specific service."""
    supervisor = get_global_supervisor()
    if not supervisor:
        raise HTTPException(
            status_code=503,
            detail="Supervisor is not running in active master mode.",
        )

    if service not in supervisor.services:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{service}' not recognized.",
        )

    success = supervisor.restart_service(service, timeout=10.0)
    return {"service": service, "restarted": success}


@router.post("/reset_breaker/{service}")
def reset_breaker_endpoint(service: str):
    """Reset the circuit breaker for a tripped service."""
    supervisor = get_global_supervisor()
    if supervisor:
        supervisor.circuit_breaker.reset(service)
    return {"service": service, "reset": True}


@router.post("/update_now")
def trigger_update_now():
    """Trigger an immediate git update check and pull."""
    supervisor = get_global_supervisor()
    cfg = load_services_config()
    updater = AutoUpdater(config=cfg, supervisor=supervisor)
    success, msg = updater.apply_update(force=True)
    return {"success": success, "message": msg}
