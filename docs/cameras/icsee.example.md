# iCSee / Xiongmai firmware cameras

Many "no-name" Chinese cameras run iCSee or its Xiongmai-firmware variants. Common brand surfaces include _Sricam_, _ANRAN_, and a long tail of Aliexpress sellers.

## Enable RTSP

1. Add the camera to the iCSee mobile app first.
2. In the app: Camera Settings → Advanced → Network Setting → enable RTSP.
3. RTSP user/password is sometimes a separate field from the cloud account — check both.

## URL pattern

Two common shapes:

```
# "user= / password=" format (older firmware)
rtsp://<host>:554/user=<USER>_password=<PASSWORD>_channel=0_stream=0&onvif=0.sdp?real_stream

# "standard auth" format (newer firmware)
rtsp://<USER>:<PASSWORD>@<host>:554/cam/realmonitor?channel=1&subtype=0
```

`channel=0` or `stream=0` is the main stream; `stream=1` is the substream.

## Resolution

iCSee firmware often outputs **portrait orientation** (e.g. 2304×2592). If your dashboard tile looks stretched, set the go2rtc transcoder to preserve aspect:

```yaml
ffmpeg:
  h264: "-vf scale=1280:720:force_original_aspect_ratio=decrease,setsar=1 -c:v libx264 ..."
```

## Verify

```bash
ffplay -rtsp_transport tcp "rtsp://USER:PASSWORD@CAMERA_IP:554/cam/realmonitor?channel=1&subtype=0"
```

## .env wiring

```bash
CAM_FRONT_RTSP=rtsp://USER:PASSWORD@CAMERA_IP:554/cam/realmonitor?channel=1&subtype=0
```

Then in `go2rtc.yaml`:

```yaml
streams:
  front:
    - "${CAM_FRONT_RTSP}"
    - "ffmpeg:front#video=h264"   # transcode H.265 → H.264 for browsers
```

## Quirks

- iCSee firmwares sometimes drop RTSP if more than one client connects directly. Funnel everything through go2rtc and your camera only sees one connection.
- Some sellers ship with default passwords like `admin/admin` — change them immediately.
- Substreams may be H.265 even when the main stream is H.264 (or vice versa). Test both before committing to one for `detect_rtsp_url`.
