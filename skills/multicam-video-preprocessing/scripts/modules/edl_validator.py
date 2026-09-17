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


def validate_edl_rows(
    rows: List[List[str]],
    *,
    known_cameras: Optional[List[str]] = None,
    max_gap_sec: float = 0.05,
) -> EDLValidationResult:
    """
    Validate raw EDL rows (including header) against semantic editing constraints.
    """
    if not rows:
        return EDLValidationResult(
            issues=[EDLIssue(severity=SEVERITY_ERROR, code="E_NO_ROWS", row=None, message="EDL 沒有任何資料列")],
            shot_count=0,
            cameras_used={},
        )

    raw_rows = [r for r in rows if r and any(str(c).strip() for c in r)]
    if not raw_rows:
        return EDLValidationResult(
            issues=[EDLIssue(severity=SEVERITY_ERROR, code="E_NO_ROWS", row=None, message="EDL 沒有任何有效資料列")],
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
            issues=[EDLIssue(severity=SEVERITY_ERROR, code="E_NO_ROWS", row=None, message="EDL 僅包含標題列，無任何鏡頭資料列")],
            shot_count=0,
            cameras_used={},
        )

    issues: List[EDLIssue] = []
    cameras_used: Dict[str, int] = {}
    shot_count = len(data_rows)
    prev_valid_time: Optional[Tuple[float, float]] = None

    for idx, row in enumerate(data_rows, start=1):
        # 1. Camera validation
        cam_raw = row[cam_col] if cam_col != -1 and cam_col < len(row) else ""
        cam_clean = str(cam_raw).strip()

        if not cam_clean:
            issues.append(EDLIssue(
                severity=SEVERITY_ERROR,
                code="E_EMPTY_CAMERA",
                row=idx,
                message=f"第 {idx} 列相機 (camera) 欄位為空"
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
                        message=f"第 {idx} 列相機 '{cam_clean}' 不在已知相機列表 ({', '.join(known_cameras)})"
                    ))
            else:
                if not re.match(r"^CAM\d+$", cam_clean, re.IGNORECASE):
                    issues.append(EDLIssue(
                        severity=SEVERITY_WARN,
                        code="W_UNKNOWN_CAMERA",
                        row=idx,
                        message=f"第 {idx} 列相機 '{cam_clean}' 不符合預設相機命名格式 (^CAM\\d+$)"
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
                message=f"第 {idx} 列時間碼為空或缺失 (start='{start_raw}', end='{end_raw}')"
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
                    message=f"第 {idx} 列時間碼無法解析 (start='{start_str}', end='{end_str}'): {e}"
                ))

        if not time_parsed:
            continue

        # 3. Negative / zero duration validation: end <= start
        if end_s <= start_s + 1e-6:
            issues.append(EDLIssue(
                severity=SEVERITY_ERROR,
                code="E_NEGATIVE_DURATION",
                row=idx,
                message=f"第 {idx} 列鏡頭長度非正值 (start={start_s:.3f}, end={end_s:.3f}, duration={end_s - start_s:.3f}s <= 0)"
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
                    message=f"第 {idx} 列開始時間倒退 (前列 start={prev_start_s:.3f}, 本列 start={start_s:.3f})"
                ))

            # E_OVERLAP: start < prev_end
            if start_s < prev_end_s - 1e-4:
                issues.append(EDLIssue(
                    severity=SEVERITY_ERROR,
                    code="E_OVERLAP",
                    row=idx,
                    message=f"第 {idx} 列與前列重疊 (前列 end={prev_end_s:.3f}, 本列 start={start_s:.3f}, 重疊={prev_end_s - start_s:.3f}s)"
                ))

            # W_GAP: start - prev_end > max_gap_sec
            gap = start_s - prev_end_s
            if gap > max_gap_sec + 1e-4:
                issues.append(EDLIssue(
                    severity=SEVERITY_WARN,
                    code="W_GAP",
                    row=idx,
                    message=f"第 {idx} 列與前列存在時間空隙 (前列 end={prev_end_s:.3f}, 本列 start={start_s:.3f}, 間隔={gap:.3f}s > 門檻={max_gap_sec:.3f}s)"
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
) -> EDLValidationResult:
    """
    Validate an EDL CSV file on disk.
    """
    csv_path_str = str(csv_path)
    if not os.path.exists(csv_path_str):
        return EDLValidationResult(
            issues=[EDLIssue(severity=SEVERITY_ERROR, code="E_NO_ROWS", row=None, message=f"EDL 檔案不存在: {csv_path_str}")],
            shot_count=0,
            cameras_used={},
        )
    with open(csv_path_str, "r", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        delimiter = "\t" if "\t" in sample and "," not in sample else ","
        reader = csv.reader(f, delimiter=delimiter)
        raw_rows = [row for row in reader if row and any(field.strip() for field in row)]

    return validate_edl_rows(raw_rows, known_cameras=known_cameras, max_gap_sec=max_gap_sec)


def format_validation_report(result: EDLValidationResult) -> str:
    """
    Format EDL validation result into a human-readable multi-line terminal report.
    """
    lines = []
    lines.append("-" * 78)
    lines.append("🔍 EDL 語意驗證報告")
    lines.append("-" * 78)
    lines.append(f"總鏡頭數  : {result.shot_count}")
    cam_str = ", ".join(f"{k}: {v}" for k, v in sorted(result.cameras_used.items())) if result.cameras_used else "無"
    lines.append(f"相機分佈  : {cam_str}")
    error_count = sum(1 for i in result.issues if i.severity == SEVERITY_ERROR)
    warn_count = sum(1 for i in result.issues if i.severity == SEVERITY_WARN)
    if error_count == 0 and warn_count == 0:
        lines.append("驗證狀態  : ✅ 通過 (無錯誤與警告)")
    elif error_count == 0:
        lines.append(f"驗證狀態  : ⚠️ 通過 (0 錯誤, {warn_count} 警告)")
    else:
        lines.append(f"驗證狀態  : ❌ 失敗 ({error_count} 錯誤, {warn_count} 警告)")
    lines.append("-" * 78)

    if not result.issues:
        lines.append("✓ 未發現任何語意問題。")
    else:
        lines.append(f"{'層級':<8} | {'代碼':<20} | {'列號':<6} | {'詳細說明'}")
        lines.append("-" * 78)
        for issue in result.issues:
            row_str = f"L{issue.row}" if issue.row is not None else "全域"
            lines.append(f"{issue.severity:<8} | {issue.code:<20} | {row_str:<6} | {issue.message}")
    lines.append("-" * 78)
    return "\n".join(lines)
