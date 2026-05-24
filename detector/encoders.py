"""Encoder profiles for the segment recorder.

Each profile maps a logical encoder name (software, vaapi, nvenc, videotoolbox)
to the ffmpeg argv fragments needed to:
  * set up any global options before -i (e.g. -vaapi_device)
  * stream-copy when no crop is configured (always the same: -c copy)
  * apply a crop filter + re-encode when a crop is configured

The profile is chosen by `recording.encoder` in detector.yaml. Default is
``software``, which uses libx264 and runs anywhere ffmpeg does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class EncoderProfile:
    name: str
    setup_args: list[str] = field(default_factory=list)
    # Returned by crop_filter_and_encoder() — built dynamically because the
    # crop filter string depends on per-camera ratios.
    requires_device: str | None = None

    def build_crop_args(
        self,
        crop_filter: str,
        segment_seconds: int,
        vaapi_device: str | None = None,
    ) -> list[str]:
        raise NotImplementedError


class SoftwareEncoder(EncoderProfile):
    def build_crop_args(self, crop_filter, segment_seconds, vaapi_device=None):
        return [
            "-vf",
            crop_filter,
            "-c:v",
            "libx264",
            "-profile:v",
            "baseline",
            "-level:v",
            "3.1",
            "-preset:v",
            "superfast",
            "-tune:v",
            "zerolatency",
            "-pix_fmt:v",
            "yuv420p",
            "-force_key_frames",
            f"expr:gte(t,n_forced*{segment_seconds})",
        ]


class VaapiEncoder(EncoderProfile):
    def build_crop_args(self, crop_filter, segment_seconds, vaapi_device=None):
        # The chosen device is passed in via vaapi_setup_args before -i; the
        # encoder argv itself doesn't need to repeat it.
        return [
            "-vf",
            f"{crop_filter},format=nv12,hwupload",
            "-c:v",
            "h264_vaapi",
            "-profile:v",
            "constrained_baseline",
            "-level:v",
            "3.1",
            "-qp",
            "23",
            "-bf",
            "0",
            "-force_key_frames",
            f"expr:gte(t,n_forced*{segment_seconds})",
        ]


class NvencEncoder(EncoderProfile):
    def build_crop_args(self, crop_filter, segment_seconds, vaapi_device=None):
        return [
            "-vf",
            crop_filter,
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p5",
            "-tune",
            "ll",
            "-rc",
            "constqp",
            "-qp",
            "23",
            "-bf",
            "0",
            "-force_key_frames",
            f"expr:gte(t,n_forced*{segment_seconds})",
        ]


class VideotoolboxEncoder(EncoderProfile):
    def build_crop_args(self, crop_filter, segment_seconds, vaapi_device=None):
        return [
            "-vf",
            crop_filter,
            "-c:v",
            "h264_videotoolbox",
            "-profile:v",
            "baseline",
            "-realtime",
            "1",
            "-pix_fmt:v",
            "yuv420p",
            "-force_key_frames",
            f"expr:gte(t,n_forced*{segment_seconds})",
        ]


_ENCODERS: dict[str, EncoderProfile] = {
    "software": SoftwareEncoder(name="software"),
    "vaapi": VaapiEncoder(name="vaapi", requires_device="/dev/dri/renderD128"),
    "nvenc": NvencEncoder(name="nvenc"),
    "videotoolbox": VideotoolboxEncoder(name="videotoolbox"),
}


def get_profile(name: str) -> EncoderProfile:
    """Return the encoder profile for ``name``, falling back to software."""
    key = (name or "software").strip().lower()
    return _ENCODERS.get(key, _ENCODERS["software"])


def available_encoder_names() -> list[str]:
    return list(_ENCODERS.keys())


def vaapi_setup_args(vaapi_device: str | None) -> list[str]:
    """Global ffmpeg args needed before -i for VA-API."""
    device = vaapi_device or "/dev/dri/renderD128"
    return ["-vaapi_device", device]


def validate_device(profile: EncoderProfile, vaapi_device: str | None) -> str | None:
    """Return a human-readable error if the encoder's required device is missing.

    Returns None when the encoder has no device requirement or the device exists.
    """
    if profile.name == "vaapi":
        device = vaapi_device or profile.requires_device or "/dev/dri/renderD128"
        if not Path(device).exists():
            return (
                f"VA-API encoder selected but device {device!r} does not exist. "
                "Install Intel/AMD VA-API drivers, or set recording.encoder: software."
            )
    return None
