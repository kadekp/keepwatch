"""FastAPI app for detector health, events, and media playback."""

from __future__ import annotations

import mimetypes
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import AppConfig
from .service import DetectorSupervisor

mimetypes.add_type("application/vnd.apple.mpegurl", ".m3u8")
mimetypes.add_type("video/mp2t", ".ts")


def create_app(config: AppConfig) -> FastAPI:
    supervisor = DetectorSupervisor(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        supervisor.start()
        try:
            yield
        finally:
            supervisor.stop()

    app = FastAPI(title="CCTV Detector API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=config.api.cors_origin_regex,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    app.mount(
        "/media",
        StaticFiles(directory=str(config.storage.media_root)),
        name="media",
    )

    @app.get("/health")
    def health() -> dict[str, object]:
        return supervisor.health()

    @app.get("/api/cameras")
    def cameras() -> dict[str, object]:
        items = [
            {
                "name": camera.name,
                "display_name": camera.display_name,
                "crop": camera.crop.as_dict(),
            }
            for camera in config.cameras
        ]
        return {"items": items, "count": len(items)}

    @app.get("/api/storage")
    def storage() -> dict[str, object]:
        return supervisor.storage_summary()

    @app.get("/api/events")
    def events(
        limit: int = Query(default=20, ge=1, le=100),
        camera: str | None = Query(default=None),
        kind: str = Query(default="all", pattern="^(all|person|motion)$"),
    ) -> dict[str, object]:
        rows = supervisor.list_events(limit=limit, camera_name=camera, kind=kind)
        return {
            "items": [_serialize_event(supervisor, event) for event in rows],
            "count": len(rows),
        }

    return app


def _serialize_event(supervisor: DetectorSupervisor, event) -> dict[str, object]:
    thumbnail_path = event.snapshot_path or event.thumbnail_path
    metadata = event.metadata or {}
    return {
        "id": event.id,
        "camera_name": event.camera_name,
        "camera_display_name": supervisor.camera_display_name(event.camera_name),
        "started_at": event.started_at.isoformat(),
        "ended_at": event.ended_at.isoformat() if event.ended_at else None,
        "duration_seconds": event.duration_seconds,
        "has_person": event.has_person,
        "kind": "person" if event.has_person else "motion",
        "best_confidence": event.best_confidence,
        "size_bytes": event.size_bytes,
        "size_mb": round(event.size_bytes / (1024**2), 2),
        "clip_url": supervisor.storage.media_url(event.clip_path),
        "thumbnail_url": supervisor.storage.media_url(thumbnail_path),
        "snapshot_url": supervisor.storage.media_url(event.snapshot_path),
        "metadata": metadata,
        "crop": metadata.get("crop"),
    }
