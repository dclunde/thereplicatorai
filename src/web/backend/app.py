from contextlib import asynccontextmanager
import os
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.core.config import ROOT_DIR, SESSIONS_DIR, SNAPSHOTS_DIR
from src.web.backend.routes.printer import get_bambu_cloud, router as printer_router
from src.web.backend.routes.sessions import router as sessions_router
from src.web.backend.routes.supervisor import router as supervisor_router

# Ensure runtime directories exist
os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(SNAPSHOTS_DIR, exist_ok=True)

# Global client reference for backwards compatibility and easy inspection
bambu_cloud = get_bambu_cloud()


def is_testing() -> bool:
    return "pytest" in sys.modules or bool(os.environ.get("PYTEST_CURRENT_TEST"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Connect MQTT on startup if not running under pytest
    if not is_testing():
        try:
            bambu_cloud.connect_mqtt()
        except Exception as e:
            print(f"Warning: Failed to connect MQTT on startup: {e}")
    yield
    # Disconnect MQTT on shutdown if connected
    try:
        if bambu_cloud.is_connected():
            bambu_cloud.disconnect_mqtt()
    except Exception:
        pass


app = FastAPI(title="Text-to-Printer Web Backend", lifespan=lifespan)

# Allow CORS for the React frontend on localhost only
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Mount the workspace directory to serve STLs and PNGs statically
app.mount("/outputs/sessions", StaticFiles(directory=SESSIONS_DIR), name="outputs_sessions")
app.mount("/created_stls", StaticFiles(directory=SESSIONS_DIR), name="workspace")

# Include modular route collections
app.include_router(sessions_router)
app.include_router(printer_router)
app.include_router(supervisor_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}


# Mount static production frontend distribution if built
frontend_dist_dir = os.path.join(ROOT_DIR, "src", "web", "frontend", "dist")
if os.path.exists(frontend_dist_dir) and os.path.isdir(frontend_dist_dir):
    app.mount("/", StaticFiles(directory=frontend_dist_dir, html=True), name="frontend_dist")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.web.backend.app:app", host="127.0.0.1", port=8000, reload=True)
