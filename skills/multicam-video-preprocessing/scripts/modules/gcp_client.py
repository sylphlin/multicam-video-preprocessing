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


def upload_file_to_gcs_with_cache(local_path, bucket_name, gcs_prefix="raw", project=None, region="us-central1", force_upload=False):
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
    file_name = os.path.basename(local_path)
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
        blob.metadata = {"sha256": local_hash, "original_filename": file_name}
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
