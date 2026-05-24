# EZVIZ (Hikvision-firmware) cameras

EZVIZ is Hikvision's consumer brand. Tested with the EZVIZ C6N (indoor pan/tilt). Most other Hikvision-firmware cameras (DS-2CD-series included) use the same RTSP scheme.

## Enable RTSP / "LAN Live View"

1. Open the EZVIZ app and select the camera.
2. Profile → Settings → LAN Live View.
3. Scan the camera, log in with the **verification code printed on the device label**, and activate RTSP.

The verification code is the RTSP password — not your EZVIZ cloud account password. If you changed it in the app, use the updated one.

## URL patterns

```
# Main stream (high quality)
rtsp://admin:<VERIFICATION_CODE>@<host>:554/ch1/main

# Substream (low bitrate, lower resolution)
rtsp://admin:<VERIFICATION_CODE>@<host>:554/ch1/sub
```

If `/ch1/main` doesn't work, try these alternates:

```
rtsp://admin:<VERIFICATION_CODE>@<host>:554/Streaming/Channels/1
rtsp://admin:<VERIFICATION_CODE>@<host>:554/channel0
rtsp://admin:<VERIFICATION_CODE>@<host>:554//Channel/01   # note the double slash
```

`admin` is the username for every Hikvision-firmware camera I've tested. Don't try to change it; the firmware ignores you.

## Codec

EZVIZ cameras typically output **H.265** on both the main stream and the substream. Browsers don't play H.265 over WebRTC reliably, so configure go2rtc to transcode:

```yaml
streams:
  garage:
    - "${CAM_GARAGE_RTSP}"
    - "ffmpeg:garage#video=h264"
```

This adds noticeable CPU cost — on a software-only host expect ~30% per camera while a live view is open. If you have an Intel iGPU, see [`encoders.md`](../encoders.md) for VA-API hardware transcoding.

## Verify

```bash
ffplay -rtsp_transport tcp "rtsp://admin:VERIFICATION_CODE@CAMERA_IP:554/ch1/main"
```

## .env wiring

```bash
CAM_GARAGE_RTSP=rtsp://admin:VERIFICATION_CODE@CAMERA_IP:554/ch1/main
```

## Quirks

- The C6N's pan/tilt is **not** controllable over RTSP. Use the EZVIZ app for that; RTSP only streams the current view direction.
- Some firmware versions silently drop RTSP if you've never enabled "LAN Live View" in the app — even if you've set a verification code. The toggle in the app is the magic step.
- DHCP lease drift is real. Reserve the IP on your router by MAC.
