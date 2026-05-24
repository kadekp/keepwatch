# TP-Link Tapo cameras

Tested with the Tapo C320WS (2K outdoor pan-only). The C100/C200/C210 indoor cameras use the same RTSP scheme.

## Enable RTSP

1. Open the Tapo app and select the camera.
2. Camera Settings → Advanced Settings → Camera Account.
3. Create a username and a password. **This is a separate account from your TP-Link cloud login** — RTSP authenticates against this one.

## URL pattern

```
# High-quality main stream
rtsp://<USER>:<PASSWORD>@<host>:554/stream1

# Lower-quality substream (lower bitrate, lower resolution)
rtsp://<USER>:<PASSWORD>@<host>:554/stream2
```

## Codec

Tapo cameras output **H.264 natively** — no transcoding required for browser compatibility. This is one of the few brands where you can skip go2rtc's `ffmpeg:<name>#video=h264` step:

```yaml
streams:
  front_yard:
    - "${CAM_FRONT_RTSP}"   # one source, no transcode
```

## Verify

```bash
ffplay -rtsp_transport tcp "rtsp://USER:PASSWORD@CAMERA_IP:554/stream1"
```

## .env wiring

```bash
CAM_FRONT_RTSP=rtsp://USER:PASSWORD@CAMERA_IP:554/stream1
CAM_FRONT_RTSP_LOW=rtsp://USER:PASSWORD@CAMERA_IP:554/stream2   # optional
```

`go2rtc.yaml`:

```yaml
streams:
  front_yard:
    - "${CAM_FRONT_RTSP}"
  front_yard_low:
    - "${CAM_FRONT_RTSP_LOW}"
```

## Quirks

- Some firmware versions reject RTSP if the camera account was created before a firmware update. Recreate the account if connection fails after an update.
- The C200 doesn't expose two named streams; the path is just `/stream`.
- Pan/tilt control is **not** available over RTSP. Use the Tapo app for that.
