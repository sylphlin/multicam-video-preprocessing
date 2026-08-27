#!/usr/bin/env python3
"""
Universal Zero-Dependency LLM Client (llm_client.py).
Supports Google Gemini REST API and OpenAI-Compatible endpoints (/v1/chat/completions)
for Codex, OpenAI (e.g. GPT-5.6 Luna), and Local Models (e.g. Gemma 4, Ollama, vLLM).
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def resolve_api_key(cli_key=None, base_url=None, model=None):
    """
    Resolve API key from CLI argument or standard environment variables.
    """
    if cli_key:
        return cli_key

    # Check environment variables in priority order
    for env_var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "LOCAL_API_KEY"):
        val = os.environ.get(env_var)
        if val:
            return val

    # For local endpoints (localhost / 127.0.0.1 / Ollama), key is optional
    if base_url and any(loc in base_url for loc in ("localhost", "127.0.0.1", "0.0.0.0", "11434", "8000")):
        return "none"

    return None


def call_gemini_generate_content(prompt, api_key, model="gemini-3.7-flash", file_uri=None, temperature=0.1, max_tokens=8192):
    """
    Call Google Gemini generateContent REST API.
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    parts = []
    if file_uri:
        parts.append({"file_data": {"mime_type": "video/mp4", "file_uri": file_uri}})
    parts.append({"text": prompt})

    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    with urllib.request.urlopen(req) as resp:
        resp_data = json.loads(resp.read().decode("utf-8"))
        text_parts = resp_data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        return "".join([p.get("text", "") for p in text_parts])


def call_openai_chat_completions(prompt, api_key, base_url, model="gpt-5.6-luna", image_base64_list=None, temperature=0.1, max_tokens=8192):
    """
    Call OpenAI-Compatible /v1/chat/completions endpoint (OpenAI, Codex, Gemma 4 on Ollama/vLLM).
    """
    clean_base = base_url.rstrip("/")
    if not clean_base.endswith("/v1") and not clean_base.endswith("/chat/completions"):
        endpoint_url = f"{clean_base}/v1/chat/completions"
    elif clean_base.endswith("/v1"):
        endpoint_url = f"{clean_base}/chat/completions"
    else:
        endpoint_url = clean_base

    content = []
    if image_base64_list:
        for img_b64 in image_base64_list:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
            })
    content.append({"type": "text", "text": prompt})

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content if image_base64_list else prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens
    }

    headers = {"Content-Type": "application/json"}
    if api_key and api_key != "none":
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(
        endpoint_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )

    with urllib.request.urlopen(req) as resp:
        resp_data = json.loads(resp.read().decode("utf-8"))
        choices = resp_data.get("choices", [{}])
        if choices:
            message = choices[0].get("message", {})
            return message.get("content", "")
        return ""


def call_llm(prompt, model="gemini-3.7-flash", base_url=None, api_key=None, file_uri=None, image_base64_list=None, temperature=0.1, max_tokens=8192):
    """
    Unified LLM router supporting Gemini, OpenAI-compatible, and Local Models.
    """
    resolved_key = resolve_api_key(api_key, base_url, model)

    # If base_url is specified or model is explicitly non-gemini (e.g. gpt-5.6-luna, gemma-4)
    is_openai_compatible = bool(base_url) or not ("gemini" in model.lower())

    if is_openai_compatible:
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
    else:
        if not resolved_key:
            raise ValueError("Missing Gemini API Key. Pass --api-key or set GEMINI_API_KEY.")
        return call_gemini_generate_content(
            prompt=prompt,
            api_key=resolved_key,
            model=model,
            file_uri=file_uri,
            temperature=temperature,
            max_tokens=max_tokens
        )
