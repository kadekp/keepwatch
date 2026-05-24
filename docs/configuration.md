# Configuration reference

Three files drive everything. Sample copies live next to this README with `.example` suffixes.

## `.env`

Consumed by `go2rtc.yaml` (`${VAR}` substitution) and by `docker-compose.yml`. The detector itself doesn't read this file.

| Variable | Required | Purpose |
|----------|----------|---------|
| `CAM_<NAME>_RTSP` | yes (per camera) | Full RTSP URL for each camera, including credentials. Names are arbitrary — match them in `go2rtc.yaml`. |
| `GO2RTC_API_USER` | yes | Username for the dashboard's HTTP basic auth. |
| `GO2RTC_API_PASSWORD` | yes | Strong random password for the dashboard. `openssl rand -hex 24` is one way. |
| `LISTEN_HOST` | yes | Interface for the dashboard / WebRTC listener. Default `127.0.0.1`. Set to a Tailscale or LAN IP to expose. Never `0.0.0.0` unless you really mean it. |

`chmod 600 .env` after creating it.

## `go2rtc.yaml`

Stream definitions, dashboard, RTSP restream, transcoder profile.

The keys you'll edit most often:

- `streams.<name>` — list of sources for a stream. First is the real camera (usually `${CAM_<NAME>_RTSP}`). Second is typically `ffmpeg:<name>#video=h264`, which re-encodes on demand for browser playback.
- `ffmpeg.h264` — the global ffmpeg argv go2rtc uses for transcoding. The example uses software encoding (`libx264`). For hardware acceleration see [`encoders.md`](encoders.md).
- `api.listen` — `${LISTEN_HOST}:1984`. Where the dashboard binds.
- `rtsp.listen` — `127.0.0.1:8554`. Don't change unless you really know.
- `webrtc.listen` + `webrtc.candidates` — `${LISTEN_HOST}:8555`. For most setups, leave these matched.

Full reference: [go2rtc docs](https://github.com/AlexxIT/go2rtc#configuration).

## `detector.yaml`

Cameras, detection tuning, storage limits, detector API.

### Top-level sections

| Section | Purpose |
|---------|---------|
| `log_level` | INFO / DEBUG / WARNING. |
| `model` | Where to find the YOLOv8 weights file; whether to use MPS (Apple Silicon). |
| `recording` | ffmpeg path, segment timing, encoder choice, retention pre/post roll. |
| `storage` | Where to write the SQLite DB, logs, media. Disk caps. |
| `api` | FastAPI listen host/port + CORS origin regex. |
| `cameras` | List of cameras. One per stream. |

### Recording fields

| Field | Default | What it does |
|-------|---------|--------------|
| `ffmpeg_path` | `ffmpeg` | Override if your system needs a specific binary (e.g. `/usr/bin/ffmpeg` for VA-API support on Ubuntu). |
| `segment_seconds` | `5` | Length of each rolling `.ts` segment. Smaller = finer-grained clips, more files. |
| `pre_roll_seconds` | `10` | How much pre-motion footage to include in a finalized clip. |
| `post_roll_seconds` | `20` | How long after the last motion frame before finalizing. |
| `segment_buffer_seconds` | `180` | How long to retain rolling segments before they're considered stale. Should comfortably exceed `pre_roll`. |
| `encoder` | `software` | One of `software`, `vaapi`, `nvenc`, `videotoolbox`. See [`encoders.md`](encoders.md). |
| `vaapi_device` | `/dev/dri/renderD128` | Used only when `encoder: vaapi`. |

### Storage fields

| Field | Default | What it does |
|-------|---------|--------------|
| `db_path` | `data/detections.db` | SQLite database for events + detections. |
| `logs_dir` | `logs` | Where the detector writes its log file. |
| `media_root` | `media` | Parent dir for `clips/`, `segments/`, `thumbnails/`, `snapshots/`, `tmp/`. |
| `jpeg_quality` | `85` | For saved thumbnails and snapshots. |
| `max_bytes` | `53687091200` (50 GB) | Hard cap for managed storage. |
| `low_water_bytes` | `48318382080` (45 GB) | Retention deletes oldest events until usage drops below this. |

All paths are resolved relative to `detector.yaml`.

### API fields

| Field | Default | What it does |
|-------|---------|--------------|
| `host` | `127.0.0.1` (in example) / `0.0.0.0` (in dataclass) | Always set this in YAML. Loopback is the safest default. |
| `port` | `8000` | FastAPI listen port. |
| `cors_origin_regex` | `^https?://[^/]+:1984$` | Restrict which origins can call the API (the dashboard origin). |

### Per-camera fields

```yaml
- name: front              # short identifier, used in URLs and on disk
  display_name: Front Door # shown in the dashboard
  detect_rtsp_url: rtsp://127.0.0.1:8554/front?video=h264
  record_rtsp_url: rtsp://127.0.0.1:8554/front?video=h264
  capture_fps: 2.0          # how often the detector pulls a frame
  inference_fps: 1.0        # how often YOLO runs on a sampled frame
  frame_width: 480          # detector-internal resize (square = fast)
  frame_height: 480
  motion_threshold: 25      # MOG2 varThreshold
  motion_min_area: 5000     # ignore motion blobs smaller than this (px²)
  motion_blur_size: 21      # Gaussian blur kernel pre-detection
  confidence_threshold: 0.5 # YOLO confidence floor
  cooldown_seconds: 2.0     # min gap between person hits inside one event
  crop:                     # optional — fraction-of-frame rectangle
    x: 0.0
    y: 0.5
    width: 1.0
    height: 0.5
```

Omit `crop:` entirely for full-frame detection (stream-copy recording, no re-encode).

## Tuning tips

- **Too many motion events from camera noise?** Bump `motion_min_area` (e.g. `8000`). If that doesn't help, raise `motion_threshold` (e.g. `35`).
- **Missing fast events?** Raise `capture_fps` to `4.0`. Doubles detector CPU per camera.
- **Person detection too jittery?** Lower `cooldown_seconds` to `1.0`; raise `confidence_threshold` to `0.6`.
- **Pre-roll feels short?** Bump `pre_roll_seconds` and `segment_buffer_seconds` together (the buffer must always exceed pre-roll).
- **Disk filling too fast?** Drop `max_bytes` and `low_water_bytes` proportionally. Keep at least 1 GB of headroom between them.
- **Crop excluding too much?** Coordinates are normalized — `(x=0, y=0)` is top-left, `(x+width, y+height) ≤ 1`.
