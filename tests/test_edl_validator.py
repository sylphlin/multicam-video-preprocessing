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
    DEFAULT_LANG,
    EDLIssue,
    EDLValidationResult,
    parse_edl_time_to_seconds,
    validate_edl_rows,
    validate_edl_file,
    format_validation_report,
    normalize_lang,
    get_report_section_heading,
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
        result = validate_edl_rows(rows, known_cameras=["CAM1"], lang="zh-TW")
        report = format_validation_report(result, lang="zh-TW")
        self.assertIn("🔍 EDL 語意驗證報告", report)
        self.assertIn("E_NEGATIVE_DURATION", report)
        self.assertIn("W_UNKNOWN_CAMERA", report)
        self.assertIn("❌ 失敗", report)

    def test_i18n_messages_differ_by_language(self):
        """The same invalid EDL produces different message strings under zh-TW vs en."""
        rows = [
            ["Start", "End", "Camera"],
            ["00:10.000", "00:05.000", "CAM1"],
            ["00:05.000", "00:15.000", "CAM99"],
        ]
        result_zh = validate_edl_rows(rows, known_cameras=["CAM1"], lang="zh-TW")
        result_en = validate_edl_rows(rows, known_cameras=["CAM1"], lang="en")

        self.assertEqual(len(result_zh.issues), len(result_en.issues))
        for izh, ien in zip(result_zh.issues, result_en.issues):
            self.assertEqual(izh.code, ien.code)
            self.assertEqual(izh.row, ien.row)
            self.assertEqual(izh.severity, ien.severity)
            self.assertNotEqual(izh.message, ien.message)

        report_zh = format_validation_report(result_zh, lang="zh-TW")
        report_en = format_validation_report(result_en, lang="en")
        self.assertIn("🔍 EDL 語意驗證報告", report_zh)
        self.assertIn("🔍 EDL Semantic Validation Report", report_en)
        self.assertIn("❌ 失敗", report_zh)
        self.assertIn("❌ FAILED", report_en)

    def test_i18n_codes_and_status_identical(self):
        """Issue codes, row numbers, has_error, and has_warning are 100% identical regardless of language."""
        rows = [
            ["Start", "End", "Camera"],
            ["00:00.000", "00:10.000", "CAM1"],
            ["00:09.500", "00:20.000", "CAM2"],  # overlap
            ["00:20.500", "00:30.000", "CAM3"],  # gap
        ]
        r_zh = validate_edl_rows(rows, lang="zh-TW")
        r_en = validate_edl_rows(rows, lang="en")

        self.assertEqual(r_zh.has_error, r_en.has_error)
        self.assertEqual(r_zh.has_warning, r_en.has_warning)
        self.assertEqual(r_zh.shot_count, r_en.shot_count)
        self.assertEqual(r_zh.cameras_used, r_en.cameras_used)
        self.assertEqual([i.code for i in r_zh.issues], [i.code for i in r_en.issues])
        self.assertEqual([i.row for i in r_zh.issues], [i.row for i in r_en.issues])

    def test_i18n_default_lang(self):
        """When lang is omitted, DEFAULT_LANG ('en') is used."""
        self.assertEqual(DEFAULT_LANG, "en")
        rows = [
            ["Start", "End", "Camera"],
            ["00:10.000", "00:05.000", "CAM1"],
        ]
        result_default = validate_edl_rows(rows)
        result_en = validate_edl_rows(rows, lang="en")
        self.assertEqual(result_default.issues[0].message, result_en.issues[0].message)

        report_default = format_validation_report(result_default)
        self.assertIn("🔍 EDL Semantic Validation Report", report_default)

    def test_i18n_unsupported_lang_fallback(self):
        """Unsupported language codes (e.g. 'fr') silently fallback to en without exception."""
        rows = [
            ["Start", "End", "Camera"],
            ["00:10.000", "00:05.000", "CAM1"],
        ]
        result_fr = validate_edl_rows(rows, lang="fr")
        result_en = validate_edl_rows(rows, lang="en")
        self.assertEqual(result_fr.issues[0].message, result_en.issues[0].message)

        report_fr = format_validation_report(result_fr, lang="fr")
        self.assertIn("🔍 EDL Semantic Validation Report", report_fr)

    def test_i18n_variant_normalization(self):
        """Variants zh_TW, zh-Hant, zh, ZH-TW normalize to zh-TW; en-US, en_GB, EN normalize to en."""
        for v in ["zh_TW", "zh-Hant", "zh", "ZH-TW", "zh-tw"]:
            self.assertEqual(normalize_lang(v), "zh-TW")
            r = validate_edl_rows([["Start", "End", "Camera"], ["00:10.000", "00:05.000", "CAM1"]], lang=v)
            self.assertIn("第 1 列", r.issues[0].message)

        for v in ["en-US", "en_GB", "EN", "en"]:
            self.assertEqual(normalize_lang(v), "en")
            r = validate_edl_rows([["Start", "End", "Camera"], ["00:10.000", "00:05.000", "CAM1"]], lang=v)
            self.assertIn("Row 1", r.issues[0].message)

    def test_known_cameras_file_validation(self):
        """Part A: validate_edl_file with known_cameras triggers W_UNKNOWN_CAMERA for CAM7, but not when omitted."""
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".csv", delete=False) as f:
            f.write("Start,End,Camera\n")
            f.write("00:00.000,00:05.000,CAM7\n")
            tmp_path = f.name
        try:
            # Without known_cameras: matches default regex ^CAM\d+$, no warning
            res_no_whitelist = validate_edl_file(tmp_path)
            self.assertFalse(res_no_whitelist.has_warning)
            self.assertEqual(len(res_no_whitelist.issues), 0)

            # With known_cameras=['CAM1', 'CAM2']: triggers W_UNKNOWN_CAMERA
            res_whitelist = validate_edl_file(tmp_path, known_cameras=["CAM1", "CAM2"])
            self.assertTrue(res_whitelist.has_warning)
            codes = [i.code for i in res_whitelist.issues]
            self.assertIn("W_UNKNOWN_CAMERA", codes)
        finally:
            os.remove(tmp_path)


    def test_known_cameras_display_filtering_aliases(self):
        """Aliases (cam1, CAM_1, C1) should be filtered out from display; only (CAM1, CAM2) shown."""
        known_cams = ["CAM1", "cam1", "CAM_1", "C1", "CAM2", "cam2", "CAM_2", "C2"]
        rows = [
            ["Start", "End", "Camera"],
            ["00:00.000", "00:05.000", "CAM7"],
        ]
        result = validate_edl_rows(rows, known_cameras=known_cams, lang="en")
        self.assertTrue(result.has_warning)
        msg = result.issues[0].message
        self.assertIn("(CAM1, CAM2)", msg)
        self.assertNotIn("cam1", msg)
        self.assertNotIn("CAM_1", msg)
        self.assertNotIn("C1", msg)
        self.assertNotIn("cam2", msg)
        self.assertNotIn("CAM_2", msg)
        self.assertNotIn("C2", msg)

    def test_known_cameras_display_numeric_sort(self):
        """Known cameras display should sort numerically: CAM1, CAM2, CAM10 (not alphabetical CAM1, CAM10, CAM2)."""
        known_cams = ["CAM10", "CAM1", "CAM2"]
        rows = [
            ["Start", "End", "Camera"],
            ["00:00.000", "00:05.000", "CAM7"],
        ]
        result = validate_edl_rows(rows, known_cameras=known_cams, lang="en")
        self.assertTrue(result.has_warning)
        msg = result.issues[0].message
        self.assertIn("(CAM1, CAM2, CAM10)", msg)

    def test_known_cameras_display_non_canonical_fallback(self):
        """When all known cameras are non-standard (e.g. AngleA, AngleB), fallback to deduplicated original list."""
        known_cams = ["AngleA", "AngleB", "AngleA"]
        rows = [
            ["Start", "End", "Camera"],
            ["00:00.000", "00:05.000", "AngleC"],
        ]
        result = validate_edl_rows(rows, known_cameras=known_cams, lang="en")
        self.assertTrue(result.has_warning)
        msg = result.issues[0].message
        self.assertIn("(AngleA, AngleB)", msg)

    def test_known_cameras_alias_matching_behavior_preserved(self):
        """Matching logic still checks all aliases; using alias like CAM_1 does NOT trigger warning."""
        known_cams = ["CAM1", "cam1", "CAM_1", "C1", "CAM2", "cam2", "CAM_2", "C2"]
        rows = [
            ["Start", "End", "Camera"],
            ["00:00.000", "00:05.000", "CAM_1"],
            ["00:05.000", "00:10.000", "cam2"],
        ]
        result = validate_edl_rows(rows, known_cameras=known_cams)
        self.assertFalse(result.has_warning)
        self.assertFalse(result.has_error)

    # ------------------------------------------------------------------
    # Localized Markdown section heading (embedded into edl_full_report.md)
    # ------------------------------------------------------------------

    def test_report_section_heading_localized(self):
        """Section heading follows --lang instead of being hardcoded."""
        self.assertEqual(
            get_report_section_heading("en"), "🔍 EDL Validation Result"
        )
        self.assertEqual(
            get_report_section_heading("zh-TW"), "🔍 EDL 驗證結果"
        )

    def test_report_section_heading_accepts_locale_aliases(self):
        """Locale aliases normalize the same way as normalize_lang()."""
        for alias in ("zh_TW", "zh-Hant", "ZH-TW", "zh-tw", "zh"):
            self.assertEqual(
                get_report_section_heading(alias),
                "🔍 EDL 驗證結果",
                msg=f"alias {alias!r} should map to zh-TW",
            )
        for alias in ("en-US", "en_GB", "EN"):
            self.assertEqual(
                get_report_section_heading(alias), "🔍 EDL Validation Result"
            )

    def test_report_section_heading_falls_back_silently(self):
        """Unsupported / empty locales fall back to DEFAULT_LANG without raising."""
        expected = get_report_section_heading(DEFAULT_LANG)
        for bad in (None, "", "   ", "fr", "xyz123", 123):
            self.assertEqual(get_report_section_heading(bad), expected)

    def test_report_section_heading_matches_body_language(self):
        """Heading and report body must not disagree on language."""
        rows = [
            ["Start", "End", "Camera"],
            ["00:00.000", "00:05.000", "CAM1"],
        ]
        result = validate_edl_rows(rows)

        zh_body = format_validation_report(result, lang="zh-TW")
        self.assertIn("驗證", get_report_section_heading("zh-TW"))
        self.assertIn("驗證", zh_body)

        en_body = format_validation_report(result, lang="en")
        self.assertIn("Validation", get_report_section_heading("en"))
        self.assertIn("Validation", en_body)


if __name__ == "__main__":
    unittest.main()
