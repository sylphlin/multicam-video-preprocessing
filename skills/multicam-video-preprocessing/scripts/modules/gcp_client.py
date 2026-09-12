"""
Google Cloud Platform (GCP) ADC & GCS Client Module.
Provides robust Application Default Credentials (ADC) integration,
GCS bucket auto-discovery/management, and hash-based upload caching.
"""

import hashlib
import json
import os
import re
import sys
import time

try:
    from google.cloud import storage
    import google.auth
    HAS_GCP_STORAGE = True
except ImportError:
    HAS_GCP_STORAGE = False


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
                    kv[k.strip()] = v.strip().strip("\"'")
    except Exception:
        pass
    return kv


def resolve_gcp_config(cli_project=None, cli_bucket=None, cli_location=None):
    """
    Resolve GCP Project ID, GCS Bucket, and Location across priority sources:
      1. Explicit CLI arguments (--project, --gcs-bucket, --location)
      2. Environment variables (GOOGLE_CLOUD_PROJECT, GCS_BUCKET, GOOGLE_CLOUD_LOCATION)
      3. Project root .env and standard locations
    Returns: dict(project=..., bucket=..., location=...)
    """
    project = cli_project or os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
    bucket = cli_bucket or os.environ.get("GCS_BUCKET") or os.environ.get("GOOGLE_CLOUD_STORAGE_BUCKET")
    location = cli_location or os.environ.get("GOOGLE_CLOUD_LOCATION") or os.environ.get("GCP_LOCATION")

    # Scan .env files if any parameter is missing
    if not (project and bucket and location):
        search_env_paths = [
            os.path.join(os.getcwd(), ".env"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), ".env"),
            os.path.expanduser("~/.gemini/.env"),
            os.path.expanduser("~/.config/gcloud/configurations/config_default")
        ]
        for env_p in search_env_paths:
            if os.path.isfile(env_p):
                env_vars = _parse_env_file(env_p)
                if not project:
                    project = env_vars.get("GOOGLE_CLOUD_PROJECT") or env_vars.get("GCP_PROJECT") or env_vars.get("project")
                if not bucket:
                    bucket = env_vars.get("GCS_BUCKET") or env_vars.get("GOOGLE_CLOUD_STORAGE_BUCKET")
                if not location:
                    location = env_vars.get("GOOGLE_CLOUD_LOCATION") or env_vars.get("GCP_LOCATION")

    # Default fallback for location
    if not location:
        location = "us-central1"

    return {
        "project": project,
        "bucket": bucket,
        "location": location
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
    """Initialize and return a google.cloud.storage.Client with ADC."""
    if not HAS_GCP_STORAGE:
        raise RuntimeError(
            "The 'google-cloud-storage' package is not installed. Please run: pip install google-cloud-storage"
        )
    return storage.Client(project=project)


def ensure_gcs_bucket(bucket_name, project=None, location="us-central1"):
    """
    Ensure the specified GCS bucket exists. Attempts creation if missing and permissions permit.
    Returns: storage.Bucket
    """
    client = get_gcs_storage_client(project=project)
    bucket = client.bucket(bucket_name)
    try:
        if not bucket.exists():
            print(f"  ► GCS Bucket 'gs://{bucket_name}' not found. Attempting creation in {location} ...")
            bucket = client.create_bucket(bucket_name, project=project, location=location)
            print(f"  ✓ GCS Bucket 'gs://{bucket_name}' created successfully.")
    except Exception as e:
        # If exists check fails due to IAM permissions, continue and let upload raise if unauthorized
        pass
    return bucket


def upload_file_to_gcs_with_cache(local_path, bucket_name, gcs_prefix="multicam_assets", project=None, location="us-central1"):
    """
    Upload a local file to GCS with hash-based caching:
    1. Computes local file SHA-256 and size.
    2. Checks if a blob with matching name and SHA-256 already exists on GCS.
    3. If cache hits: skips upload and immediately returns gs://bucket/path.
    4. If cache misses: executes resumable upload with live progress, storing hash in metadata.
    Returns: "gs://<bucket_name>/<blob_name>"
    """
    if not os.path.exists(local_path):
        raise FileNotFoundError(f"Local file not found: {local_path}")

    file_size = os.path.getsize(local_path)
    file_name = os.path.basename(local_path)
    blob_name = f"{gcs_prefix}/{file_name}" if gcs_prefix else file_name

    client = get_gcs_storage_client(project=project)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    print(f"\n[GCS] Verifying GCS upload cache for {file_name} ({file_size / (1024 * 1024):.1f} MB)...")
    t0 = time.time()

    # Step 1: Calculate local hash
    local_hash = compute_file_sha256(local_path)

    # Step 2: Check remote blob metadata
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
    print(f"  ► [Uploading] Transmitting to gs://{bucket_name}/{blob_name} via ADC ...")
    t_up_start = time.time()

    try:
        blob.metadata = {"sha256": local_hash, "original_filename": file_name}
        # Set chunk size to 16MB for high performance
        blob.chunk_size = 16 * 1024 * 1024
        blob.upload_from_filename(local_path, timeout=1200)
        up_duration = time.time() - t_up_start
        speed_mbps = (file_size / (1024 * 1024)) / max(0.1, up_duration) * 8
        print(f"  ✓ Uploaded to gs://{bucket_name}/{blob_name} in {up_duration:.1f}s ({speed_mbps:.1f} Mbps)")
        return f"gs://{bucket_name}/{blob_name}"
    except Exception as e:
        raise RuntimeError(f"GCS upload failed for {local_path} -> gs://{bucket_name}/{blob_name}: {e}")
