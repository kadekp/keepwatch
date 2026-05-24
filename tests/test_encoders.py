from __future__ import annotations

import unittest

from detector.encoders import available_encoder_names, get_profile, validate_device


class EncoderRegistryTests(unittest.TestCase):
    def test_known_encoders(self) -> None:
        names = available_encoder_names()
        for name in ("software", "vaapi", "nvenc", "videotoolbox"):
            self.assertIn(name, names)

    def test_unknown_falls_back_to_software(self) -> None:
        self.assertEqual(get_profile("nope").name, "software")
        self.assertEqual(get_profile("").name, "software")
        self.assertEqual(get_profile("SOFTWARE").name, "software")

    def test_software_argv_contains_libx264(self) -> None:
        profile = get_profile("software")
        args = profile.build_crop_args(crop_filter="crop=iw*1:ih*1:0:0", segment_seconds=5)
        self.assertIn("libx264", args)
        self.assertIn("crop=iw*1:ih*1:0:0", args)

    def test_vaapi_argv_contains_h264_vaapi(self) -> None:
        profile = get_profile("vaapi")
        args = profile.build_crop_args(
            crop_filter="crop=iw*1:ih*1:0:0",
            segment_seconds=5,
            vaapi_device="/dev/dri/renderD128",
        )
        self.assertIn("h264_vaapi", args)
        # The hwupload chain is appended to the crop filter.
        vf_index = args.index("-vf")
        self.assertTrue(args[vf_index + 1].endswith("format=nv12,hwupload"))

    def test_nvenc_argv_contains_h264_nvenc(self) -> None:
        profile = get_profile("nvenc")
        args = profile.build_crop_args(crop_filter="crop=iw*1:ih*1:0:0", segment_seconds=5)
        self.assertIn("h264_nvenc", args)

    def test_videotoolbox_argv_contains_h264_videotoolbox(self) -> None:
        profile = get_profile("videotoolbox")
        args = profile.build_crop_args(crop_filter="crop=iw*1:ih*1:0:0", segment_seconds=5)
        self.assertIn("h264_videotoolbox", args)

    def test_validate_device_friendly_message_for_missing_vaapi(self) -> None:
        profile = get_profile("vaapi")
        # Use a path very unlikely to exist.
        result = validate_device(profile, "/dev/dri/renderD-DOES-NOT-EXIST")
        self.assertIsNotNone(result)
        self.assertIn("VA-API", result)

    def test_validate_device_returns_none_for_non_device_encoders(self) -> None:
        for name in ("software", "nvenc", "videotoolbox"):
            profile = get_profile(name)
            self.assertIsNone(validate_device(profile, None))


if __name__ == "__main__":
    unittest.main()
