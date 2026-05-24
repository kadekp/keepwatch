#!/usr/bin/env python3
"""Entry point for the multi-camera detection and recording service."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).parent))

from detector.api import create_app
from detector.config import load_config
from detector.database import DetectionDatabase
from detector.image_storage import MediaStorage


def purge_finalized_events(config_path: str) -> None:
    config = load_config(config_path)
    storage = MediaStorage(config.storage)
    database = DetectionDatabase(config.storage.db_path)

    event_ids = database.list_oldest_event_ids()
    deleted_files = 0
    for event_id in event_ids:
        file_paths = database.get_event_file_paths(event_id)
        deleted_files += len(file_paths)
        storage.delete_paths(file_paths)
        database.delete_event(event_id)

    database.vacuum()
    print(
        f"Purged {len(event_ids)} finalized events and {deleted_files} linked media files from {config.storage.db_path}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="CCTV detection and recording service")
    parser.add_argument(
        "--config",
        type=str,
        default="detector.yaml",
        help="Path to detector config file (default: detector.yaml)",
    )
    parser.add_argument(
        "--purge-finalized-events",
        action="store_true",
        help="Delete finalized events and linked media, then exit",
    )
    args = parser.parse_args()

    if args.purge_finalized_events:
        purge_finalized_events(args.config)
        return

    config = load_config(args.config)
    app = create_app(config)
    uvicorn.run(
        app,
        host=config.api.host,
        port=config.api.port,
        log_level=config.log_level.lower(),
    )


if __name__ == "__main__":
    main()
