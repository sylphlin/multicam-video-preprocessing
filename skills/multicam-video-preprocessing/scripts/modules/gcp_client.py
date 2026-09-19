"""
Google Cloud Platform (GCP) ADC & GCS Client Module.
Provides 100% Application Default Credentials (ADC) integration,
GCS bucket auto-discovery/provisioning with 2-day lifecycle auto-cleanup,
SHA-256 hash-based upload caching, and ephemeral blob cleanup.
"""

import hashlib
import os
import re
import subprocess
import sys
import time
from urllib.parse import urlparse

try:
    from google.cloud import storage
    import google.auth
    HAS_GCP_STORAGE = True
except ImportError:
    HAS_GCP_STORAGE = False


_MIME_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".webm": "video/webm",
    ".m4v": "video/mp4",
}


def guess_mime_type(filepath):
    """Return explicit MIME type for media file so GCS blob and Vertex AI Part match."""
    ext = os.path.splitext(str(filepath))[1].lower()
    return _MIME_TYPES.get(ext, "video/mp4")


def parse_gcs_uri(gcs_uri):
    """Parse gs://bucket/path/to/blob into (bucket_name, blob_name)."""
    parsed = urlparse(str(gcs_uri))
    if parsed.scheme != "gs":
        raise ValueError(f"Invalid GCS URI (must start with gs://): {gcs_uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def _parse_env_file(filepath):
    """Parse key-value pairs from a .env file."""
    kv = {}
    if not os.path.exists(filepath):
        return kv
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                line = re.sub(r"^export\s+", "", line)
                if "=" in line:
                    k, v = line.split("=", 1)
                    v = v.split("#")[0].strip().strip("\"'")
                    kv[k.strip()] = v
    except Exception:
        pass
    return kv


def resolve_gcp_config(cli_project=None, cli_bucket=None, cli_location=None, cli_region=None):
    """
    Resolve GCP Project ID, GCS Bucket, Vertex AI Location, and GCS Region across priority sources:
      1. Explicit CLI arguments (--project, --gcs-bucket, --location, --region)
      2. Environment variables (GOOGLE_CLOUD_PROJECT / GCP_PROJECT, GCS_BUCKET, GOOGLE_CLOUD_LOCATION, GCP_REGION)
      3. Project root .env and ~/.gemini/.env
      4. Application Default Credentials (google.auth.default()) & gcloud CLI config
      5. Deterministic default bucket: multicam-video-${PROJECT_ID}
    Returns: dict(project=..., bucket=..., location=..., region=...)
    """
    project = cli_project or os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
    bucket = (
        cli_bucket
        or os.environ.get("GCS_BUCKET")
        or os.environ.get("MULTICAM_STORAGE_BUCKET")
        or os.environ.get("GOOGLE_CLOUD_STORAGE_BUCKET")
    )
    location = cli_location or os.environ.get("GOOGLE_CLOUD_LOCATION")
    region = cli_region or os.environ.get("GCP_REGION")

    # Scan .env files if any parameter is missing
    search_env_paths = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env"),
        os.path.expanduser("~/.gemini/.env"),
        os.path.expanduser("~/.config/gcloud/configurations/config_default"),
    ]
    for env_p in search_env_paths:
        if os.path.isfile(env_p):
            env_vars = _parse_env_file(env_p)
            if not project:
                project = env_vars.get("GOOGLE_CLOUD_PROJECT") or env_vars.get("GCP_PROJECT") or env_vars.get("project")
            if not bucket:
                bucket = (
                    env_vars.get("GCS_BUCKET")
                    or env_vars.get("MULTICAM_STORAGE_BUCKET")
                    or env_vars.get("GOOGLE_CLOUD_STORAGE_BUCKET")
                )
            if not location:
                location = env_vars.get("GOOGLE_CLOUD_LOCATION")
            if not region:
                region = env_vars.get("GCP_REGION")

    # Fallback: discover project via google.auth.default() or gcloud CLI
    if not project and HAS_GCP_STORAGE:
        try:
            _, default_proj = google.auth.default()
            if default_proj:
                project = default_proj
        except Exception:
            pass

    if not project:
        try:
            res = subprocess.run(
                ["gcloud", "config", "get-value", "project"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5
            )
            if res.returncode == 0 and res.stdout.strip():
                val = res.stdout.strip()
                if val != "(unset)":
                    project = val
        except Exception:
            pass

    # Strip gs:// prefix if provided
    if bucket and bucket.startswith("gs://"):
        bucket = bucket[5:].strip("/")

    # Deterministic default bucket name (matching meeting-transcribe-agent pattern)
    if not bucket and project:
        bucket = f"multicam-video-{project}"

    # Default location for Vertex AI Gemini 3.8 Flash is 'global', GCS region is 'us-central1'
    if not location:
        location = "global"
    if not region:
        region = "us-central1"

    return {
        "project": project,
        "bucket": bucket,
        "location": location,
        "region": region,
    }


def compute_file_sha256(filepath, chunk_size=8 * 1024 * 1024):
    """Compute SHA-256 hash of a local file in streaming chunks."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def get_gcs_storage_client(project=None):
    """Initialize and return a google.cloud.storage.Client with Application Default Credentials (ADC)."""
    if not HAS_GCP_STORAGE:
        raise RuntimeError(
            "The 'google-cloud-storage' package is not installed. Please run: pip install google-cloud-storage"
        )
    return storage.Client(project=project)


def ensure_gcs_bucket(bucket_name, project=None, region="us-central1"):
    """
    Ensure the specified GCS bucket exists with lifecycle rules:
    - raw/: 2-day auto-delete (ephemeral staging)
    - output/, deliverables/, multicam_assets/: 15-day auto-delete (deliverables retention)
    Attempts creation if missing and permissions permit.
    Returns: storage.Bucket
    """
    client = get_gcs_storage_client(project=project)
    bucket = client.bucket(bucket_name)
    try:
        if not bucket.exists():
            create_loc = "us-central1" if region == "global" else (region or "us-central1")
            print(f"  ► GCS Bucket 'gs://{bucket_name}' not found. Auto-provisioning in {create_loc} ...")
            bucket = client.create_bucket(bucket_name, project=project, location=create_loc)
            bucket.iam_configuration.uniform_bucket_level_access_enabled = True
            bucket.iam_configuration.public_access_prevention = "enforced"
            bucket.add_lifecycle_delete_rule(age=2, matches_prefix=["raw/"])
            bucket.add_lifecycle_delete_rule(age=15, matches_prefix=["output/", "deliverables/", "multicam_assets/"])
            bucket.patch()
            print(f"  ✓ GCS Bucket 'gs://{bucket_name}' created with lifecycle rules (raw/: 2d, deliverables: 15d).")
    except Exception:
        # If exists/create check fails due to bucket-level IAM permissions, let upload proceed
        pass
    return bucket


def upload_file_to_gcs_with_cache(local_path, bucket_name, gcs_prefix="raw", project=None, region="us-central1", force_upload=False, extra_metadata=None):
    """
    Upload a local media file to GCS with SHA-256 hash-based caching:
    1. Computes local file SHA-256 and size.
    2. Checks if a blob with matching name and SHA-256 already exists on GCS (unless force_upload=True).
    3. If cache hits: skips upload and immediately returns gs://bucket/path.
    4. If cache misses: executes resumable chunked upload with explicit MIME content_type and metadata.
    Returns: "gs://<bucket_name>/<blob_name>"
    """
    if not os.path.exists(local_path):
        raise FileNotFoundError(f"Local file not found: {local_path}")

    bucket_name = bucket_name.replace("gs://", "").strip("/")
    file_size = os.path.getsize(local_path)
    file_size_mb = file_size / (1024 * 1024)
    file_name = fix_mojibake_filename(os.path.basename(local_path))
    blob_name = f"{gcs_prefix.strip('/')}/{file_name}" if gcs_prefix else file_name
    mime_type = guess_mime_type(local_path)

    ensure_gcs_bucket(bucket_name, project=project, region=region)
    client = get_gcs_storage_client(project=project)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    print(f"\n[GCS] Verifying GCS upload cache for {file_name} ({file_size_mb:.1f} MB)...")
    t0 = time.time()

    # Step 1: Calculate local hash
    local_hash = compute_file_sha256(local_path)

    # Step 2: Check remote blob metadata
    if not force_upload:
        try:
            blob.reload()
            remote_hash = (blob.metadata or {}).get("sha256")
            remote_size = blob.size
            if remote_size == file_size and remote_hash == local_hash:
                duration = time.time() - t0
                print(f"  ✓ [Cache Hit] Remote gs://{bucket_name}/{blob_name} is identical (SHA-256 matched, verified in {duration:.1f}s). Skipping upload!")
                return f"gs://{bucket_name}/{blob_name}"
        except Exception:
            # Blob does not exist or metadata unreadable, proceed to upload
            pass

    # Step 3: Resumable chunked upload
    effective_timeout = max(1200, int(file_size_mb * 5) + 60)
    print(f"  ► [Uploading] Transmitting to gs://{bucket_name}/{blob_name} via ADC (MIME: {mime_type})...")
    t_up_start = time.time()

    try:
        blob.content_type = mime_type
        meta = {"sha256": local_hash, "original_filename": file_name}
        if isinstance(extra_metadata, dict):
            for k, v in extra_metadata.items():
                if v is not None:
                    meta[str(k)] = str(v)
        blob.metadata = meta
        blob.chunk_size = 16 * 1024 * 1024  # 16MB chunks
        blob.upload_from_filename(local_path, content_type=mime_type, timeout=effective_timeout)
        up_duration = time.time() - t_up_start
        speed_mbps = file_size_mb / max(0.1, up_duration) * 8
        print(f"  ✓ Uploaded to gs://{bucket_name}/{blob_name} in {up_duration:.1f}s ({speed_mbps:.1f} Mbps)")
        return f"gs://{bucket_name}/{blob_name}"
    except Exception as exc:
        err_msg = str(exc)
        if "403" in err_msg or "Forbidden" in err_msg or "AccessDeniedException" in err_msg:
            print(
                f"\n{'='*72}\n"
                f"[❌ GCS PERMISSION ERROR: 403 Forbidden]\n"
                f"Failed to upload media to Cloud Storage bucket 'gs://{bucket_name}'.\n"
                f"Your GCP identity or Vertex AI service agent lacks permission on this bucket.\n\n"
                f"Action Required:\n"
                f"  1. Run './setup.sh' in the project root to auto-configure GCS & IAM bindings, OR:\n"
                f"  2. Grant 'roles/storage.objectUser' on 'gs://{bucket_name}':\n"
                f"     gcloud storage buckets add-iam-policy-binding gs://{bucket_name} \\\n"
                f"       --member=\"user:$(gcloud config get-value account)\" \\\n"
                f"       --role=\"roles/storage.objectUser\"\n"
                f"  3. Re-authenticate if credentials expired: gcloud auth application-default login\n"
                f"{'='*72}\n",
                file=sys.stderr,
            )
        elif "RefreshError" in err_msg or "invalid_scope" in err_msg or "401" in err_msg or "DefaultCredentialsError" in err_msg:
            print(
                f"\n{'='*72}\n"
                f"[❌ GCP AUTHENTICATION ERROR]\n"
                f"Application Default Credentials (ADC) are missing, invalid, or expired:\n"
                f"  {exc}\n\n"
                f"Action Required:\n"
                f"  Run: gcloud auth application-default login\n"
                f"{'='*72}\n",
                file=sys.stderr,
            )
        raise RuntimeError(f"GCS upload failed for {local_path} -> gs://{bucket_name}/{blob_name}: {exc}")


def delete_gcs_blob(gcs_uri, project=None):
    """
    Delete a blob from GCS by its gs:// URI.
    Used when --cleanup-gcs is passed or for cleaning up ephemeral audio chunks after inference.
    """
    if not gcs_uri or not str(gcs_uri).startswith("gs://"):
        return
    try:
        bucket_name, blob_name = parse_gcs_uri(gcs_uri)
        client = get_gcs_storage_client(project=project)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.delete()
        print(f"  ✓ [GCS Cleanup] Deleted remote blob: {gcs_uri}")
    except Exception:
        pass


# ==============================================================================
# Google Drive (ADC) Integration: URL/ID Parsing, Caching & GCS Transfer
# ==============================================================================
GDRIVE_MEDIA_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4a", ".wav", ".aac", ".mp3", ".webm"}
GDRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/cloud-platform",
]


def is_gdrive_source(val):
    """
    Return True if `val` is a Google Drive URL (`https://drive.google.com/...`)
    or explicit `gdrive://` / `gdrive:` scheme.
    """
    if not val or not isinstance(val, str):
        return False
    s = val.strip()
    return (
        s.startswith("https://drive.google.com/")
        or s.startswith("http://drive.google.com/")
        or s.startswith("https://docs.google.com/")
        or s.startswith("gdrive://")
        or s.startswith("gdrive:")
    )


def parse_gdrive_url(url_or_id):
    """
    Parse a Google Drive file URL, folder URL, `gdrive://` URI, or raw Drive ID.
    Returns:
      {"id": "<drive_id>", "type": "folder" | "file" | "unknown"}
    """
    if not url_or_id or not isinstance(url_or_id, str):
        raise ValueError(f"Invalid Google Drive URL or ID: {url_or_id!r}")

    s = url_or_id.strip()

    # 1. Explicit gdrive://folder/<ID> or gdrive://<ID>
    if s.startswith("gdrive://"):
        rest = s[len("gdrive://"):].strip("/")
        if rest.startswith("folder/") or rest.startswith("folders/"):
            fid = rest.split("/", 1)[1].split("?")[0].strip("/")
            return {"id": fid, "type": "folder"}
        if rest.startswith("file/"):
            fid = rest.split("/", 1)[1].split("?")[0].strip("/")
            return {"id": fid, "type": "file"}
        return {"id": rest.split("?")[0].strip("/"), "type": "unknown"}

    if s.startswith("gdrive:"):
        fid = s[len("gdrive:"):].strip("/")
        return {"id": fid, "type": "unknown"}

    # 2. Folder URL: https://drive.google.com/drive/folders/<ID> or /drive/u/0/folders/<ID>
    m_folder = re.search(r"/folders/([a-zA-Z0-9_-]{10,})", s)
    if m_folder:
        return {"id": m_folder.group(1), "type": "folder"}

    # 3. File URL: https://drive.google.com/file/d/<ID>/view...
    m_file = re.search(r"/(?:file|document|presentation|spreadsheets)/d/([a-zA-Z0-9_-]{10,})", s)
    if m_file:
        return {"id": m_file.group(1), "type": "file"}

    # 4. Query param ?id=<ID> or &id=<ID>
    m_query = re.search(r"[?&]id=([a-zA-Z0-9_-]{10,})", s)
    if m_query:
        return {"id": m_query.group(1), "type": "unknown"}

    # 5. Raw Drive ID (alphanumeric/dash/underscore, >= 15 chars, no path slashes)
    if "/" not in s and re.match(r"^[a-zA-Z0-9_-]{15,}$", s):
        return {"id": s, "type": "unknown"}

    raise ValueError(f"Could not extract Google Drive ID from: {url_or_id!r}")


def _natural_sort_key(name):
    """Natural sort helper so CAM1.mp4 < CAM2.mp4 < CAM10.mp4."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(name))]


def _load_gcloud_user_drive_credentials():
    """
    Attempt to load personal user Google Drive credentials (user@gmail.com) from
    ~/.config/gcloud/legacy_credentials/<account>/adc.json (populated when user runs
    `gcloud auth login --enable-gdrive-access`, which uses Google Cloud SDK's
    whitelisted first-party CLOUDSDK_CLIENT_ID and never triggers 'This app is blocked').
    Returns refreshed Credentials if valid for Drive access, or None otherwise.
    """
    if not HAS_GOOGLE_AUTH:
        return None

    from google.auth.transport.requests import Request as GoogleAuthRequest

    gcloud_dir = os.path.expanduser("~/.config/gcloud")
    legacy_dir = os.path.join(gcloud_dir, "legacy_credentials")
    if not os.path.isdir(legacy_dir):
        return None

    active_account = None
    try:
        active_cfg_name = "default"
        active_cfg_file = os.path.join(gcloud_dir, "active_config")
        if os.path.isfile(active_cfg_file):
            with open(active_cfg_file, "r", encoding="utf-8") as f:
                active_cfg_name = f.read().strip() or "default"
        cfg_path = os.path.join(gcloud_dir, "configurations", f"config_{active_cfg_name}")
        if os.path.isfile(cfg_path):
            cfg_vars = _parse_env_file(cfg_path)
            active_account = cfg_vars.get("account")
    except Exception:
        pass

    candidate_accounts = []
    if active_account:
        candidate_accounts.append(active_account)
    try:
        for entry in sorted(os.listdir(legacy_dir)):
            if entry not in candidate_accounts:
                candidate_accounts.append(entry)
    except Exception:
        pass

    drive_scopes = [
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/cloud-platform",
    ]
    for acct in candidate_accounts:
        adc_file = os.path.join(legacy_dir, acct, "adc.json")
        if os.path.isfile(adc_file):
            try:
                creds, _ = google.auth.load_credentials_from_file(adc_file, scopes=drive_scopes)
                creds.refresh(GoogleAuthRequest())
                if creds.valid:
                    return creds
            except Exception:
                continue
    return None


def get_gdrive_session(project=None):
    """
    Create an HTTP session for Google Drive access using a 3-Tier Waterfall (zero 'This app is blocked'):
    1. Tier 1 (Personal Account Identity - `user@gmail.com`):
       Loads personal credentials from `gcloud auth login --enable-gdrive-access`
       (`~/.config/gcloud/legacy_credentials/<account>/adc.json`) or native Service Account.
       Supports Scenario 1 (private files/folders shared ONLY with the user's personal account)
       as well as Scenario 2 (`Open to all` public links).
    2. Tier 2 (GCP Service Account Impersonation - `multicam-video-sa@...`):
       Uses standard `cloud-platform` ADC to impersonate the project's Service Account via
       `google.auth.impersonated_credentials`. Directly supports Scenario 2 (`Open to all`
       files/folders with full Drive API v3 folder listing & md5Checksum) and Scenario 1
       when the folder is shared with the Service Account email.
    3. Tier 3 (Public Direct Stream Fallback):
       Falls back to an unauthenticated `requests.Session()` paired with `embeddedfolderview`
       and `drive.usercontent.google.com` so `Open to all` links always work.
    """
    import requests
    from google.auth.transport.requests import AuthorizedSession, Request as GoogleAuthRequest

    quota_project = (
        project
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT")
    )
    source_creds = None
    adc_project = None
    if HAS_GOOGLE_AUTH:
        try:
            source_creds, adc_project = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            if not quota_project:
                quota_project = adc_project
        except Exception:
            pass

    # Tier 1A: Personal Google Account credentials (`gcloud auth login --enable-gdrive-access`)
    user_drive_creds = _load_gcloud_user_drive_credentials()
    if user_drive_creds is not None:
        sess = AuthorizedSession(user_drive_creds)
        if quota_project:
            sess.headers["X-Goog-User-Project"] = quota_project
        return sess

    # Tier 1B: Native Service Account credentials
    if source_creds and hasattr(source_creds, "service_account_email"):
        sa_creds, _ = google.auth.default(scopes=GDRIVE_SCOPES)
        sess = AuthorizedSession(sa_creds)
        if quota_project:
            sess.headers["X-Goog-User-Project"] = quota_project
        return sess

    # Tier 2: Impersonate project Service Account using standard cloud-platform ADC (zero OAuth block)
    if source_creds and quota_project:
        try:
            from google.auth import impersonated_credentials
            sa_candidates = []
            env_sa = os.environ.get("GCP_SERVICE_ACCOUNT") or os.environ.get("SERVICE_ACCOUNT")
            if env_sa:
                sa_candidates.append(env_sa)
            for prefix in ("multicam-video-sa", "video-trimmer-sa", "meeting-transcribe-sa"):
                cand = f"{prefix}@{quota_project}.iam.gserviceaccount.com"
                if cand not in sa_candidates:
                    sa_candidates.append(cand)

            for sa_email in sa_candidates:
                try:
                    imp_creds = impersonated_credentials.Credentials(
                        source_credentials=source_creds,
                        target_principal=sa_email,
                        target_scopes=GDRIVE_SCOPES,
                        lifetime=3600,
                    )
                    imp_creds.refresh(GoogleAuthRequest())
                    sess = AuthorizedSession(imp_creds)
                    sess.headers["X-Goog-User-Project"] = quota_project
                    return sess
                except Exception:
                    continue
        except Exception:
            pass

    # Tier 3: Fallback to unauthenticated requests.Session() for public 'Open to all' links
    return requests.Session()


def list_public_gdrive_folder_fallback(folder_id):
    """
    Fallback folder scanner for 'Open to all' (Anyone with the link) Google Drive folders
    using Google Drive's embeddedfolderview endpoint when Drive API v3 credentials are unavailable.
    """
    import html
    import requests

    url = f"https://drive.google.com/embeddedfolderview?id={folder_id}#list"
    resp = requests.get(url, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Could not access public Google Drive folder '{folder_id}' (HTTP {resp.status_code}).")

    # Match entries in embeddedfolderview: id="entry-<FILE_ID>" ... <div class="flip-entry-title">FILENAME</div>
    pattern = re.compile(
        r'id="entry-([a-zA-Z0-9_-]{10,})"[^>]*>.*?<div class="flip-entry-title">([^<]+)</div>',
        re.DOTALL,
    )
    media_files = []
    for m in pattern.finditer(resp.text):
        fid = m.group(1)
        fname = fix_mojibake_filename(html.unescape(m.group(2).strip()))
        ext = os.path.splitext(fname)[1].lower()
        if ext in GDRIVE_MEDIA_EXTENSIONS:
            media_files.append({
                "id": fid,
                "name": fname,
                "mimeType": guess_mime_type(fname),
                "size": 0,
                "md5Checksum": None,
            })
    media_files.sort(key=lambda x: _natural_sort_key(x.get("name", "")))
    return media_files


def fix_mojibake_filename(name: str) -> str:
    """
    Recover UTF-8 filenames that were decoded as ISO-8859-1 (latin-1) by HTTP headers.
    Leaves valid UTF-8 and ASCII filenames untouched.
    """
    if not name:
        return ""
    s = str(name)
    if any(0x80 <= ord(c) <= 0xFF for c in s) and all(ord(c) <= 0xFF for c in s):
        try:
            return s.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass

    def _decode_run(m):
        chunk = m.group(0)
        try:
            return chunk.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return chunk

    if any(0x80 <= ord(c) <= 0xFF for c in s):
        s = re.sub(r"[\x80-\xff]{2,}", _decode_run, s)
    return s


def extract_filename_from_content_disposition(cd: str, fallback_name: str) -> str:
    """
    Extract and decode filename from an HTTP Content-Disposition header.
    Handles RFC 5987 filename*=UTF-8''... (case-insensitive) and ISO-8859-1 mojibake
    inside filename="..." headers.
    """
    from urllib.parse import unquote
    if not cd:
        return fix_mojibake_filename(fallback_name)
    m_utf8 = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;\r\n]+)", cd, re.IGNORECASE)
    if m_utf8:
        raw_val = m_utf8.group(1).strip().strip("\"'")
        return fix_mojibake_filename(unquote(raw_val, encoding="utf-8", errors="replace"))
    m_quoted = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.IGNORECASE)
    if m_quoted:
        raw_val = m_quoted.group(1).strip()
        return fix_mojibake_filename(unquote(raw_val, encoding="utf-8", errors="replace"))
    m_plain = re.search(r'filename\s*=\s*([^;\r\n]+)', cd, re.IGNORECASE)
    if m_plain:
        raw_val = m_plain.group(1).strip().strip("\"'")
        return fix_mojibake_filename(unquote(raw_val, encoding="utf-8", errors="replace"))
    return fix_mojibake_filename(fallback_name)


def download_public_gdrive_file(file_id, dest_dir, preferred_name=None):
    """
    Direct public stream download for 'Open to all' Google Drive files via
    https://drive.usercontent.google.com/download (bypasses large-file virus scan warning
    page automatically with confirm=t, requiring zero OAuth scopes).
    """
    import requests

    os.makedirs(dest_dir, exist_ok=True)
    dl_url = "https://drive.usercontent.google.com/download"
    params = {"id": file_id, "export": "download", "confirm": "t"}
    with requests.get(dl_url, params=params, stream=True, timeout=600) as r:
        r.raise_for_status()
        cd = r.headers.get("Content-Disposition", "")
        detected_name = fix_mojibake_filename(preferred_name) if preferred_name else ""
        if not detected_name:
            detected_name = extract_filename_from_content_disposition(cd, f"gdrive_{file_id}.mp4")

        local_path = os.path.join(dest_dir, detected_name)
        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            print(f"  ✓ [GDrive Local Cache Hit] '{detected_name}' already exists locally. Skipping download!")
            return local_path

        tmp_path = f"{local_path}.part"
        print(f"\n[GDrive Public Stream] Downloading '{detected_name}' ({file_id}) -> {local_path} ...")
        with open(tmp_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=16 * 1024 * 1024):
                if chunk:
                    f.write(chunk)
        os.replace(tmp_path, local_path)
        print(f"  ✓ Download Complete: {local_path}")
        return local_path


def _raise_gdrive_api_error(resp, resource_id):
    """Format clear actionable diagnostics when Google Drive API returns 401/403/404."""
    status = getattr(resp, "status_code", 0)
    body = ""
    try:
        body = resp.text
    except Exception:
        pass

    if status in (401, 403):
        print(
            f"\n{'='*72}\n"
            f"[❌ GOOGLE DRIVE PERMISSION / ACCESS ERROR ({status})]\n"
            f"Failed to access Google Drive resource '{resource_id}'.\n"
            f"Details: {body[:400]}\n\n"
            f"Action Required (Choose Scenario 1 or Scenario 2):\n"
            f"  • Scenario 1 (Private link shared ONLY with your personal Google Account):\n"
            f"    Run Google Cloud SDK's whitelisted user login (never triggers 'This app is blocked'):\n"
            f"      gcloud auth login --enable-gdrive-access\n"
            f"  • Scenario 2 (Public link or Team Folder):\n"
            f"    Set link sharing to 'Anyone with the link (Viewer)' OR share with your project's\n"
            f"    Service Account provisioned by `./setup.sh --project YOUR_PROJECT_ID`.\n"
            f"{'='*72}\n",
            file=sys.stderr,
        )
    elif status == 404:
        print(
            f"\n[❌ GOOGLE DRIVE 404 NOT FOUND] File or folder '{resource_id}' is private and not accessible.\n"
            f"  -> If shared ONLY with your personal account, run: gcloud auth login --enable-gdrive-access\n"
            f"  -> Or set the link to 'Anyone with the link' / share with your project Service Account.",
            file=sys.stderr,
        )
    raise RuntimeError(f"Google Drive API error ({status}) for '{resource_id}': {body[:300]}")


def get_gdrive_file_metadata(url_or_id, project=None):
    """
    Fetch file/folder metadata (id, name, mimeType, size, md5Checksum) from Google Drive API v3.
    """
    parsed = parse_gdrive_url(url_or_id)
    file_id = parsed["id"]
    session = get_gdrive_session(project=project)
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
    params = {
        "fields": "id,name,mimeType,size,md5Checksum",
        "supportsAllDrives": "true",
    }
    resp = session.get(url, params=params, timeout=30)
    if resp.status_code != 200:
        _raise_gdrive_api_error(resp, file_id)
    return resp.json()


def list_gdrive_folder_videos(folder_url_or_id, project=None):
    """
    List all video/audio files inside a Google Drive folder, sorted naturally by filename (e.g. CAM1.mp4, CAM2.mp4).
    Returns a list of dicts: [{"id": ..., "name": ..., "mimeType": ..., "size": ..., "md5Checksum": ...}, ...]
    """
    parsed = parse_gdrive_url(folder_url_or_id)
    folder_id = parsed["id"]
    session = get_gdrive_session(project=project)
    url = "https://www.googleapis.com/drive/v3/files"

    media_files = []
    page_token = None
    while True:
        params = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": "nextPageToken, files(id,name,mimeType,size,md5Checksum)",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "pageSize": 100,
        }
        if page_token:
            params["pageToken"] = page_token

        resp = session.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            try:
                return list_public_gdrive_folder_fallback(folder_id)
            except Exception:
                _raise_gdrive_api_error(resp, folder_id)

        data = resp.json()
        for item in data.get("files", []):
            mime = (item.get("mimeType") or "").lower()
            name = item.get("name") or ""
            ext = os.path.splitext(name)[1].lower()
            if mime == "application/vnd.google-apps.folder":
                continue
            if mime.startswith("video/") or mime.startswith("audio/") or ext in GDRIVE_MEDIA_EXTENSIONS:
                media_files.append(item)

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    media_files.sort(key=lambda x: _natural_sort_key(x.get("name", "")))
    return media_files


def compute_file_md5(filepath, chunk_size=8 * 1024 * 1024):
    """Compute MD5 hash of a local file to match Google Drive's md5Checksum."""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def download_gdrive_file_with_cache(url_or_id, dest_dir, project=None, force_download=False, metadata=None):
    """
    Download a single file from Google Drive to `dest_dir` via ADC with MD5 & size caching.
    If the local file already exists and matches Google Drive's size & md5Checksum, skips downloading.
    Returns the local file path.
    """
    parsed = parse_gdrive_url(url_or_id)
    file_id = parsed["id"]
    try:
        meta = metadata if (metadata and metadata.get("md5Checksum")) else get_gdrive_file_metadata(url_or_id, project=project)
        file_id = meta["id"]
        file_name = fix_mojibake_filename(meta.get("name") or f"{file_id}.mp4")
        expected_size = int(meta.get("size") or 0)
        expected_md5 = meta.get("md5Checksum")
    except Exception:
        pref_name = metadata.get("name") if isinstance(metadata, dict) else None
        return download_public_gdrive_file(file_id, dest_dir, preferred_name=pref_name)

    os.makedirs(dest_dir, exist_ok=True)
    local_path = os.path.join(dest_dir, file_name)
    size_mb = expected_size / (1024 * 1024) if expected_size else 0.0

    if not force_download and os.path.exists(local_path):
        local_size = os.path.getsize(local_path)
        if expected_size > 0 and local_size == expected_size:
            if not expected_md5 or compute_file_md5(local_path) == expected_md5:
                print(f"  ✓ [GDrive Cache Hit] {file_name} ({size_mb:.1f} MB) matches Google Drive MD5. Skipping download!")
                return local_path

    print(f"  ► [GDrive Download] Pulling {file_name} ({size_mb:.1f} MB) from Google Drive (ID: {file_id}) via ADC...")
    session = get_gdrive_session(project=project)
    download_url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
    params = {"alt": "media", "supportsAllDrives": "true"}

    t0 = time.time()
    part_path = local_path + ".part"
    with session.get(download_url, params=params, stream=True, timeout=600) as resp:
        if resp.status_code != 200:
            _raise_gdrive_api_error(resp, file_id)
        with open(part_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
                if chunk:
                    f.write(chunk)

    os.replace(part_path, local_path)
    elapsed = max(0.1, time.time() - t0)
    speed_mbps = (os.path.getsize(local_path) / (1024 * 1024)) / elapsed * 8
    print(f"  ✓ Downloaded {file_name} to {local_path} in {elapsed:.1f}s ({speed_mbps:.1f} Mbps)")
    return local_path


def transfer_gdrive_to_gcs_with_cache(url_or_id, bucket_name, gcs_prefix="raw", project=None, region="us-central1", local_cache_dir=None, force_upload=False):
    """
    Ensure a Google Drive file is staged in GCS (`gs://<bucket_name>/<gcs_prefix>/<name>`):
    1. Queries Google Drive metadata (`id`, `name`, `size`, `md5Checksum`).
    2. Checks if the target GCS blob already exists with matching `size` and `gdrive_md5`.
       If matched -> returns `gs://...` immediately with ZERO download and ZERO upload!
    3. Otherwise -> downloads via `download_gdrive_file_with_cache` and uploads to GCS with `gdrive_md5` metadata.
    Returns: ("gs://<bucket>/<blob>", "<local_cached_path>")
    """
    meta = get_gdrive_file_metadata(url_or_id, project=project)
    file_id = meta["id"]
    file_name = fix_mojibake_filename(meta.get("name") or f"{file_id}.mp4")
    expected_size = int(meta.get("size") or 0)
    expected_md5 = meta.get("md5Checksum")

    bucket_name = bucket_name.replace("gs://", "").strip("/")
    blob_name = f"{gcs_prefix.strip('/')}/{file_name}" if gcs_prefix else file_name

    ensure_gcs_bucket(bucket_name, project=project, region=region)
    client = get_gcs_storage_client(project=project)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    if not force_upload and expected_md5:
        try:
            blob.reload()
            remote_meta = blob.metadata or {}
            if blob.size == expected_size and remote_meta.get("gdrive_md5") == expected_md5:
                print(
                    f"  ✓ [GDrive -> GCS Cache Hit] gs://{bucket_name}/{blob_name} already matches Google Drive "
                    f"(MD5: {expected_md5[:12]}...). Zero transfer needed!"
                )
                local_existing = os.path.join(local_cache_dir, file_name) if local_cache_dir else None
                return f"gs://{bucket_name}/{blob_name}", (local_existing if local_existing and os.path.exists(local_existing) else None)
        except Exception:
            pass

    cache_dir = local_cache_dir or os.path.join(os.getcwd(), "output", "gdrive_inputs")
    local_path = download_gdrive_file_with_cache(
        file_id, dest_dir=cache_dir, project=project, force_download=force_upload, metadata=meta
    )
    gcs_uri = upload_file_to_gcs_with_cache(
        local_path=local_path,
        bucket_name=bucket_name,
        gcs_prefix=gcs_prefix,
        project=project,
        region=region,
        force_upload=force_upload,
        extra_metadata={"gdrive_id": file_id, "gdrive_md5": expected_md5},
    )
    return gcs_uri, local_path


def resolve_multicam_gdrive_inputs(ref=None, targets=None, gdrive_folder=None, output_dir=None, project=None):
    """
    Resolve Google Drive folder or file links for Stage 1 (`multicam_pipeline.py`):
    - If `gdrive_folder` is given (or `ref` is a Google Drive folder link and `targets` is empty):
      lists all camera videos in the Drive folder, downloads them to `<output_dir>/gdrive_inputs/`,
      and returns `(local_ref, local_targets)`.
    - If `ref` or any entry in `targets` is a Google Drive file link:
      downloads those files to `<output_dir>/gdrive_inputs/` and returns `(local_ref, local_targets)`.
    """
    cache_dir = os.path.join(output_dir or "output", "gdrive_inputs")

    # Check if `ref` itself is a Google Drive folder link when gdrive_folder wasn't explicitly specified
    if not gdrive_folder and ref and is_gdrive_source(ref):
        parsed = parse_gdrive_url(ref)
        if parsed["type"] == "folder" and not targets:
            gdrive_folder = ref

    if gdrive_folder:
        print(f"\n[GDrive Folder] Scanning Google Drive folder for camera angles: {gdrive_folder}")
        videos = list_gdrive_folder_videos(gdrive_folder, project=project)
        if len(videos) < 2:
            raise ValueError(
                f"Google Drive folder '{gdrive_folder}' contains {len(videos)} media file(s); "
                f"at least 2 camera files (e.g., CAM1.mp4, CAM2.mp4) are required."
            )
        print(f"  ✓ Discovered {len(videos)} camera files in Google Drive folder: {[v['name'] for v in videos]}")
        local_files = [
            download_gdrive_file_with_cache(v["id"], dest_dir=cache_dir, project=project, metadata=v)
            for v in videos
        ]
        return local_files[0], local_files[1:]

    resolved_ref = ref
    if ref and is_gdrive_source(ref):
        resolved_ref = download_gdrive_file_with_cache(ref, dest_dir=cache_dir, project=project)

    resolved_targets = []
    for t in (targets or []):
        if is_gdrive_source(t):
            resolved_targets.append(download_gdrive_file_with_cache(t, dest_dir=cache_dir, project=project))
        else:
            resolved_targets.append(t)

    return resolved_ref, resolved_targets

