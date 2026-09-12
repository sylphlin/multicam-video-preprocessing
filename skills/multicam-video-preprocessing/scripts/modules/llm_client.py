#!/usr/bin/env python3
"""
Universal Zero-Dependency LLM Client (llm_client.py).
Supports Google Gemini REST API and OpenAI-Compatible endpoints (/v1/chat/completions)
for Codex, OpenAI (e.g. GPT-5.6 Luna), and Local Models (e.g. Gemma 4 (gemma4:e4b), Ollama, vLLM).
Includes robust SSL support for macOS and multi-layer .env discovery (Antigravity & Codex).
"""

import json
import os
import random
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def get_ssl_context():
    """
    Create a robust SSL context that works seamlessly on macOS without certificate verification failures.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass

    try:
        # Standard system default context
        ctx = ssl.create_default_context()
        return ctx
    except Exception:
        # Fallback for environments with broken local CA certificates
        ctx = ssl._create_unverified_context()
        return ctx


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


def resolve_api_key(cli_key=None, base_url=None, model=None):
    """
    Resolve API key across multiple priority sources:
      1. Explicit CLI argument (--api-key)
      2. Environment variables (GEMINI_API_KEY, GOOGLE_API_KEY, OPENAI_API_KEY)
      3. Project root .env file
      4. Antigravity global env (~/.gemini/.env)
      5. Codex global env (~/.codex/.env)
    """
    if cli_key:
        return cli_key

    # Check environment variables in priority order
    for env_var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "LOCAL_API_KEY"):
        val = os.environ.get(env_var)
        if val:
            return val

    # Scan .env files in standard locations
    search_env_paths = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), ".env"),
        os.path.expanduser("~/.gemini/.env"),
        os.path.expanduser("~/.codex/.env"),
        os.path.expanduser("~/.config/gemini/api_key")
    ]

    for env_path in search_env_paths:
        if os.path.isfile(env_path):
            env_vars = _parse_env_file(env_path)
            for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "LOCAL_API_KEY"):
                if env_vars.get(k):
                    return env_vars[k]

    # For local endpoints (localhost / 127.0.0.1 / Ollama), key is optional
    if base_url and any(loc in base_url for loc in ("localhost", "127.0.0.1", "0.0.0.0", "11434", "8000")):
        return "none"

    return None


def call_gemini_generate_content(prompt, api_key, model="gemini-3.8-flash", file_uri=None, audio_path=None, temperature=0.1, max_tokens=8192, thinking_budget=None, max_retries=5):
    """
    Call Google Gemini generateContent REST API with optional video file_uri or audio_path.
    Includes robust exponential backoff with jitter for HTTP 429 (rate limits) and 5xx errors.
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    parts = []
    if file_uri:
        parts.append({"file_data": {"mime_type": "video/mp4", "file_uri": file_uri}})
    if audio_path and os.path.isfile(audio_path):
        import base64
        with open(audio_path, "rb") as f_aud:
            aud_data = base64.b64encode(f_aud.read()).decode("utf-8")
        mime = "audio/mp3" if audio_path.endswith(".mp3") else "audio/wav"
        parts.append({"inline_data": {"mime_type": mime, "data": aud_data}})
    parts.append({"text": prompt})

    gen_config = {"temperature": temperature, "maxOutputTokens": max_tokens}
    if thinking_budget is not None and ("3.7" in model or "3.8" in model):
        gen_config["thinkingConfig"] = {"thinkingBudget": thinking_budget}

    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": gen_config
    }

    req_data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    ctx = get_ssl_context()

    for attempt in range(max_retries):
        req = urllib.request.Request(url, data=req_data, headers=headers)
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=600) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                candidates = data.get("candidates", [])
                if candidates:
                    content_parts = candidates[0].get("content", {}).get("parts", [])
                    text_parts = [p.get("text", "") for p in content_parts if "text" in p]
                    return "".join(text_parts).strip()
                return ""
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            # Retry on 429 (Resource Exhausted / Rate Limit), 500, 502, 503, 504
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                retry_after = None
                if "Retry-After" in e.headers:
                    try:
                        retry_after = float(e.headers["Retry-After"])
                    except Exception:
                        pass
                if retry_after is not None:
                    sleep_sec = retry_after + random.uniform(0.5, 1.5)
                else:
                    sleep_sec = min(60.0, 2.0 * (2 ** attempt)) + random.uniform(0.5, 2.0)
                status_label = "Rate Limit (429)" if e.code == 429 else f"Server Error ({e.code})"
                print(f"\n  ⚠️ Gemini API {status_label} hit, retrying in {sleep_sec:.1f}s (Attempt {attempt + 1}/{max_retries})...", file=sys.stderr)
                time.sleep(sleep_sec)
                continue
            raise RuntimeError(f"Gemini API error (HTTP {e.code}): {err_body}")
        except urllib.error.URLError as e:
            if attempt < max_retries - 1:
                sleep_sec = min(60.0, 2.0 * (2 ** attempt)) + random.uniform(0.5, 1.5)
                print(f"\n  ⚠️ Gemini API connection issue ({e.reason}), retrying in {sleep_sec:.1f}s (Attempt {attempt + 1}/{max_retries})...", file=sys.stderr)
                time.sleep(sleep_sec)
                continue
            # Retry with unverified SSL if certificate verification was the issue
            try:
                unverified_ctx = ssl._create_unverified_context()
                with urllib.request.urlopen(req, context=unverified_ctx, timeout=600) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    candidates = data.get("candidates", [])
                    if candidates:
                        content_parts = candidates[0].get("content", {}).get("parts", [])
                        text_parts = [p.get("text", "") for p in content_parts if "text" in p]
                        return "".join(text_parts).strip()
                    return ""
            except Exception as retry_err:
                raise RuntimeError(f"Gemini API connection error: {e.reason} (Retry failed: {retry_err})")
        except Exception as e:
            if attempt < max_retries - 1:
                sleep_sec = min(60.0, 2.0 * (2 ** attempt)) + random.uniform(0.5, 1.5)
                print(f"\n  ⚠️ Gemini API call exception ({e}), retrying in {sleep_sec:.1f}s (Attempt {attempt + 1}/{max_retries})...", file=sys.stderr)
                time.sleep(sleep_sec)
                continue
            raise


def call_openai_chat_completions(prompt, api_key, base_url="https://api.openai.com/v1", model="gpt-5.6-luna",
                                 image_base64_list=None, temperature=0.1, max_tokens=8192, max_retries=5):
    """
    Call OpenAI-Compatible /v1/chat/completions REST endpoint.
    Supports Cloud models (Codex, GPT-5.6 Luna) and Local models (Gemma 4 (gemma4:e4b), Ollama, vLLM).
    Includes automatic exponential backoff retry for HTTP 429 and 5xx errors.
    """
    clean_base = base_url.rstrip("/")
    if not clean_base.endswith("/chat/completions"):
        if clean_base.endswith("/v1"):
            url = f"{clean_base}/chat/completions"
        else:
            url = f"{clean_base}/v1/chat/completions"
    else:
        url = clean_base

    content_items = []
    if image_base64_list:
        for img_b64 in image_base64_list:
            content_items.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
            })
    content_items.append({"type": "text", "text": prompt})

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content_items if image_base64_list else prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens
    }

    headers = {"Content-Type": "application/json"}
    if api_key and api_key != "none":
        headers["Authorization"] = f"Bearer {api_key}"

    req_data = json.dumps(payload).encode("utf-8")
    ctx = get_ssl_context()

    for attempt in range(max_retries):
        req = urllib.request.Request(url, data=req_data, headers=headers)
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=600) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                choices = data.get("choices", [])
                if choices:
                    message = choices[0].get("message", {})
                    return message.get("content", "").strip()
                return ""
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                retry_after = None
                if "Retry-After" in e.headers:
                    try:
                        retry_after = float(e.headers["Retry-After"])
                    except Exception:
                        pass
                if retry_after is not None:
                    sleep_sec = retry_after + random.uniform(0.5, 1.5)
                else:
                    sleep_sec = min(60.0, 2.0 * (2 ** attempt)) + random.uniform(0.5, 2.0)
                status_label = "Rate Limit (429)" if e.code == 429 else f"Server Error ({e.code})"
                print(f"\n  ⚠️ OpenAI endpoint {status_label} hit, retrying in {sleep_sec:.1f}s (Attempt {attempt + 1}/{max_retries})...", file=sys.stderr)
                time.sleep(sleep_sec)
                continue
            raise RuntimeError(f"OpenAI-Compatible endpoint error (HTTP {e.code} at {url}): {err_body}")
        except urllib.error.URLError as e:
            if attempt < max_retries - 1:
                sleep_sec = min(60.0, 2.0 * (2 ** attempt)) + random.uniform(0.5, 1.5)
                print(f"\n  ⚠️ OpenAI endpoint connection error ({e.reason}), retrying in {sleep_sec:.1f}s (Attempt {attempt + 1}/{max_retries})...", file=sys.stderr)
                time.sleep(sleep_sec)
                continue
            try:
                unverified_ctx = ssl._create_unverified_context()
                with urllib.request.urlopen(req, context=unverified_ctx, timeout=600) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    choices = data.get("choices", [])
                    if choices:
                        message = choices[0].get("message", {})
                        return message.get("content", "").strip()
                    return ""
            except Exception as retry_err:
                raise RuntimeError(f"OpenAI-Compatible connection error to {url}: {e.reason} (Retry: {retry_err})")
        except Exception as e:
            if attempt < max_retries - 1:
                sleep_sec = min(60.0, 2.0 * (2 ** attempt)) + random.uniform(0.5, 1.5)
                print(f"\n  ⚠️ OpenAI endpoint call exception ({e}), retrying in {sleep_sec:.1f}s (Attempt {attempt + 1}/{max_retries})...", file=sys.stderr)
                time.sleep(sleep_sec)
                continue
            raise


def call_vertex_generate_content(prompt, project, location="us-central1", model="gemini-3.8-flash",
                                 gcs_uri=None, audio_path=None, temperature=0.1, max_tokens=8192,
                                 thinking_budget=None, max_retries=5):
    """
    Call Google Cloud Vertex AI Gemini API using Application Default Credentials (ADC).
    Supports GCS URIs (gs://...) and local audio files (inline).
    """
    import google.genai as genai
    from google.genai import types

    client = genai.Client(vertexai=True, project=project, location=location)

    contents = []
    if gcs_uri:
        mime = "video/mp4" if gcs_uri.endswith(".mp4") else ("audio/mp3" if gcs_uri.endswith(".mp3") else "audio/wav")
        contents.append(types.Part.from_uri(file_uri=gcs_uri, mime_type=mime))
    if audio_path and os.path.isfile(audio_path):
        with open(audio_path, "rb") as f_aud:
            aud_bytes = f_aud.read()
        mime = "audio/mp3" if audio_path.endswith(".mp3") else "audio/wav"
        contents.append(types.Part.from_bytes(data=aud_bytes, mime_type=mime))
    contents.append(prompt)

    config_params = {"temperature": temperature, "max_output_tokens": max_tokens}
    if thinking_budget is not None and ("3.7" in model or "3.8" in model):
        config_params["thinking_config"] = types.ThinkingConfig(thinking_budget=thinking_budget)
    config = types.GenerateContentConfig(**config_params)

    for attempt in range(max_retries):
        try:
            resp = client.models.generate_content(
                model=model,
                contents=contents,
                config=config
            )
            return resp.text.strip() if resp.text else ""
        except Exception as e:
            err_str = str(e)
            is_rate_limit = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
            is_server_err = any(code in err_str for code in ("500", "502", "503", "504", "UNAVAILABLE"))
            if (is_rate_limit or is_server_err) and attempt < max_retries - 1:
                sleep_sec = min(60.0, 2.0 * (2 ** attempt)) + random.uniform(0.5, 2.0)
                status_label = "Rate Limit (429)" if is_rate_limit else "Server Error"
                print(f"\n  ⚠️ Vertex AI {status_label} hit, retrying in {sleep_sec:.1f}s (Attempt {attempt + 1}/{max_retries})...", file=sys.stderr)
                time.sleep(sleep_sec)
                continue
            raise


def call_llm(prompt, model="gemini-3.8-flash", backend="vertex", project=None, location=None,
             gcs_bucket=None, fallback_studio=False, base_url=None, api_key=None,
             file_uri=None, gcs_uri=None, audio_path=None, image_base64_list=None,
             temperature=0.1, max_tokens=8192, thinking_budget=None):
    """
    Unified LLM router:
    - Primary: Google Cloud Vertex AI (ADC + GCS) when backend="vertex".
    - Secondary: Google AI Studio (API Key) when backend="studio" or via fallback_studio=True.
    - Third: OpenAI-Compatible REST endpoints.
    """
    try:
        from .gcp_client import resolve_gcp_config
    except ImportError:
        try:
            from modules.gcp_client import resolve_gcp_config
        except ImportError:
            from scripts.modules.gcp_client import resolve_gcp_config

    # Route to OpenAI-Compatible if custom base_url or explicit non-gemini model
    is_openai_compatible = bool(base_url) or not ("gemini" in model.lower())
    if is_openai_compatible:
        resolved_key = resolve_api_key(api_key, base_url, model)
        effective_base = base_url or "https://api.openai.com/v1"
        return call_openai_chat_completions(
            prompt=prompt,
            api_key=resolved_key,
            base_url=effective_base,
            model=model,
            image_base64_list=image_base64_list,
            temperature=temperature,
            max_tokens=max_tokens
        )

    # Resolve GCP & AI Studio configurations
    gcp_cfg = resolve_gcp_config(cli_project=project, cli_bucket=gcs_bucket, cli_location=location)
    resolved_key = resolve_api_key(api_key, base_url, model)

    target_backend = str(backend).lower() if backend else "vertex"

    # Primary: Vertex AI (ADC)
    if target_backend == "vertex":
        if not gcp_cfg.get("project"):
            if fallback_studio and resolved_key:
                print(f"\n  ⚠️ No GCP Project configured. Falling back to Google AI Studio (API Key)...", file=sys.stderr)
                return call_gemini_generate_content(
                    prompt=prompt, api_key=resolved_key, model=model, file_uri=file_uri,
                    audio_path=audio_path, temperature=temperature, max_tokens=max_tokens,
                    thinking_budget=thinking_budget
                )
            raise ValueError(
                "Missing Google Cloud Project ID for Vertex AI. Please pass --project, set GOOGLE_CLOUD_PROJECT in .env, or use --backend studio / --fallback-studio."
            )

        try:
            return call_vertex_generate_content(
                prompt=prompt,
                project=gcp_cfg["project"],
                location=gcp_cfg["location"],
                model=model,
                gcs_uri=gcs_uri,
                audio_path=audio_path,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking_budget=thinking_budget
            )
        except Exception as vertex_err:
            if fallback_studio and resolved_key:
                print(f"\n  ⚠️ Vertex AI call encountered: {vertex_err}. Falling back to Google AI Studio (API Key)...", file=sys.stderr)
                return call_gemini_generate_content(
                    prompt=prompt, api_key=resolved_key, model=model, file_uri=file_uri,
                    audio_path=audio_path, temperature=temperature, max_tokens=max_tokens,
                    thinking_budget=thinking_budget
                )
            raise RuntimeError(
                f"Vertex AI API call failed on project '{gcp_cfg['project']}': {vertex_err}\n"
                f"(Note: To permit automatic fallback to Google AI Studio, pass --fallback-studio or run with --backend studio)"
            )

    # Secondary: AI Studio (API Key)
    elif target_backend in ("studio", "ai_studio", "gemini"):
        if not resolved_key:
            raise ValueError(
                "Missing Gemini API Key for AI Studio. Please pass --api-key, export GEMINI_API_KEY, or add GEMINI_API_KEY=YOUR_KEY in a .env file."
            )
        return call_gemini_generate_content(
            prompt=prompt,
            api_key=resolved_key,
            model=model,
            file_uri=file_uri,
            audio_path=audio_path,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking_budget=thinking_budget
        )
    else:
        raise ValueError(f"Unknown LLM backend: '{backend}'. Supported backends: 'vertex', 'studio'")

