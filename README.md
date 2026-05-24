# keepwatch

A small, readable, self-hosted CCTV recorder for the home lab. **One config file, your RTSP cameras, motion-triggered HLS clips with optional person detection.** No MQTT, no cloud, no message bus.

<!-- Add a dashboard screenshot at docs/screenshots/dashboard.png and uncomment:
![Dashboard](docs/screenshots/dashboard.png)
-->

```
   RTSP cameras  ─►  go2rtc  ─►  Python detector  ─►  HLS event clips
                                       │                  │
                                       ▼                  ▼
                                   SQLite              dashboard
```

---

## What it does

- Continuously records each RTSP stream into 5-second `.ts` segments. On motion, those segments are stitched into an HLS event clip with pre-roll and post-roll.
- Runs YOLOv8n once per detected motion to flag person events (single shared model across all cameras).
- Serves a browser dashboard with live WebRTC view + recorded-event playback.
- Manages its own disk: hard cap + low-water mark, oldest events deleted first.
- One Python process, one go2rtc process, one config file. That's the whole runtime.

## What it isn't

**keepwatch is intentionally small.** If you need any of the following, use [Frigate](https://frigate.video/) — it's excellent and these are all out of scope here:

- MQTT events / Home Assistant integration / Node-RED hooks
- Multi-class object detection (cars, packages, animals)
- Coral TPU pipelines
- Two-way audio
- Cloud sync, off-host storage, replication
- Multi-tenant / multi-operator
- A built-in NVR interface for ten cameras and PTZ control

What you get instead: ~1,600 lines of Python you can actually read in an afternoon.

---

## Quick start (Docker)

You need a working RTSP URL from at least one camera before this is interesting. See [`docs/cameras.md`](docs/cameras.md) for tips on getting one out of common brands.

```bash
git clone https://github.com/<your-username>/keepwatch.git
cd keepwatch

# 1. Copy the three example configs and edit them.
cp .env.example .env
cp detector.yaml.example detector.yaml
cp go2rtc.yaml.example go2rtc.yaml
$EDITOR .env detector.yaml go2rtc.yaml

# 2. Start the stack.
docker compose -f docker/docker-compose.yml up --build
```

Then open `http://127.0.0.1:1984` (the go2rtc dashboard — HTTP basic auth from `.env`). The detector's event API is at `http://127.0.0.1:8000/health`.

To expose either service to your LAN, change `LISTEN_HOST` in `.env` and read [the security note](#security) first.

## Quick start (bare metal Linux)

```bash
# 1. Install ffmpeg + go2rtc.
sudo apt install ffmpeg
curl -L https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_amd64 \
     -o go2rtc && chmod +x go2rtc

# 2. Install Python deps with uv.
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync

# 3. Configure (see Docker quickstart above).

# 4. Run go2rtc in one terminal, detector in another.
set -a; . ./.env; set +a
./go2rtc -c go2rtc.yaml &
uv run python run_detector.py --config detector.yaml
```

For a permanent install, see the systemd unit templates in [`deploy/`](deploy/).

---

## Configuration

The whole runtime is driven by three files:

| File | What it controls |
|------|------------------|
| [`.env`](.env.example) | Camera RTSP URLs, dashboard credentials, listen host. Never committed. |
| [`go2rtc.yaml`](go2rtc.yaml.example) | Stream definitions, dashboard listener, RTSP restream port, transcoder. |
| [`detector.yaml`](detector.yaml.example) | Cameras, motion/inference tuning, storage limits, detector API. |

Full field reference in [`docs/configuration.md`](docs/configuration.md).

## Architecture

One paragraph: go2rtc owns RTSP ingestion and exposes a loopback restream that the detector reads. The detector runs one thread per camera (motion + clip orchestration), one shared YOLOv8n model behind a lock, one ffmpeg subprocess per camera writing 5-second segments. A FastAPI process serves `/health`, `/api/cameras`, `/api/storage`, `/api/events`, and statically mounts the `media/` directory for the dashboard to play. SQLite holds event metadata; the filesystem holds segments and clips.

Diagram + threading model + module dependency graph in [`docs/architecture.md`](docs/architecture.md).

## Encoders

`recording.encoder` in `detector.yaml` picks the video encoder used when a camera has a `crop` configured (which forces re-encoding). The default is **`software`** (libx264) — works on any machine ffmpeg runs on.

| Encoder | When to use | CPU impact |
|---------|-------------|------------|
| `software` | Default. Any platform. | High |
| `vaapi` | Intel or AMD GPU on Linux with `/dev/dri/renderD128`. | Low |
| `nvenc` | NVIDIA GPU with the `--enable-nvenc` ffmpeg build. | Very low |
| `videotoolbox` | macOS. | Low |

Pick one in [`docs/encoders.md`](docs/encoders.md). Wrong picks fail loudly at recorder start, not silently.

## Performance

Reference figures from a 3-camera deployment on an Intel i3-7100U mini PC with HD Graphics 620, all H.265 cameras transcoded to H.264:

| Component | CPU | Notes |
|-----------|-----|-------|
| go2rtc (per H.265 camera) | 25–55% | Down from 80–110% on software |
| Detector recording (cropped) | 5–10% | Per camera, VA-API |
| Detector inference | 35–45% | YOLOv8n on 3 cameras |
| Total | ~100–150% | One core for live + recording + detection |

On a fully software-encoded path, double those numbers.

---

## Roadmap

The aim is to stay small. Things on the list:

- Optional notification webhooks (no MQTT — just a `POST` to a URL of your choice).
- Better dashboard playback on Firefox / Chrome (currently Safari is the only native HLS browser).
- Multi-class detection toggle (still YOLO-based, but allow `vehicle`, `bicycle`).
- Linux ARM container build for Raspberry Pi 5 deployments.
- Replace the SQLite `ALTER TABLE` shims with real migrations.

Things explicitly **not** on the list:

- HomeAssistant integration, MQTT, message brokers
- Web-based config editor
- Multi-operator / multi-tenant
- Cloud sync

## Security

keepwatch binds to `127.0.0.1` by default. Anything beyond that — LAN, Tailscale, a reverse proxy — is your call. Recommended layered defenses if you expose the dashboard:

1. Use a VPN or zero-trust mesh (Tailscale, WireGuard, Cloudflare Tunnel) instead of opening a port to the internet.
2. Keep HTTP basic auth on the go2rtc dashboard (`GO2RTC_API_PASSWORD`).
3. Bind to a specific interface, not `0.0.0.0`. The example configs use `${LISTEN_HOST}` from `.env`.
4. Put a firewall in front (UFW default-deny inbound on Linux is a one-liner).

The detector's `:8000` API has **no auth** — it assumes its network boundary is the authentication boundary. Don't expose it directly to the internet.

## Contributing

PRs welcome for: bug fixes, new encoder profiles, new camera examples, dashboard improvements that stay vanilla-JS, performance work.

PRs likely to be declined: anything in the "not on the list" section above. Open an issue first if unsure.

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
