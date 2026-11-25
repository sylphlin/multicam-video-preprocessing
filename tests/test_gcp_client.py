import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "skills",
        "multicam-video-preprocessing",
        "scripts",
    ),
)
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
        self.assertFalse(is_gdrive_source("/path/to/videos/cam1.mp4"))
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

    @patch("google.genai.Client")
    @patch("modules.llm_client.resolve_gcp_config")
    def test_get_vertex_client_sets_http_timeout(self, mock_cfg, mock_client_cls):
        from modules.llm_client import get_vertex_client

        mock_cfg.return_value = {
            "project": "demo-proj",
            "location": "global",
            "region": "us-central1",
            "bucket": "multicam-video-demo-proj",
        }
        get_vertex_client(project="demo-proj", location="global", timeout_ms=600_000)
        self.assertTrue(mock_client_cls.called)
        kwargs = mock_client_cls.call_args.kwargs
        self.assertTrue(kwargs.get("vertexai"))
        self.assertEqual(kwargs.get("project"), "demo-proj")
        self.assertEqual(kwargs.get("location"), "global")
        self.assertIsNotNone(kwargs.get("http_options"))
        self.assertEqual(kwargs["http_options"].timeout, 600_000)

    def test_extract_visible_text_ignores_thought_parts(self):
        from types import SimpleNamespace
        from generate_edl import _extract_visible_text

        part_thought = SimpleNamespace(thought=True, text="internal reasoning")
        part_visible = SimpleNamespace(thought=False, text="00:00.000,00:05.000,CAM1,Rule,Reason")
        resp = SimpleNamespace(
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part_thought, part_visible]))],
            text="fallback",
        )
        self.assertEqual(_extract_visible_text(resp), "00:00.000,00:05.000,CAM1,Rule,Reason")

    def test_calculate_dynamic_thinking_budget(self):
        from generate_edl import calculate_dynamic_thinking_budget

        b_short = calculate_dynamic_thinking_budget(300.0)
        b_chunk = calculate_dynamic_thinking_budget(1920.0)
        self.assertGreaterEqual(b_short, 1024)
        self.assertLessEqual(b_short, 4096)
        self.assertGreaterEqual(b_chunk, 1024)
        self.assertLessEqual(b_chunk, 4096)

    @patch("modules.video_segmenter.detect_all_silences")
    def test_find_natural_split_points_64min(self, mock_silences):
        from modules.video_segmenter import find_natural_split_points

        mock_silences.return_value = [
            {"start": 1971.2, "end": 1972.6, "duration": 1.4, "mid": 1971.9},
        ]
        pts = find_natural_split_points(
            "dummy.mp4",
            start_sec=0.0,
            end_sec=3867.4,
            min_dur_sec=1800.0,
            max_dur_sec=2400.0,
        )
        self.assertEqual(pts, [0.0, 1971.9, 3867.4])

    def test_shift_and_merge_chunk_edl_rows(self):
        from modules.video_segmenter import shift_and_merge_chunk_edl_rows

        chunk_results = [
            {
                "part_index": 1,
                "start_sec": 0.0,
                "end_sec": 1971.9,
                "csv_rows": [
                    ["Start_Time", "End_Time", "Best_Camera", "剪輯規則", "剪輯原因"],
                    ["00:38.500", "15:00.000", "CAM1", "Rule1", "Opening"],
                    ["15:00.000", "32:51.500", "CAM2", "Rule2", "Guest"],
                ],
            },
            {
                "part_index": 2,
                "start_sec": 1971.9,
                "end_sec": 3867.4,
                "csv_rows": [
                    ["Start_Time", "End_Time", "Best_Camera", "剪輯規則", "剪輯原因"],
                    ["00:00.000", "10:00.000", "CAM1", "Rule1", "Part2 start"],
                    ["10:00.000", "20:17.600", "CAM2", "Rule2", "Closing"],
                ],
            },
        ]
        merged = shift_and_merge_chunk_edl_rows(chunk_results)
        self.assertEqual(len(merged), 5)
        # Boundary between Part 1 last cut and Part 2 first cut must be stitched at 32:51.900
        self.assertEqual(merged[2][1], "32:51.900")
        self.assertEqual(merged[3][0], "32:51.900")
        self.assertEqual(merged[3][1], "42:51.900")
        self.assertEqual(merged[4][0], "42:51.900")
        self.assertEqual(merged[4][1], "53:09.500")

    @patch("modules.video_composer.subprocess.run")
    def test_cut_single_clip_zero_offset_stream_copy_and_hwaccel(self, mock_run):
        from unittest.mock import MagicMock
        from modules.video_composer import cut_single_clip
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        # Case 1: start_sec == 0.0 -> automatic lossless stream copy (-c copy)
        cut_single_clip("cam1.mp4", "cam1_synced.mp4", start_sec=0.0, end_sec=600.0, copy_codec=False)
        cmd_zero = mock_run.call_args_list[-1][0][0]
        self.assertIn("-c", cmd_zero)
        self.assertIn("copy", cmd_zero)
        self.assertNotIn("-hwaccel", cmd_zero)

        # Case 2: start_sec > 0.001 -> hardware decode (-hwaccel videotoolbox) + re-encode (h264_videotoolbox) + 1s GOP (-g 30)
        cut_single_clip("cam2.mp4", "cam2_synced.mp4", start_sec=4.32, end_sec=604.32, copy_codec=False)
        cmd_offset = mock_run.call_args_list[-1][0][0]
        self.assertIn("-hwaccel", cmd_offset)
        self.assertIn("videotoolbox", cmd_offset)
        self.assertIn("h264_videotoolbox", cmd_offset)
        self.assertIn("-g", cmd_offset)
        self.assertIn("30", cmd_offset)

    def test_resolve_thinking_level(self):
        from generate_edl import resolve_thinking_level as edl_resolve
        from modules.llm_client import resolve_thinking_level as llm_resolve

        self.assertIs(edl_resolve, llm_resolve)
        self.assertEqual(edl_resolve(0), "low")
        self.assertEqual(edl_resolve(1024), "low")
        self.assertEqual(edl_resolve(2048), "low")
        self.assertEqual(edl_resolve(2049), "medium")
        self.assertEqual(edl_resolve(3000), "medium")
        self.assertEqual(edl_resolve(4096), "medium")
        self.assertEqual(edl_resolve(4097), "high")
        self.assertEqual(edl_resolve(8192), "high")
        self.assertEqual(edl_resolve(None), "high")

    def test_generate_edl_content_standard_gemini_4_hydrogen_and_legacy_config(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from generate_edl import generate_edl_content_standard

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(parts=[SimpleNamespace(thought=False, text="00:00.000,00:05.000,CAM1,R,C")])
                )
            ],
            usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5, total_token_count=15),
        )

        # Default model is gemini-4-hydrogen: omit temperature and map short video budget to 'low'
        generate_edl_content_standard(
            "gs://my-bucket/raw/merged.mp4",
            "prompt",
            client=mock_client,
            video_duration_sec=300.0,
            use_ticker=False,
        )
        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "gemini-4-hydrogen")
        cfg = call_kwargs["config"]
        self.assertIsNone(cfg.temperature)
        self.assertEqual(cfg.max_output_tokens, 65536)
        self.assertIsNone(cfg.thinking_config.thinking_budget)
        self.assertTrue(str(cfg.thinking_config.thinking_level).lower().endswith("low"))

        # Long chunk (32 mins -> 4096 budget): map to 'medium'
        generate_edl_content_standard(
            "gs://my-bucket/raw/merged.mp4",
            "prompt",
            client=mock_client,
            video_duration_sec=1920.0,
            use_ticker=False,
        )
        cfg_med = mock_client.models.generate_content.call_args.kwargs["config"]
        self.assertIsNone(cfg_med.temperature)
        self.assertTrue(str(cfg_med.thinking_config.thinking_level).lower().endswith("medium"))

        # Legacy non-Gemini-4 override: preserve temperature and thinking_budget
        generate_edl_content_standard(
            "gs://my-bucket/raw/merged.mp4",
            "prompt",
            client=mock_client,
            model="gemini-2.5-flash",
            thinking_budget=2048,
            use_ticker=False,
        )
        cfg_legacy = mock_client.models.generate_content.call_args.kwargs["config"]
        self.assertEqual(cfg_legacy.temperature, 0.1)
        self.assertEqual(cfg_legacy.thinking_config.thinking_budget, 2048)
        self.assertIsNone(cfg_legacy.thinking_config.thinking_level)

    def test_call_agentic_video_edl_fallback_gemini_4_hydrogen_config(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from generate_edl import call_agentic_video_edl

        mock_client = MagicMock()
        mock_client.interactions.create.side_effect = RuntimeError("interactions endpoint unavailable")
        mock_client.models.generate_content.return_value = SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(parts=[SimpleNamespace(thought=False, text="00:00.000,00:05.000,CAM1,R,C")])
                )
            ],
            usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5, total_token_count=15),
        )

        call_agentic_video_edl(
            "gs://my-bucket/raw/merged.mp4",
            "prompt",
            client=mock_client,
        )
        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "gemini-4-hydrogen")
        cfg = call_kwargs["config"]
        self.assertIsNone(cfg.temperature)
        self.assertEqual(cfg.max_output_tokens, 65536)
        self.assertIsNone(cfg.thinking_config.thinking_budget)
        self.assertTrue(str(cfg.thinking_config.thinking_level).lower().endswith("medium"))

    @patch("modules.llm_client.get_vertex_client")
    @patch("modules.llm_client.resolve_gcp_config")
    def test_call_vertex_generate_content_gemini_4_hydrogen_config(self, mock_cfg, mock_get_client):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from modules.llm_client import call_vertex_generate_content

        mock_cfg.return_value = {
            "project": "demo-proj",
            "location": "global",
            "region": "us-central1",
            "bucket": "multicam-video-demo-proj",
        }
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = SimpleNamespace(text="ok")
        mock_get_client.return_value = mock_client

        # Default Gemini 4 Hydrogen call without thinking_budget
        out = call_vertex_generate_content("hello")
        self.assertEqual(out, "ok")
        kwargs_default = mock_client.models.generate_content.call_args.kwargs
        self.assertEqual(kwargs_default["model"], "gemini-4-hydrogen")
        self.assertEqual(kwargs_default["config"].max_output_tokens, 65536)
        self.assertIsNone(kwargs_default["config"].temperature)
        self.assertIsNone(kwargs_default["config"].thinking_config)

        # thinking_budget=0 maps to 'low'
        call_vertex_generate_content("hello", thinking_budget=0)
        cfg_zero = mock_client.models.generate_content.call_args.kwargs["config"]
        self.assertIsNone(cfg_zero.temperature)
        self.assertTrue(str(cfg_zero.thinking_config.thinking_level).lower().endswith("low"))

        # thinking_budget=8192 maps to 'high'
        call_vertex_generate_content("hello", thinking_budget=8192)
        cfg_high = mock_client.models.generate_content.call_args.kwargs["config"]
        self.assertIsNone(cfg_high.temperature)
        self.assertTrue(str(cfg_high.thinking_config.thinking_level).lower().endswith("high"))


if __name__ == "__main__":
    unittest.main()


