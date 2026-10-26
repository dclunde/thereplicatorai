from src.web.backend.routes.printer import router as printer_router
from src.web.backend.routes.sessions import router as sessions_router
from src.web.backend.routes.supervisor import router as supervisor_router

__all__ = ["printer_router", "sessions_router", "supervisor_router"]
