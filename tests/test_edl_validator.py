"""
Unit tests for EDL Semantic Validator (tests/test_edl_validator.py).
Validates all 8 semantic error and warning rules, CSV compatibility, and report formatting.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add scripts directory to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from modules.edl_validator import (
    SEVERITY_ERROR,
    SEVERITY_WARN,
    EDLIssue,
    EDLValidationResult,
    parse_edl_time_to_seconds,
    validate_edl_rows,
    validate_edl_file,
    format_validation_report,
)


class TestEDLValidator(unittest.TestCase):

    def test_clean_edl(self):
        """Clean EDL with standard columns and contiguous cuts should produce 0 errors and 0 warnings."""
        rows = [
            ["Start", "End", "Camera", "Rule", "Reason"],
            ["00:00.000", "00:10.000", "CAM1", "[一般] 主持人開場", "開場白"],
            ["00:10.000", "00:25.500", "CAM2", "[強制] 來賓回答", "來賓特寫"],
            ["00:25.500", "00:40.000", "CAM1", "[一般] 主持人追問", "雙人全景切換"],
        ]
        result = validate_edl_rows(rows)
        self.assertFalse(result.has_error)
        self.assertFalse(result.has_warning)
        self.assertEqual(result.shot_count, 3)
        self.assertEqual(result.cameras_used, {"CAM1": 2, "CAM2": 1})
        self.assertEqual(len(result.issues), 0)

    def test_negative_duration(self):
        """end <= start should trigger E_NEGATIVE_DURATION."""
        rows = [
            ["Start", "End", "Camera", "Rule", "Reason"],
            ["00:10.000", "00:05.000", "CAM1", "Rule", "Reason"],
        ]
        result = validate_edl_rows(rows)
        self.assertTrue(result.has_error)
        codes = [i.code for i in result.issues]
        self.assertIn("E_NEGATIVE_DURATION", codes)
        self.assertEqual(result.issues[0].row, 1)

    def test_zero_duration(self):
        """end == start should also trigger E_NEGATIVE_DURATION."""
        rows = [
            ["Start", "End", "Camera"],
            ["00:10.000", "00:10.000", "CAM1"],
        ]
        result = validate_edl_rows(rows)
        self.assertTrue(result.has_error)
        codes = [i.code for i in result.issues]
        self.assertIn("E_NEGATIVE_DURATION", codes)

    def test_non_monotonic_time(self):
        """Current start < previous start should trigger E_NON_MONOTONIC."""
        rows = [
            ["Start", "End", "Camera"],
            ["00:10.000", "00:20.000", "CAM1"],
            ["00:08.000", "00:15.000", "CAM2"],
        ]
        result = validate_edl_rows(rows)
        self.assertTrue(result.has_error)
        codes = [i.code for i in result.issues]
        self.assertIn("E_NON_MONOTONIC", codes)

    def test_overlap(self):
        """Current start < previous end by 0.5s should trigger E_OVERLAP."""
        rows = [
            ["Start", "End", "Camera"],
            ["00:00.000", "00:10.000", "CAM1"],
            ["00:09.500", "00:20.000", "CAM2"],
        ]
        result = validate_edl_rows(rows)
        self.assertTrue(result.has_error)
        codes = [i.code for i in result.issues]
        self.assertIn("E_OVERLAP", codes)
        self.assertEqual(result.issues[0].row, 2)

    def test_empty_camera(self):
        """Empty camera column should trigger E_EMPTY_CAMERA."""
        rows = [
            ["Start", "End", "Camera"],
            ["00:00.000", "00:10.000", ""],
            ["00:10.000", "00:20.000", "   "],
        ]
        result = validate_edl_rows(rows)
        self.assertTrue(result.has_error)
        codes = [i.code for i in result.issues]
        self.assertEqual(codes.count("E_EMPTY_CAMERA"), 2)

    def test_no_rows_header_only(self):
        """Only header and no data rows should trigger E_NO_ROWS."""
        rows = [["Start", "End", "Camera", "Rule", "Reason"]]
        result = validate_edl_rows(rows)
        self.assertTrue(result.has_error)
        codes = [i.code for i in result.issues]
        self.assertIn("E_NO_ROWS", codes)
        self.assertEqual(result.shot_count, 0)
        self.assertIsNone(result.issues[0].row)

    def test_no_rows_completely_empty(self):
        """Empty list should trigger E_NO_ROWS."""
        result = validate_edl_rows([])
        self.assertTrue(result.has_error)
        self.assertIn("E_NO_ROWS", [i.code for i in result.issues])
        self.assertEqual(result.shot_count, 0)

    def test_parse_time_error(self):
        """Unparseable timestamp 'ab:cd' should trigger E_PARSE_TIME."""
        rows = [
            ["Start", "End", "Camera"],
            ["ab:cd", "00:10.000", "CAM1"],
        ]
        result = validate_edl_rows(rows)
        self.assertTrue(result.has_error)
        codes = [i.code for i in result.issues]
        self.assertIn("E_PARSE_TIME", codes)
        self.assertEqual(result.issues[0].row, 1)

    def test_unknown_camera_with_whitelist(self):
        """CAM7 not in known_cameras ['CAM1', 'CAM2'] should trigger W_UNKNOWN_CAMERA."""
        rows = [
            ["Start", "End", "Camera"],
            ["00:00.000", "00:10.000", "CAM7"],
        ]
        result = validate_edl_rows(rows, known_cameras=["CAM1", "CAM2"])
        self.assertFalse(result.has_error)
        self.assertTrue(result.has_warning)
        codes = [i.code for i in result.issues]
        self.assertIn("W_UNKNOWN_CAMERA", codes)
        self.assertEqual(result.issues[0].row, 1)

    def test_unknown_camera_default_regex(self):
        """Non-CAM naming when no whitelist is provided triggers W_UNKNOWN_CAMERA."""
        rows = [
            ["Start", "End", "Camera"],
            ["00:00.000", "00:10.000", "AngleB"],
        ]
        result = validate_edl_rows(rows)
        self.assertTrue(result.has_warning)
        self.assertIn("W_UNKNOWN_CAMERA", [i.code for i in result.issues])

        # CAM1 should not trigger warning
        rows_ok = [
            ["Start", "End", "Camera"],
            ["00:00.000", "00:10.000", "CAM1"],
        ]
        result_ok = validate_edl_rows(rows_ok)
        self.assertFalse(result_ok.has_warning)

    def test_gap_warning(self):
        """Gap of 0.4s (> 0.05s) should trigger W_GAP."""
        rows = [
            ["Start", "End", "Camera"],
            ["00:00.000", "00:10.000", "CAM1"],
            ["00:10.400", "00:20.000", "CAM2"],
        ]
        result = validate_edl_rows(rows, max_gap_sec=0.05)
        self.assertFalse(result.has_error)
        self.assertTrue(result.has_warning)
        codes = [i.code for i in result.issues]
        self.assertIn("W_GAP", codes)
        self.assertEqual(result.issues[0].row, 2)

    def test_parameterized_gap_threshold(self):
        """Same 0.4s gap should not trigger W_GAP when max_gap_sec is increased to 1.0."""
        rows = [
            ["Start", "End", "Camera"],
            ["00:00.000", "00:10.000", "CAM1"],
            ["00:10.400", "00:20.000", "CAM2"],
        ]
        result = validate_edl_rows(rows, max_gap_sec=1.0)
        self.assertFalse(result.has_error)
        self.assertFalse(result.has_warning)

    def test_no_header_fallback(self):
        """CSV without header should fallback to [Start, End, Camera, Rule, Reason]."""
        rows = [
            ["00:00.000", "00:10.000", "CAM1", "Rule1", "Reason1"],
            ["00:10.000", "00:20.000", "CAM2", "Rule2", "Reason2"],
        ]
        result = validate_edl_rows(rows)
        self.assertFalse(result.has_error)
        self.assertEqual(result.shot_count, 2)
        self.assertEqual(result.cameras_used, {"CAM1": 1, "CAM2": 1})

    def test_mixed_timecode_formats(self):
        """Mixed MM:SS.mmm and HH:MM:SS.mmm should parse seamlessly."""
        rows = [
            ["Start", "End", "Camera"],
            ["00:00.000", "01:05.500", "CAM1"],
            ["01:05.500", "01:02:10.000", "CAM2"],
            ["3730.000", "3800.5", "CAM1"],
        ]
        result = validate_edl_rows(rows)
        self.assertFalse(result.has_error)
        self.assertEqual(result.shot_count, 3)

    def test_tab_delimited_file(self):
        """Tab-delimited file reading through validate_edl_file."""
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".tsv", delete=False) as f:
            f.write("Start\tEnd\tCamera\tRule\tReason\n")
            f.write("00:00.000\t00:10.000\tCAM1\tRuleA\tReasonA\n")
            f.write("00:10.000\t00:20.000\tCAM2\tRuleB\tReasonB\n")
            tmp_path = f.name
        try:
            result = validate_edl_file(tmp_path)
            self.assertFalse(result.has_error)
            self.assertFalse(result.has_warning)
            self.assertEqual(result.shot_count, 2)
        finally:
            os.remove(tmp_path)

    def test_utf8_sig_bom_file(self):
        """UTF-8 with BOM (utf-8-sig) file reading through validate_edl_file."""
        with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig", suffix=".csv", delete=False) as f:
            f.write("Start,End,Camera\n")
            f.write("00:00.000,00:05.000,CAM1\n")
            tmp_path = f.name
        try:
            result = validate_edl_file(tmp_path)
            self.assertFalse(result.has_error)
            self.assertEqual(result.shot_count, 1)
            self.assertIn("CAM1", result.cameras_used)
        finally:
            os.remove(tmp_path)

    def test_format_validation_report(self):
        """Report formatter produces readable multiline string containing issues."""
        rows = [
            ["Start", "End", "Camera"],
            ["00:10.000", "00:05.000", "CAM1"],
            ["00:05.000", "00:15.000", "CAM99"],
        ]
        result = validate_edl_rows(rows, known_cameras=["CAM1"])
        report = format_validation_report(result)
        self.assertIn("🔍 EDL 語意驗證報告", report)
        self.assertIn("E_NEGATIVE_DURATION", report)
        self.assertIn("W_UNKNOWN_CAMERA", report)
        self.assertIn("❌ 失敗", report)


if __name__ == "__main__":
    unittest.main()
