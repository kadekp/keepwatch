# Finding your camera's RTSP URL

keepwatch consumes RTSP streams. Every IP camera worth buying speaks RTSP, but the URL format varies wildly. This page is a generic guide; brand-specific recipes are in [`docs/cameras/`](cameras/).

## The general shape

```
rtsp://<username>:<password>@<host>:<port><path>?<options>
```

| Part | Typical value |
|------|---------------|
| `<username>:<password>` | Whatever you set in the camera's web UI or mobile app. Often a separate "RTSP user" distinct from the cloud account. |
| `<host>` | The camera's LAN IP. Use DHCP reservation so it doesn't change. |
| `<port>` | `554` for almost everything. |
| `<path>` | Brand-specific. Examples: `/stream1`, `/ch1/main`, `/Streaming/Channels/1`. |

## How to find the path

1. **Check the manufacturer's docs.** Look for "RTSP" or "ONVIF" in the manual. The path is usually in a one-pager.
2. **Enable RTSP in the app.** Many cameras (EZVIZ, Tapo, iCSee) ship with RTSP disabled by default. Find the toggle in the mobile app.
3. **Use ONVIF Device Manager** (Windows) or `onvif-cli` (cross-platform) to discover the stream URI directly from the camera.
4. **Try the common patterns.** A short list per brand is in [`docs/cameras/`](cameras/).

## Verify it works

```bash
ffplay -rtsp_transport tcp 'rtsp://user:pass@192.0.2.10:554/stream1'
```

You should see live video. If `ffplay` is missing or you prefer no GUI, record a 5-second sample to a file:

```bash
ffmpeg -rtsp_transport tcp -i 'rtsp://user:pass@192.0.2.10:554/stream1' \
       -t 5 -c copy sample.mp4 && ls -lh sample.mp4
```

A non-zero file size means the URL is correct. Open the file in VLC to double-check the video isn't garbled.

## Things that go wrong

- **"401 Unauthorized"** — wrong credentials, or the camera distinguishes between cloud login and RTSP user. Check the app settings.
- **"Connection refused"** — RTSP service disabled in the camera, or wrong port. The default is 554 but some Hikvision-style firmwares move it.
- **Stream connects then drops** — you're probably hitting the camera's "1 RTSP client" limit. That's why keepwatch funnels everything through go2rtc: only one TCP connection to the camera, multiple consumers downstream.
- **H.265 + browser playback** — most cameras now ship H.265 by default. Browsers don't play H.265 over WebRTC reliably, so go2rtc transcodes on demand. If you have a software-only host, expect 50–100% CPU per camera while the browser is live-viewing.
- **Video stretched / wrong aspect** — common with portrait-mode firmware. The fix is in the go2rtc transcode line: use `scale=W:H:force_original_aspect_ratio=decrease,setsar=1` instead of `scale_vaapi=W:H` (which stretches).

## Lock the IP

Once a camera is on a known IP, reserve it in your router by MAC address. Otherwise a DHCP lease renewal will silently break your config the next time the camera reboots. Most consumer routers let you do this from their admin UI; OpenWrt and pfSense both have one-line config equivalents.

## Brand-specific notes

- [`docs/cameras/icsee.example.md`](cameras/icsee.example.md) — iCSee / Xiongmai-firmware cameras.
- [`docs/cameras/tapo.example.md`](cameras/tapo.example.md) — TP-Link Tapo (C320WS tested).
- [`docs/cameras/ezviz.example.md`](cameras/ezviz.example.md) — EZVIZ / Hikvision (C6N tested).

If you get a new brand working, please open a PR adding a sibling file. The template lives next to the existing examples.
