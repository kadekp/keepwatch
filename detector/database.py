"""SQLite storage for motion events and person detections."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def _dt_to_text(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _text_to_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass
class DetectionRecord:
    id: int | None
    event_id: int | None
    timestamp: datetime
    camera_name: str
    confidence: float
    bbox_x1: int
    bbox_y1: int
    bbox_x2: int
    bbox_y2: int
    image_path: str
    metadata: dict | None = None


@dataclass
class EventRecord:
    id: int
    camera_name: str
    started_at: datetime
    ended_at: datetime | None
    status: str
    has_person: bool
    best_confidence: float | None
    clip_path: str | None
    thumbnail_path: str | None
    snapshot_path: str | None
    size_bytes: int
    metadata: dict | None = None

    @property
    def duration_seconds(self) -> float | None:
        if not self.ended_at:
            return None
        return max((self.ended_at - self.started_at).total_seconds(), 0.0)


class DetectionDatabase:
    """SQLite database for detections and recorded motion events."""

    DETECTIONS_SCHEMA = """
    CREATE TABLE IF NOT EXISTS detections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id INTEGER,
        timestamp TEXT NOT NULL,
        camera_name TEXT NOT NULL,
        confidence REAL NOT NULL,
        bbox_x1 INTEGER NOT NULL,
        bbox_y1 INTEGER NOT NULL,
        bbox_x2 INTEGER NOT NULL,
        bbox_y2 INTEGER NOT NULL,
        image_path TEXT NOT NULL,
        metadata TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_detections_timestamp ON detections(timestamp);
    CREATE INDEX IF NOT EXISTS idx_detections_camera ON detections(camera_name);
    CREATE INDEX IF NOT EXISTS idx_detections_confidence ON detections(confidence);
    CREATE INDEX IF NOT EXISTS idx_detections_event_id ON detections(event_id);
    """

    EVENTS_SCHEMA = """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        camera_name TEXT NOT NULL,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        has_person INTEGER NOT NULL DEFAULT 0,
        best_confidence REAL,
        clip_path TEXT,
        thumbnail_path TEXT,
        snapshot_path TEXT,
        size_bytes INTEGER NOT NULL DEFAULT 0,
        metadata TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);
    CREATE INDEX IF NOT EXISTS idx_events_started_at ON events(started_at);
    CREATE INDEX IF NOT EXISTS idx_events_camera ON events(camera_name);
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(self.DETECTIONS_SCHEMA)
            conn.executescript(self.EVENTS_SCHEMA)
            self._ensure_detections_event_id(conn)
            self._ensure_events_metadata(conn)

    def _ensure_detections_event_id(self, conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(detections)").fetchall()}
        if "event_id" not in columns:
            conn.execute("ALTER TABLE detections ADD COLUMN event_id INTEGER")

    def _ensure_events_metadata(self, conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
        if "metadata" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN metadata TEXT")

    def create_event(
        self,
        camera_name: str,
        started_at: datetime,
        thumbnail_path: str,
        metadata: dict | None = None,
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO events
                (camera_name, started_at, thumbnail_path, metadata, status, updated_at)
                VALUES (?, ?, ?, ?, 'active', CURRENT_TIMESTAMP)
                """,
                (
                    camera_name,
                    _dt_to_text(started_at),
                    thumbnail_path,
                    json.dumps(metadata) if metadata else None,
                ),
            )
            return int(cursor.lastrowid)

    def mark_event_detection(
        self, event_id: int, confidence: float, snapshot_path: str | None
    ) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT best_confidence, snapshot_path FROM events WHERE id = ?",
                (event_id,),
            ).fetchone()
            if not row:
                return

            best_confidence = row["best_confidence"]
            update_snapshot = snapshot_path and (
                best_confidence is None or confidence >= float(best_confidence)
            )
            conn.execute(
                """
                UPDATE events
                SET has_person = 1,
                    best_confidence = CASE
                        WHEN best_confidence IS NULL OR ? > best_confidence THEN ?
                        ELSE best_confidence
                    END,
                    snapshot_path = CASE
                        WHEN ? THEN ?
                        ELSE snapshot_path
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (confidence, confidence, 1 if update_snapshot else 0, snapshot_path, event_id),
            )

    def finalize_event(
        self,
        event_id: int,
        ended_at: datetime,
        clip_path: str | None,
        thumbnail_path: str | None,
        snapshot_path: str | None,
        size_bytes: int,
        status: str = "finalized",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE events
                SET ended_at = ?,
                    clip_path = ?,
                    thumbnail_path = COALESCE(?, thumbnail_path),
                    snapshot_path = COALESCE(?, snapshot_path),
                    size_bytes = ?,
                    status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    _dt_to_text(ended_at),
                    clip_path,
                    thumbnail_path,
                    snapshot_path,
                    size_bytes,
                    status,
                    event_id,
                ),
            )

    def fail_event(self, event_id: int, ended_at: datetime) -> None:
        self.finalize_event(
            event_id=event_id,
            ended_at=ended_at,
            clip_path=None,
            thumbnail_path=None,
            snapshot_path=None,
            size_bytes=0,
            status="failed",
        )

    def insert_detection(self, record: DetectionRecord) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO detections
                (event_id, timestamp, camera_name, confidence, bbox_x1, bbox_y1,
                 bbox_x2, bbox_y2, image_path, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.event_id,
                    _dt_to_text(record.timestamp),
                    record.camera_name,
                    record.confidence,
                    record.bbox_x1,
                    record.bbox_y1,
                    record.bbox_x2,
                    record.bbox_y2,
                    record.image_path,
                    json.dumps(record.metadata) if record.metadata else None,
                ),
            )
            return int(cursor.lastrowid)

    def list_events(
        self,
        limit: int = 20,
        camera_name: str | None = None,
        kind: str = "all",
    ) -> list[EventRecord]:
        query = ["SELECT * FROM events WHERE status = 'finalized' AND clip_path IS NOT NULL"]
        params: list[object] = []

        if camera_name:
            query.append("AND camera_name = ?")
            params.append(camera_name)
        if kind == "person":
            query.append("AND has_person = 1")
        elif kind == "motion":
            query.append("AND has_person = 0")

        query.append("ORDER BY started_at DESC LIMIT ?")
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(" ".join(query), params).fetchall()
        return [self._row_to_event(row) for row in rows]

    def get_event(self, event_id: int) -> EventRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return self._row_to_event(row) if row else None

    def get_event_file_paths(self, event_id: int) -> set[str]:
        with self._connect() as conn:
            event_row = conn.execute(
                "SELECT clip_path, thumbnail_path, snapshot_path FROM events WHERE id = ?",
                (event_id,),
            ).fetchone()
            detection_rows = conn.execute(
                "SELECT image_path FROM detections WHERE event_id = ?",
                (event_id,),
            ).fetchall()

        file_paths = set()
        if event_row:
            for key in ("clip_path", "thumbnail_path", "snapshot_path"):
                value = event_row[key]
                if value:
                    file_paths.add(value)
        for row in detection_rows:
            if row["image_path"]:
                file_paths.add(row["image_path"])
        return file_paths

    def list_oldest_event_ids(self) -> list[int]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id
                FROM events
                WHERE status = 'finalized' AND clip_path IS NOT NULL
                ORDER BY started_at ASC
                """
            ).fetchall()
        return [int(row["id"]) for row in rows]

    def delete_event(self, event_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM detections WHERE event_id = ?", (event_id,))
            conn.execute("DELETE FROM events WHERE id = ?", (event_id,))

    def count_events(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM events WHERE status = 'finalized' AND clip_path IS NOT NULL"
            ).fetchone()
        return int(row["total"])

    def vacuum(self) -> None:
        with sqlite3.connect(self.db_path, isolation_level=None) as conn:
            conn.execute("VACUUM")

    def _row_to_event(self, row: sqlite3.Row) -> EventRecord:
        return EventRecord(
            id=int(row["id"]),
            camera_name=row["camera_name"],
            started_at=_text_to_dt(row["started_at"]) or datetime.now(),
            ended_at=_text_to_dt(row["ended_at"]),
            status=row["status"],
            has_person=bool(row["has_person"]),
            best_confidence=float(row["best_confidence"])
            if row["best_confidence"] is not None
            else None,
            clip_path=row["clip_path"],
            thumbnail_path=row["thumbnail_path"],
            snapshot_path=row["snapshot_path"],
            size_bytes=int(row["size_bytes"] or 0),
            metadata=json.loads(row["metadata"]) if row["metadata"] else None,
        )

    def get_detections(
        self,
        camera_name: str | None = None,
        event_id: int | None = None,
        limit: int = 100,
    ) -> list[DetectionRecord]:
        query = ["SELECT * FROM detections WHERE 1 = 1"]
        params: list[object] = []
        if camera_name:
            query.append("AND camera_name = ?")
            params.append(camera_name)
        if event_id is not None:
            query.append("AND event_id = ?")
            params.append(event_id)
        query.append("ORDER BY timestamp DESC LIMIT ?")
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(" ".join(query), params).fetchall()
        return [self._row_to_detection(row) for row in rows]

    def _row_to_detection(self, row: sqlite3.Row) -> DetectionRecord:
        return DetectionRecord(
            id=int(row["id"]),
            event_id=row["event_id"],
            timestamp=_text_to_dt(row["timestamp"]) or datetime.now(),
            camera_name=row["camera_name"],
            confidence=float(row["confidence"]),
            bbox_x1=int(row["bbox_x1"]),
            bbox_y1=int(row["bbox_y1"]),
            bbox_x2=int(row["bbox_x2"]),
            bbox_y2=int(row["bbox_y2"]),
            image_path=row["image_path"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else None,
        )

    def get_statistics(self) -> dict[str, int]:
        with self._connect() as conn:
            detections = conn.execute("SELECT COUNT(*) AS total FROM detections").fetchone()
            events = conn.execute(
                "SELECT COUNT(*) AS total FROM events WHERE status = 'finalized' AND clip_path IS NOT NULL"
            ).fetchone()
        return {
            "detections": int(detections["total"]),
            "events": int(events["total"]),
        }
