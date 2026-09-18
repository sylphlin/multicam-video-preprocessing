import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from modules.gcp_client import resolve_gcp_config, parse_gcs_uri, guess_mime_type


class TestGcpClient(unittest.TestCase):
    def test_guess_mime_type(self):
        self.assertEqual(guess_mime_type("multicam_merged_full.mp4"), "video/mp4")
        self.assertEqual(guess_mime_type("chunk_001.mp3"), "audio/mpeg")
        self.assertEqual(guess_mime_type("audio.wav"), "audio/wav")
        self.assertEqual(guess_mime_type("cam1.mov"), "video/quicktime")

    def test_parse_gcs_uri(self):
        bucket, blob = parse_gcs_uri("gs://my-bucket/raw/multicam_merged_full.mp4")
        self.assertEqual(bucket, "my-bucket")
        self.assertEqual(blob, "raw/multicam_merged_full.mp4")

        with self.assertRaises(ValueError):
            parse_gcs_uri("https://storage.googleapis.com/my-bucket/file.mp4")

    def test_resolve_gcp_config_cli_overrides(self):
        cfg = resolve_gcp_config(
            cli_project="custom-proj",
            cli_bucket="gs://custom-bucket/",
            cli_location="global",
            cli_region="asia-east1",
        )
        self.assertEqual(cfg["project"], "custom-proj")
        self.assertEqual(cfg["bucket"], "custom-bucket")
        self.assertEqual(cfg["location"], "global")
        self.assertEqual(cfg["region"], "asia-east1")

    @patch("modules.gcp_client._parse_env_file", return_value={})
    @patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "auto-proj"}, clear=True)
    def test_resolve_gcp_config_deterministic_default_bucket(self, _mock_env):
        cfg = resolve_gcp_config()
        self.assertEqual(cfg["project"], "auto-proj")
        self.assertEqual(cfg["bucket"], "multicam-video-auto-proj")
        self.assertEqual(cfg["location"], "global")
        self.assertEqual(cfg["region"], "us-central1")


if __name__ == "__main__":
    unittest.main()
