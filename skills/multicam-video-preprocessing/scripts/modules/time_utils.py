"""
Time utility module for bidirectional conversion between time strings (HH:MM:SS.mmm) and float seconds.
"""


def parse_time_to_seconds(ts_str):
    """
    Parse a time string (HH:MM:SS.mmm, MM:SS.mmm, or raw float seconds) into float seconds.
    """
    if ts_str is None:
        return None
    try:
        return float(ts_str)
    except ValueError:
        pass
    parts = str(ts_str).strip().split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    elif len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    raise ValueError(f"Unrecognized time format: {ts_str}")


def format_seconds(seconds):
    """
    Format float seconds into a standard HH:MM:SS.mmm timestamp string.
    """
    if seconds is None:
        return "--:--:--"
    if seconds < 0:
        return f"-{format_seconds(-seconds)}"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"
