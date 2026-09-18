"""
EDL Semantic Validator Module (edl_validator.py).
Performs deterministic semantic validation on Edit Decision Lists (EDL) before rendering
or downstream NLE timeline export (Final Cut Pro 7 XML).
"""

import csv
from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple, Union
import unicodedata

SEVERITY_ERROR = "ERROR"
SEVERITY_WARN = "WARN"

HEADER_START_KEYS = ("start_time", "start", "in", "start_sec", "in_point", "from")
HEADER_END_KEYS = ("end_time", "end", "out", "end_sec", "out_point", "to")
HEADER_CAM_KEYS = ("best_camera", "camera", "cam", "source", "source_file", "clip", "angle", "source_camera")
HEADER_RULE_KEYS = ("剪輯規則", "rule", "rules", "rule_type")
HEADER_REASON_KEYS = ("剪輯原因", "reason", "reasons", "notes", "description", "label", "comment")


def parse_edl_time_to_seconds(t_val: Any) -> float:
    """
    Parse time strings (MM:SS.mmm, HH:MM:SS.mmm, or float seconds) to float seconds.
    Examples:
      - 00:38.500 -> 38.5
      - 02:21.000 -> 141.0
      - 01:04:27.371 -> 3867.371
    """
    if t_val is None:
        return 0.0
    if isinstance(t_val, (int, float)):
        return float(t_val)
    t_str = str(t_val).strip().replace('"', '').replace("'", "")
    if not t_str:
        return 0.0
    try:
        return float(t_str)
    except ValueError:
        pass

    parts = t_str.split(":")
    try:
        if len(parts) == 3:
            h = float(parts[0])
            m = float(parts[1])
            s = float(parts[2])
            return h * 3600 + m * 60 + s
        elif len(parts) == 2:
            m = float(parts[0])
            s = float(parts[1])
            return m * 60 + s
        elif len(parts) == 1:
            return float(parts[0])
    except ValueError:
        raise ValueError(f"Unable to parse time format: '{t_val}'")
    raise ValueError(f"Unsupported time string: '{t_val}'")


@dataclass
class EDLIssue:
    severity: str
    code: str
    row: Optional[int]
    message: str


@dataclass
class EDLValidationResult:
    issues: List[EDLIssue]
    shot_count: int
    cameras_used: Dict[str, int]

    @property
    def has_error(self) -> bool:
        return any(i.severity == SEVERITY_ERROR for i in self.issues)

    @property
    def has_warning(self) -> bool:
        return any(i.severity == SEVERITY_WARN for i in self.issues)


DEFAULT_LANG = "en"

_MESSAGES: Dict[str, Dict[str, str]] = {
    "zh-TW": {
        "report_title": "🔍 EDL 語意驗證報告",
        "shot_count": "總鏡頭數  : {count}",
        "camera_dist": "相機分佈  : {cam_str}",
        "cam_none": "無",
        "status_pass": "驗證狀態  : ✅ 通過 (無錯誤與警告)",
        "status_warn": "驗證狀態  : ⚠️ 通過 (0 錯誤, {warn_count} 警告)",
        "status_fail": "驗證狀態  : ❌ 失敗 ({error_count} 錯誤, {warn_count} 警告)",
        "no_issues": "✓ 未發現任何語意問題。",
        "col_severity": "層級",
        "col_code": "代碼",
        "col_row": "列號",
        "col_details": "詳細說明",
        "row_global": "全域",
        "no_rows_empty": "EDL 沒有任何資料列",
        "no_rows_invalid": "EDL 沒有任何有效資料列",
        "no_rows_header_only": "EDL 僅包含標題列，無任何鏡頭資料列",
        "file_not_found": "EDL 檔案不存在: {path}",
        "empty_camera": "第 {idx} 列相機 (camera) 欄位為空",
        "unknown_camera_list": "第 {idx} 列相機 '{cam}' 不在已知相機列表 ({known})",
        "unknown_camera_regex": "第 {idx} 列相機 '{cam}' 不符合預設相機命名格式 (^CAM\\d+$)",
        "time_empty": "第 {idx} 列時間碼為空或缺失 (start='{start}', end='{end}')",
        "time_unparseable": "第 {idx} 列時間碼無法解析 (start='{start}', end='{end}'): {err}",
        "negative_duration": "第 {idx} 列鏡頭長度非正值 (start={start:.3f}, end={end:.3f}, duration={duration:.3f}s <= 0)",
        "non_monotonic": "第 {idx} 列開始時間倒退 (前列 start={prev_start:.3f}, 本列 start={start:.3f})",
        "overlap": "第 {idx} 列與前列重疊 (前列 end={prev_end:.3f}, 本列 start={start:.3f}, 重疊={overlap:.3f}s)",
        "gap": "第 {idx} 列與前列存在時間空隙 (前列 end={prev_end:.3f}, 本列 start={start:.3f}, 間隔={gap:.3f}s > 門檻={threshold:.3f}s)",
    },
    "en": {
        "report_title": "🔍 EDL Semantic Validation Report",
        "shot_count": "Shot Count   : {count}",
        "camera_dist": "Camera Dist  : {cam_str}",
        "cam_none": "None",
        "status_pass": "Status       : ✅ PASSED (0 errors, 0 warnings)",
        "status_warn": "Status       : ⚠️ PASSED (0 errors, {warn_count} warnings)",
        "status_fail": "Status       : ❌ FAILED ({error_count} errors, {warn_count} warnings)",
        "no_issues": "✓ No semantic issues found.",
        "col_severity": "Severity",
        "col_code": "Code",
        "col_row": "Row",
        "col_details": "Details",
        "row_global": "Global",
        "no_rows_empty": "EDL does not contain any data rows",
        "no_rows_invalid": "EDL does not contain any valid data rows",
        "no_rows_header_only": "EDL contains only header row, no shot cut rows",
        "file_not_found": "EDL file not found: {path}",
        "empty_camera": "Row {idx}: camera field is empty",
        "unknown_camera_list": "Row {idx}: camera '{cam}' is not in known camera list ({known})",
        "unknown_camera_regex": "Row {idx}: camera '{cam}' does not match default camera naming format (^CAM\\d+$)",
        "time_empty": "Row {idx}: timecode is empty or missing (start='{start}', end='{end}')",
        "time_unparseable": "Row {idx}: unable to parse timecode (start='{start}', end='{end}'): {err}",
        "negative_duration": "Row {idx}: shot duration is non-positive (start={start:.3f}, end={end:.3f}, duration={duration:.3f}s <= 0)",
        "non_monotonic": "Row {idx}: start time goes backward (prev start={prev_start:.3f}, start={start:.3f})",
        "overlap": "Row {idx}: overlaps previous row (prev end={prev_end:.3f}, start={start:.3f}, overlap={overlap:.3f}s)",
        "gap": "Row {idx}: time gap with previous row (prev end={prev_end:.3f}, start={start:.3f}, gap={gap:.3f}s > threshold={threshold:.3f}s)",
    },
}


def normalize_lang(lang: Optional[str]) -> str:
    """
    Normalize language code string to supported language ('en' or 'zh-TW').
    Accepts arbitrary string without throwing or warning:
      - 'zh-Hant', 'zh_TW', 'zh', 'zh-tw', 'ZH-TW' -> 'zh-TW'
      - 'en-US', 'en_GB', 'EN' -> 'en'
      - Unsupported / empty / None -> silently falls back to DEFAULT_LANG ('en')
    """
    if not lang or not isinstance(lang, str):
        return DEFAULT_LANG
    clean = lang.strip().lower().replace("_", "-")
    if clean.startswith("zh"):
        return "zh-TW"
    if clean.startswith("en"):
        return "en"
    return DEFAULT_LANG


def _t(lang: Optional[str], key: str, **kwargs) -> str:
    """Look up message and format; fallbacks to DEFAULT_LANG then key itself."""
    norm = normalize_lang(lang)
    catalog = _MESSAGES.get(norm) or _MESSAGES[DEFAULT_LANG]
    template = catalog.get(key) or _MESSAGES[DEFAULT_LANG].get(key, key)
    if kwargs:
        return template.format(**kwargs)
    return template


def _display_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in str(s))


def _pad_display(s: str, target_width: int, align: str = "<") -> str:
    s_str = str(s)
    dw = _display_width(s_str)
    pad = max(0, target_width - dw)
    if align == ">":
        return " " * pad + s_str
    elif align == "^":
        pad_l = pad // 2
        pad_r = pad - pad_l
        return " " * pad_l + s_str + " " * pad_r
    else:  # "<"
        return s_str + " " * pad


def _format_known_cameras_for_display(known_cameras: Optional[List[str]]) -> str:
    """
    Format known cameras list for human-readable display in validation issues.
    1. Filters canonical ^CAM\\d+$ names (case-sensitive CAM + digits).
    2. Deduplicates and sorts numerically by camera index (e.g. CAM2 before CAM10).
    3. If canonical filter is empty (e.g. ['AngleA', 'AngleB']),
       falls back to deduplicated original names.
    """
    if not known_cameras:
        return ""
    canonical = []
    seen = set()
    for c in known_cameras:
        c_str = str(c).strip()
        m = re.match(r"^CAM(\d+)$", c_str)
        if m and c_str not in seen:
            seen.add(c_str)
            canonical.append((int(m.group(1)), c_str))

    if canonical:
        canonical.sort(key=lambda x: x[0])
        return ", ".join(c for _, c in canonical)

    seen_raw = set()
    deduped = []
    for c in known_cameras:
        c_str = str(c).strip()
        if c_str not in seen_raw:
            seen_raw.add(c_str)
            deduped.append(c_str)
    return ", ".join(deduped)



def validate_edl_rows(
    rows: List[List[str]],
    *,
    known_cameras: Optional[List[str]] = None,
    max_gap_sec: float = 0.05,
    lang: str = DEFAULT_LANG,
) -> EDLValidationResult:
    """
    Validate raw EDL rows (including header) against semantic editing constraints.
    """
    norm_lang = normalize_lang(lang)
    if not rows:
        return EDLValidationResult(
            issues=[EDLIssue(severity=SEVERITY_ERROR, code="E_NO_ROWS", row=None, message=_t(norm_lang, "no_rows_empty"))],
            shot_count=0,
            cameras_used={},
        )

    raw_rows = [r for r in rows if r and any(str(c).strip() for c in r)]
    if not raw_rows:
        return EDLValidationResult(
            issues=[EDLIssue(severity=SEVERITY_ERROR, code="E_NO_ROWS", row=None, message=_t(norm_lang, "no_rows_invalid"))],
            shot_count=0,
            cameras_used={},
        )

    # Detect header
    header = [str(h).strip().lower() for h in raw_rows[0]]
    start_col = -1
    end_col = -1
    cam_col = -1
    rule_col = -1
    reason_col = -1

    for i, col_name in enumerate(header):
        if col_name in HEADER_START_KEYS:
            start_col = i
        elif col_name in HEADER_END_KEYS:
            end_col = i
        elif col_name in HEADER_CAM_KEYS:
            cam_col = i
        elif col_name in HEADER_RULE_KEYS:
            rule_col = i
        elif col_name in HEADER_REASON_KEYS:
            reason_col = i

    has_header = (start_col != -1 and end_col != -1) or (start_col != -1 and cam_col != -1)
    data_rows = raw_rows[1:] if has_header else raw_rows

    if not has_header:
        start_col = 0
        end_col = 1
        cam_col = 2
        rule_col = 3 if len(raw_rows[0]) > 3 else -1
        reason_col = 4 if len(raw_rows[0]) > 4 else -1

    if not data_rows:
        return EDLValidationResult(
            issues=[EDLIssue(severity=SEVERITY_ERROR, code="E_NO_ROWS", row=None, message=_t(norm_lang, "no_rows_header_only"))],
            shot_count=0,
            cameras_used={},
        )

    issues: List[EDLIssue] = []
    cameras_used: Dict[str, int] = {}
    shot_count = len(data_rows)
    prev_valid_time: Optional[Tuple[float, float]] = None
    known_display = _format_known_cameras_for_display(known_cameras) if known_cameras is not None else ""

    for idx, row in enumerate(data_rows, start=1):
        # 1. Camera validation
        cam_raw = row[cam_col] if cam_col != -1 and cam_col < len(row) else ""
        cam_clean = str(cam_raw).strip()

        if not cam_clean:
            issues.append(EDLIssue(
                severity=SEVERITY_ERROR,
                code="E_EMPTY_CAMERA",
                row=idx,
                message=_t(norm_lang, "empty_camera", idx=idx)
            ))
        else:
            cameras_used[cam_clean] = cameras_used.get(cam_clean, 0) + 1
            if known_cameras is not None:
                known_set = set(k.strip() for k in known_cameras)
                known_set_upper = set(k.strip().upper() for k in known_cameras)
                if cam_clean not in known_set and cam_clean.upper() not in known_set_upper:
                    issues.append(EDLIssue(
                        severity=SEVERITY_WARN,
                        code="W_UNKNOWN_CAMERA",
                        row=idx,
                        message=_t(norm_lang, "unknown_camera_list", idx=idx, cam=cam_clean, known=known_display)
                    ))
            else:
                if not re.match(r"^CAM\d+$", cam_clean, re.IGNORECASE):
                    issues.append(EDLIssue(
                        severity=SEVERITY_WARN,
                        code="W_UNKNOWN_CAMERA",
                        row=idx,
                        message=_t(norm_lang, "unknown_camera_regex", idx=idx, cam=cam_clean)
                    ))

        # 2. Time parsing validation
        start_raw = row[start_col] if start_col != -1 and start_col < len(row) else ""
        end_raw = row[end_col] if end_col != -1 and end_col < len(row) else ""
        start_str = str(start_raw).strip().replace('"', '').replace("'", "")
        end_str = str(end_raw).strip().replace('"', '').replace("'", "")

        time_parsed = False
        start_s = 0.0
        end_s = 0.0

        if not start_str or not end_str:
            issues.append(EDLIssue(
                severity=SEVERITY_ERROR,
                code="E_PARSE_TIME",
                row=idx,
                message=_t(norm_lang, "time_empty", idx=idx, start=start_raw, end=end_raw)
            ))
        else:
            try:
                start_s = parse_edl_time_to_seconds(start_str)
                end_s = parse_edl_time_to_seconds(end_str)
                time_parsed = True
            except Exception as e:
                issues.append(EDLIssue(
                    severity=SEVERITY_ERROR,
                    code="E_PARSE_TIME",
                    row=idx,
                    message=_t(norm_lang, "time_unparseable", idx=idx, start=start_str, end=end_str, err=e)
                ))

        if not time_parsed:
            continue

        # 3. Negative / zero duration validation: end <= start
        if end_s <= start_s + 1e-6:
            issues.append(EDLIssue(
                severity=SEVERITY_ERROR,
                code="E_NEGATIVE_DURATION",
                row=idx,
                message=_t(norm_lang, "negative_duration", idx=idx, start=start_s, end=end_s, duration=end_s - start_s)
            ))

        # 4. Sequence-level checks (monotonicity, overlap, gap)
        if prev_valid_time is not None:
            prev_start_s, prev_end_s = prev_valid_time

            # E_NON_MONOTONIC: start < prev_start
            if start_s < prev_start_s - 1e-4:
                issues.append(EDLIssue(
                    severity=SEVERITY_ERROR,
                    code="E_NON_MONOTONIC",
                    row=idx,
                    message=_t(norm_lang, "non_monotonic", idx=idx, prev_start=prev_start_s, start=start_s)
                ))

            # E_OVERLAP: start < prev_end
            if start_s < prev_end_s - 1e-4:
                issues.append(EDLIssue(
                    severity=SEVERITY_ERROR,
                    code="E_OVERLAP",
                    row=idx,
                    message=_t(norm_lang, "overlap", idx=idx, prev_end=prev_end_s, start=start_s, overlap=prev_end_s - start_s)
                ))

            # W_GAP: start - prev_end > max_gap_sec
            gap = start_s - prev_end_s
            if gap > max_gap_sec + 1e-4:
                issues.append(EDLIssue(
                    severity=SEVERITY_WARN,
                    code="W_GAP",
                    row=idx,
                    message=_t(norm_lang, "gap", idx=idx, prev_end=prev_end_s, start=start_s, gap=gap, threshold=max_gap_sec)
                ))

        prev_valid_time = (start_s, end_s)

    return EDLValidationResult(
        issues=issues,
        shot_count=shot_count,
        cameras_used=cameras_used,
    )


def validate_edl_file(
    csv_path: Union[str, Path],
    *,
    known_cameras: Optional[List[str]] = None,
    max_gap_sec: float = 0.05,
    lang: str = DEFAULT_LANG,
) -> EDLValidationResult:
    """
    Validate an EDL CSV file on disk.
    """
    csv_path_str = str(csv_path)
    norm_lang = normalize_lang(lang)
    if not os.path.exists(csv_path_str):
        return EDLValidationResult(
            issues=[EDLIssue(severity=SEVERITY_ERROR, code="E_NO_ROWS", row=None, message=_t(norm_lang, "file_not_found", path=csv_path_str))],
            shot_count=0,
            cameras_used={},
        )
    with open(csv_path_str, "r", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        delimiter = "\t" if "\t" in sample and "," not in sample else ","
        reader = csv.reader(f, delimiter=delimiter)
        raw_rows = [row for row in reader if row and any(field.strip() for field in row)]

    return validate_edl_rows(raw_rows, known_cameras=known_cameras, max_gap_sec=max_gap_sec, lang=lang)


def format_validation_report(result: EDLValidationResult, lang: str = DEFAULT_LANG) -> str:
    """
    Format EDL validation result into a human-readable multi-line terminal report.
    """
    norm_lang = normalize_lang(lang)
    lines = []
    lines.append("-" * 78)
    lines.append(_t(norm_lang, "report_title"))
    lines.append("-" * 78)
    lines.append(_t(norm_lang, "shot_count", count=result.shot_count))
    if result.cameras_used:
        cam_str = ", ".join(f"{k}: {v}" for k, v in sorted(result.cameras_used.items()))
    else:
        cam_str = _t(norm_lang, "cam_none")
    lines.append(_t(norm_lang, "camera_dist", cam_str=cam_str))
    error_count = sum(1 for i in result.issues if i.severity == SEVERITY_ERROR)
    warn_count = sum(1 for i in result.issues if i.severity == SEVERITY_WARN)
    if error_count == 0 and warn_count == 0:
        lines.append(_t(norm_lang, "status_pass"))
    elif error_count == 0:
        lines.append(_t(norm_lang, "status_warn", warn_count=warn_count))
    else:
        lines.append(_t(norm_lang, "status_fail", error_count=error_count, warn_count=warn_count))
    lines.append("-" * 78)

    if not result.issues:
        lines.append(_t(norm_lang, "no_issues"))
    else:
        col_sev = _pad_display(_t(norm_lang, "col_severity"), 8)
        col_code = _pad_display(_t(norm_lang, "col_code"), 20)
        col_row = _pad_display(_t(norm_lang, "col_row"), 6)
        col_det = _t(norm_lang, "col_details")
        lines.append(f"{col_sev} | {col_code} | {col_row} | {col_det}")
        lines.append("-" * 78)
        for issue in result.issues:
            row_str = f"L{issue.row}" if issue.row is not None else _t(norm_lang, "row_global")
            c_sev = _pad_display(issue.severity, 8)
            c_code = _pad_display(issue.code, 20)
            c_row = _pad_display(row_str, 6)
            lines.append(f"{c_sev} | {c_code} | {c_row} | {issue.message}")
    lines.append("-" * 78)
    return "\n".join(lines)
