# Encoders

The detector only re-encodes when a camera has a `crop` configured (cropping forces re-encoding because we can't stream-copy a cropped frame). Uncropped cameras always use `-c copy` and skip the encoder entirely.

`recording.encoder` in `detector.yaml` picks which ffmpeg encoder to use when re-encoding is needed.

## Which one should you use?

```
                  Linux            Linux       Linux+         macOS
                  no GPU           +Intel/AMD  +NVIDIA
                  ─────────────────────────────────────────────────
  default         software         software    software       software
  upgrade to      —                vaapi       nvenc          videotoolbox
```

**Software is always a safe default.** It's what the example config ships with. Pick a hardware encoder only when:
- You have one or more cameras with `crop:` configured, AND
- Your detector CPU is more than ~50% saturated, AND
- You have a compatible GPU.

## Profiles

### `software` (default)

```yaml
recording:
  encoder: software
```

Uses libx264, baseline profile, level 3.1, `superfast` preset, `zerolatency` tune, yuv420p. Works on any ffmpeg build that includes libx264 (i.e. all of them). CPU cost is proportional to the resolution and FPS of cropped streams.

### `vaapi` — Intel / AMD on Linux

```yaml
recording:
  encoder: vaapi
  vaapi_device: /dev/dri/renderD128
```

Requirements:
- Linux host with an Intel iGPU (Gen 7+ for full H.264 encode support) or an AMD GPU with VAAPI driver.
- System ffmpeg with VA-API support — Ubuntu/Debian repos work; **static builds typically do not**.
- Render device readable+writable by the detector user. Either add the user to the `video` and `render` groups, or `chmod 666 /dev/dri/renderD128`.

Detect support before going live:
```bash
ffmpeg -vaapi_device /dev/dri/renderD128 \
       -f lavfi -i testsrc=duration=1:size=320x240:rate=1 \
       -c:v h264_vaapi -f null -
```

Should complete without errors. If `Cannot load libva` or `Failed to open device`, your ffmpeg lacks VA-API.

The detector validates `/dev/dri/renderD128` (or whatever you set in `vaapi_device`) exists at recorder start. If it doesn't, you get a clear error instead of a cryptic ffmpeg crash.

### `nvenc` — NVIDIA GPUs

```yaml
recording:
  encoder: nvenc
```

Requirements:
- NVIDIA GPU with NVENC (Maxwell or newer for H.264).
- ffmpeg built with `--enable-nvenc` and the NVIDIA driver.
- Outside containers, you also need `libnvidia-encode.so` in the dynamic linker path.

Performance is excellent — the GPU does practically all the work. The trade-off is bigger driver / library surface and a stricter ffmpeg build requirement.

Not validated automatically by the detector; if the encoder isn't available, the ffmpeg subprocess will fail at startup and `recorder_error` will surface in `/health`.

### `videotoolbox` — macOS

```yaml
recording:
  encoder: videotoolbox
```

Apple's hardware H.264 encoder. Works on any modern Mac (Intel + Apple Silicon). ffmpeg from Homebrew includes VideoToolbox support by default.

Quality is good for live streams but lower than libx264 at equivalent bitrate. Use only if you need the CPU back.

## What happens if I pick wrong?

The detector tries to validate device prerequisites where it can (currently: VA-API render device existence). For other failures the ffmpeg subprocess will fail at startup and the failure surfaces as `recorder_error` in the `/health` payload — the dashboard will flag the affected camera as "Recorder error". Logs land in `logs/detector.log` for the actual stderr.

To recover: edit `detector.yaml`, change `encoder:` to `software`, restart the detector. The .ts segments already on disk are unaffected.

## Why is the go2rtc transcoder configured separately?

go2rtc has its own ffmpeg invocation for live-stream transcoding (turning H.265 camera streams into H.264 for browsers). The `ffmpeg.h264` line in `go2rtc.yaml` controls that — independently from `recording.encoder` in `detector.yaml`. If you want hardware acceleration for the live view too, edit the go2rtc transcoder string. The [go2rtc docs](https://github.com/AlexxIT/go2rtc#source-ffmpeg) cover the syntax.
