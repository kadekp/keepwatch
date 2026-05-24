# Architecture

## High level

```
RTSP cameras  (LAN, brand-specific URL schemes)
      │
      ▼
┌──────────────────────────────────────────────────────────────────┐
│  go2rtc                                                          │
│   - WebRTC dashboard + signaling   :1984  (auth, ${LISTEN_HOST}) │
│   - WebRTC media                   :8555  (${LISTEN_HOST})       │
│   - RTSP restream                  :8554  (127.0.0.1 only)       │
│   - on-demand ffmpeg transcode (H.265 → H.264 for browsers)      │
└──────────────────────────────────────────────────────────────────┘
      │ (loopback RTSP, per camera)
      ▼
┌──────────────────────────────────────────────────────────────────┐
│  Python detector  (run_detector.py + uvicorn)                    │
│                                                                  │
│   per camera:                                                    │
│     RTSPCapture     ── threaded frame puller w/ auto-reconnect   │
│     MotionDetector  ── MOG2 background subtraction               │
│     SegmentRecorder ── ffmpeg subprocess writing .ts segments    │
│                                                                  │
│   shared:                                                        │
│     PersonDetector  ── single YOLOv8n behind a Lock              │
│     MediaStorage    ── filesystem accounting + retention         │
│     DetectionDatabase ── SQLite events + detections              │
│                                                                  │
│   FastAPI on :8000                                               │
│     GET  /health        — per-camera connectivity + storage      │
│     GET  /api/cameras   — list configured cameras                │
│     GET  /api/storage   — current usage / cap / event count      │
│     GET  /api/events    — recent finalized events                │
│     GET  /media/...     — static-mounted recordings              │
└──────────────────────────────────────────────────────────────────┘
      ▲
      │  HTTP
      ▼
   Static dashboard served by go2rtc (dashboard/index.html)
```

## Threading model

- **CameraWorker** — one thread per camera. Pulls frames, runs motion, kicks off person detection on motion, manages event lifecycle.
- **SegmentRecorder** — owns one ffmpeg subprocess per camera, restart-on-poll, writes 5s segments continuously.
- **RTSPCapture** — one thread per camera, drains frames into a `Queue(maxsize=3)` to prevent backpressure.
- **PersonDetector** — a single YOLOv8n model shared across cameras; access serialized via `detector_lock`.
- **Retention loop** — one thread polling storage usage every 60s; also fires after every event finalization.
- **Uvicorn / FastAPI** — async event loop; calls into the supervisor's thread-safe surface.

The supervisor (`detector/service.py::DetectorSupervisor`) owns all of the above and provides the FastAPI surface as a `lifespan` context manager so workers start with the API and stop with it.

## Module dependency

```
config.py
  ▲
  │
  ├── frame_processing.py     image_storage.py     database.py
  │           ▲                       ▲                 ▲
  │           │                       │                 │
  ├── stream_capture.py     encoders.py                 │
  ├── motion_detector.py        ▲                       │
  ├── person_detector.py        │                       │
  │                             │                       │
  └── recording.py ─────────────┘                       │
            ▲                                           │
            │                                           │
       service.py  (CameraWorker, DetectorSupervisor) ──┘
            ▲
            │
        api.py
            ▲
            │
      run_detector.py
```

No cycles. Each module has a single concern.

## Event lifecycle

1. **Continuous recording.** `SegmentRecorder` is always running. Every 5 seconds (configurable), ffmpeg flushes a fresh `.ts` file into `media/segments/<camera>/`. A 90-second rolling window is kept; older segments are pruned by `SegmentRecorder.cleanup` unless an active event needs them.
2. **Motion detected.** `MotionDetector` (MOG2 background subtraction) flags a frame. `CameraWorker._ensure_event` creates an `events` row in SQLite, saves a thumbnail, and remembers the start time.
3. **Person check (optional).** If a person is detected within the active event, the snapshot and confidence are recorded.
4. **Post-roll wait.** After the last motion frame, the worker waits `post_roll_seconds` before finalizing.
5. **Clip assembly.** `SegmentRecorder.build_clip` copies the .ts segments that overlap with `[event_start - pre_roll, last_motion + post_roll]` into `media/clips/<camera>/YYYY/MM/DD/<event_dir>/` and writes an `index.m3u8` playlist.
6. **DB update.** The event row gets `ended_at`, `clip_path`, `size_bytes`, and `status='finalized'`.
7. **Retention check.** If managed usage exceeds `max_bytes`, the oldest finalized events are deleted until usage drops below `low_water_bytes`.

## Why HLS instead of MP4?

Stream-copy assembly. We can take the .ts segments we already recorded and write a playlist around them without re-encoding. That's why finalization is O(seconds), not O(minutes). The trade-off: only Safari plays HLS natively. Chrome/Firefox need `hls.js` (vendored in `dashboard/vendor/`).

## Why go2rtc?

Two reasons:
1. Browser-friendly transcoding — H.265 cameras need to become H.264 for WebRTC, and go2rtc handles the transcode lifecycle on demand (only when a client connects).
2. Loopback restream — the detector reads from `127.0.0.1:8554` instead of hammering the camera with a second TCP connection. One source connection per camera, multiple consumers (live view + detector recorder).
