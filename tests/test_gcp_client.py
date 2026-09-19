import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from modules.gcp_client import (
    resolve_gcp_config,
    parse_gcs_uri,
    guess_mime_type,
    is_gdrive_source,
    parse_gdrive_url,
    _natural_sort_key,
    resolve_multicam_gdrive_inputs,
    fix_mojibake_filename,
    extract_filename_from_content_disposition,
)


class TestGcpClient(unittest.TestCase):
    def test_fix_mojibake_filename_and_content_disposition(self):
        original = "CAM1_主機位訪談錄影_4K.mp4"
        latin1_mojibake = original.encode("utf-8").decode("latin-1")
        self.assertEqual(fix_mojibake_filename(latin1_mojibake), original)
        self.assertEqual(
            extract_filename_from_content_disposition(f'attachment; filename="{latin1_mojibake}"', "fallback.mp4"),
            original,
        )
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

    def test_is_gdrive_source(self):
        self.assertTrue(is_gdrive_source("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz123456"))
        self.assertTrue(is_gdrive_source("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz123456/view?usp=sharing"))
        self.assertTrue(is_gdrive_source("gdrive://1AbCdEfGhIjKlMnOpQrStUvWxYz123456"))
        self.assertFalse(is_gdrive_source("/Users/sylph/Videos/cam1.mp4"))
        self.assertFalse(is_gdrive_source("gs://my-bucket/raw/cam1.mp4"))
        self.assertFalse(is_gdrive_source(None))

    def test_parse_gdrive_url(self):
        r1 = parse_gdrive_url("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz123456?usp=drive_link")
        self.assertEqual(r1, {"id": "1AbCdEfGhIjKlMnOpQrStUvWxYz123456", "type": "folder"})

        r2 = parse_gdrive_url("https://drive.google.com/file/d/1XyZ9876543210AbCdEfGhIjKlMnOpQrS/view?usp=sharing")
        self.assertEqual(r2, {"id": "1XyZ9876543210AbCdEfGhIjKlMnOpQrS", "type": "file"})

        r3 = parse_gdrive_url("https://drive.google.com/open?id=1XyZ9876543210AbCdEfGhIjKlMnOpQrS")
        self.assertEqual(r3, {"id": "1XyZ9876543210AbCdEfGhIjKlMnOpQrS", "type": "unknown"})

        r4 = parse_gdrive_url("gdrive://folder/1AbCdEfGhIjKlMnOpQrStUvWxYz123456")
        self.assertEqual(r4, {"id": "1AbCdEfGhIjKlMnOpQrStUvWxYz123456", "type": "folder"})

    def test_natural_sort_key_for_cameras(self):
        names = ["CAM10.mp4", "CAM2.mp4", "CAM1.mp4", "CAM3.mp4"]
        sorted_names = sorted(names, key=_natural_sort_key)
        self.assertEqual(sorted_names, ["CAM1.mp4", "CAM2.mp4", "CAM3.mp4", "CAM10.mp4"])

    @patch("modules.gcp_client.download_gdrive_file_with_cache")
    @patch("modules.gcp_client.list_gdrive_folder_videos")
    def test_resolve_multicam_gdrive_inputs_folder(self, mock_list, mock_dl):
        mock_list.return_value = [
            {"id": "id_cam1", "name": "CAM1_main.mp4", "size": 1000},
            {"id": "id_cam2", "name": "CAM2_side.mp4", "size": 1000},
            {"id": "id_cam3", "name": "CAM3_wide.mp4", "size": 1000},
        ]
        mock_dl.side_effect = lambda file_id, **kwargs: f"/tmp/gdrive_inputs/{file_id}.mp4"

        ref, targets = resolve_multicam_gdrive_inputs(
            ref=None,
            targets=None,
            gdrive_folder="https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz123456",
            output_dir="/tmp/out",
        )
        self.assertEqual(ref, "/tmp/gdrive_inputs/id_cam1.mp4")
        self.assertEqual(targets, ["/tmp/gdrive_inputs/id_cam2.mp4", "/tmp/gdrive_inputs/id_cam3.mp4"])


if __name__ == "__main__":
    unittest.main()
