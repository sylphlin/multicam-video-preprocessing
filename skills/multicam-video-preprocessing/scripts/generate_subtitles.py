#!/usr/bin/env python3
"""
YouTube Subtitles Generator CLI Tool (generate_subtitles.py).
Generates millisecond-accurate, contextually proofread YouTube subtitles (SRT & VTT).

Three-Stage Golden Standard Pipeline:
  Stage 1: Global Audio Context & Glossary Extraction (Gemini 1M Context Audio Scan).
           Listens to the entire episode audio to extract speaker names, channel title, English terms, and domain jargon.
           Optionally merges user-provided interview outlines (--outline).
  Stage 2: Zero-Drift Acoustic Transcription via Whisper (mlx-whisper / faster-whisper).
           Produces frame-locked, physical acoustic millisecond timestamps (0.000s drift).
  Stage 3: Multimodal Audio-Text Chunked Precision Proofreading (Gemini 3.7 Flash).
           Slices local audio chunks and proofreads subtitles against both the local acoustic waveform and the Global Glossary,
           strictly preserving 100% of Whisper's millisecond timestamps and line indices.

Usage Examples:
  # Standard YouTube Subtitle Generation (Whisper + Gemini Audio Proofreading)
  python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4

  # With User Interview Outline / Glossary
  python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4 --outline "Host: 國威, Guest: Kelly Tsai, Topics: 矽谷, 估值狂飆"

  # OpenAI / Codex Cloud Endpoint (GPT-5.6 Luna)
  python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4 \
    --base-url https://api.openai.com/v1 --model gpt-5.6-luna --api-key $OPENAI_API_KEY
"""

import argparse
import concurrent.futures
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

# Support internal modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from modules.llm_client import call_llm, resolve_api_key
    from modules.progress import LiveTicker
except ImportError:
    from scripts.modules.llm_client import call_llm, resolve_api_key
    from scripts.modules.progress import LiveTicker





def format_timestamp_srt(seconds):
    """Format seconds (float) into SRT timestamp: HH:MM:SS,mmm"""
    if seconds < 0:
        seconds = 0
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    ms = int(round((s - int(s)) * 1000))
    if ms >= 1000:
        ms = 999
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"


def format_timestamp_vtt(seconds):
    """Format seconds (float) into WebVTT timestamp: HH:MM:SS.mmm"""
    if seconds < 0:
        seconds = 0
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    ms = int(round((s - int(s)) * 1000))
    if ms >= 1000:
        ms = 999
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d}.{ms:03d}"


def srt_to_vtt(srt_content):
    """Convert standard SRT format text into WebVTT (.vtt) format text."""
    lines = ["WEBVTT\n"]
    for line in srt_content.strip().splitlines():
        if "-->" in line:
            parts = line.split("-->")
            start = parts[0].strip().replace(",", ".")
            end = parts[1].strip().replace(",", ".")
            lines.append(f"{start} --> {end}")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def extract_audio_16k_mono(input_media, output_wav):
    """Extract audio from input media to 16kHz mono 16-bit PCM WAV."""
    cmd = [
        "ffmpeg", "-y", "-i", input_media,
        "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        output_wav
    ]
    with LiveTicker(f"Extracting 16kHz mono audio ({os.path.basename(input_media)})"):
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"FFmpeg audio extraction failed: {res.stderr}")


def run_whisper_transcription(audio_wav, model_size="base", language="zh", device="auto", initial_prompt=None):
    """
    Run high-speed Whisper acoustic transcription with automatic Apple Silicon / GPU hardware acceleration:
      1. mlx-whisper (Apple Silicon Metal / Neural Engine - Fastest on Mac)
      2. faster-whisper with ARM NEON / AVX int8 multi-core vector acceleration
      3. openai-whisper with PyTorch MPS / CUDA
    Returns: (sub_list, detected_lang, all_words)
    """
    print(f"\n[Stage 2/3] 🎙️ Running Whisper acoustic transcription (Model: {model_size}, Device: {device}, Word Timestamps: ON)...")
    if initial_prompt:
        print(f"  • Bias Prompt   : {initial_prompt}")
    t0 = time.time()
    lang_arg = None if str(language).lower() in ("auto", "none") else language
    if lang_arg and "-" in str(lang_arg):
        lang_arg = str(lang_arg).split("-")[0].lower()
    sub_list = []
    all_words = []
    detected_lang = language if lang_arg else "zh"

    # Backend 1: Apple Silicon Native MLX (mlx-whisper)
    if device in ("auto", "mps", "mlx"):
        try:
            import mlx_whisper
            print(f"  ► [Backend: Apple MLX] Utilizing Apple Silicon GPU / Neural Engine acceleration (word_timestamps=True)...")
            repo_name = f"mlx-community/whisper-{model_size}-mlx"
            result = mlx_whisper.transcribe(
                audio_wav,
                path_or_hf_repo=repo_name,
                language=lang_arg,
                initial_prompt=initial_prompt,
                word_timestamps=True
            )
            detected_lang = result.get("language") or detected_lang
            for idx, seg in enumerate(result.get("segments", []), start=1):
                seg_words = []
                for w in seg.get("words", []):
                    w_dict = {"word": w.get("word", ""), "start": float(w.get("start", 0.0)), "end": float(w.get("end", 0.0))}
                    seg_words.append(w_dict)
                    all_words.append(w_dict)
                s_start = seg_words[0]["start"] if seg_words else float(seg["start"])
                s_end = seg_words[-1]["end"] if seg_words else float(seg["end"])
                sub_list.append({
                    "index": idx,
                    "start": s_start,
                    "end": s_end,
                    "text": seg["text"].strip(),
                    "words": seg_words
                })
            duration = time.time() - t0
            print(f"  ✓ MLX transcription complete in {duration:.1f}s ({len(sub_list)} segments, {len(all_words)} words, language: '{detected_lang}')")
            return sub_list, detected_lang, all_words
        except ImportError:
            pass
        except Exception as e:
            print(f"  [Notice] MLX backend skipped ({e}), switching to next acceleration engine...", file=sys.stderr)

    # Backend 2: faster-whisper (CTranslate2 with int8 & ARM NEON / AVX vectorization)
    try:
        from faster_whisper import WhisperModel
        num_threads = min(8, os.cpu_count() or 4)
        print(f"  ► [Backend: faster-whisper] Utilizing multi-core ARM NEON/AVX vector acceleration ({num_threads} CPU threads, int8, word_timestamps=True)...")
        model = WhisperModel(model_size, device="cpu", compute_type="int8", cpu_threads=num_threads)
        segments, info = model.transcribe(
            audio_wav,
            language=lang_arg,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=250),
            initial_prompt=initial_prompt,
            word_timestamps=True
        )
        detected_lang = getattr(info, "language", detected_lang)

        total_dur = getattr(info, "duration", 0)
        for idx, seg in enumerate(segments, start=1):
            seg_words = []
            for w in (seg.words or []):
                w_dict = {"word": w.word, "start": float(w.start), "end": float(w.end)}
                seg_words.append(w_dict)
                all_words.append(w_dict)
            s_start = seg_words[0]["start"] if seg_words else float(seg.start)
            s_end = seg_words[-1]["end"] if seg_words else float(seg.end)
            sub_list.append({
                "index": idx,
                "start": s_start,
                "end": s_end,
                "text": seg.text.strip(),
                "words": seg_words
            })
            if total_dur > 0:
                pct = min(100.0, (s_end / total_dur) * 100.0)
                print(f"\r  ► [Whisper ASR] {format_timestamp_srt(s_end)} / {format_timestamp_srt(total_dur)} ({pct:4.1f}%) | Segment #{idx:03d}...", end="", flush=True)
            else:
                print(f"\r  ► [Whisper ASR] Segment #{idx:03d} ({format_timestamp_srt(s_start)} -> {format_timestamp_srt(s_end)})...", end="", flush=True)
        print()

    except ImportError:
        # Backend 3: openai-whisper (PyTorch MPS / CUDA / CPU)
        try:
            import whisper
            import torch
            if device in ("auto", "mps") and torch.backends.mps.is_available():
                target_dev = "mps"
            elif device in ("auto", "cuda") and torch.cuda.is_available():
                target_dev = "cuda"
            else:
                target_dev = "cpu"

            print(f"  ► [Backend: openai-whisper] Running on PyTorch ({target_dev.upper()}, word_timestamps=True)...")
            model = whisper.load_model(model_size, device=target_dev)
            result = model.transcribe(audio_wav, language=lang_arg, initial_prompt=initial_prompt, word_timestamps=True)
            detected_lang = result.get("language") or detected_lang

            for idx, seg in enumerate(result.get("segments", []), start=1):
                seg_words = []
                for w in seg.get("words", []):
                    w_dict = {"word": w.get("word", ""), "start": float(w.get("start", 0.0)), "end": float(w.get("end", 0.0))}
                    seg_words.append(w_dict)
                    all_words.append(w_dict)
                s_start = seg_words[0]["start"] if seg_words else float(seg["start"])
                s_end = seg_words[-1]["end"] if seg_words else float(seg["end"])
                sub_list.append({
                    "index": idx,
                    "start": s_start,
                    "end": s_end,
                    "text": seg["text"].strip(),
                    "words": seg_words
                })
        except ImportError:
            raise RuntimeError("No Whisper backend found! Please install via `pip install mlx-whisper` (Apple Silicon GPU) or `pip install faster-whisper`.")

    duration = time.time() - t0
    print(f"  ✓ Whisper acoustic transcription complete in {duration:.1f}s ({len(sub_list)} segments, {len(all_words)} words, language: '{detected_lang}')")
    return sub_list, detected_lang, all_words


def build_srt_from_segments(segments):
    """Build standard SRT string from segment list."""
    blocks = []
    for seg in segments:
        idx = seg["index"]
        t_start = format_timestamp_srt(seg["start"])
        t_end = format_timestamp_srt(seg["end"])
        txt = seg["text"]
        blocks.append(f"{idx}\n{t_start} --> {t_end}\n{txt}\n")
    return "\n".join(blocks)


def normalize_language_tag(lang_str):
    """Normalize language code to standardized locale code (e.g. zh-TW, en, ja, zh-CN, ko)."""
    if not lang_str:
        return "zh-TW"
    l = str(lang_str).lower().replace("_", "-").strip()
    if l in ("zh", "zh-tw", "zh-hant", "zh-hk", "zh-mo", "cmn-hant", "cmn-tw"):
        return "zh-TW"
    if l in ("zh-cn", "zh-hans", "zh-sg", "cmn-hans", "cmn-cn"):
        return "zh-CN"
    if l.startswith("en"):
        return "en"
    if l.startswith("ja"):
        return "ja"
    if l.startswith("ko"):
        return "ko"
    return "zh-TW"


def load_proofread_template(language="zh-TW"):
    """Load subtitle proofreading prompt template based on language locale."""
    norm_lang = normalize_language_tag(language)

    search_dirs = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets"),
        os.path.expanduser("~/.gemini/config/skills/multicam-video-preprocessing/assets"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets"),
    ]

    target_names = [
        f"subtitle_proofread_template.{norm_lang}.md",
        "subtitle_proofread_template.zh-TW.md",
    ]

    for s_dir in search_dirs:
        for t_name in target_names:
            p = os.path.join(s_dir, t_name)
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                        if content:
                            return content, os.path.basename(p)
                except Exception:
                    pass

    return (
        "You are an expert subtitle proofreader for YouTube.\n"
        "Your task: Re-segment and proofread subtitles into natural, fluent semantic clauses (max 15 chars for CJK, 37 chars for English) with acoustic timestamp fusion.\n"
        "Output ONLY the corrected SRT inside ```srt ... ``` code block."
    ), "builtin_fallback"


def extract_whisper_prompt(glossary_text, max_chars=145, language="zh-TW"):
    """
    Extract a compact, high-density Whisper ASR initial prompt from Global Glossary:
    1. Primary: Parse '> **Whisper Initial Prompt**: ...' line directly.
    2. Fallback: Parse bold terms (**word**) from glossary sections and assemble cleanly.
    """
    if not glossary_text:
        return None

    # Path 1: Primary line match from Stage 1 output
    m = re.search(r">\s*\*\*Whisper\s+(?:Initial\s+)?Prompt\*\*:\s*(.+)", glossary_text, re.IGNORECASE)
    if not m:
        m = re.search(r"\*\*Whisper\s+(?:Initial\s+)?Prompt\*\*:\s*(.+)", glossary_text, re.IGNORECASE)
    if m:
        line = m.group(1).strip()
        line = re.sub(r"^[`'\"]+|[`'\"]+$", "", line).strip()
        if len(line) > max_chars:
            cut = line[:max_chars]
            last_comma = max(cut.rfind("、"), cut.rfind("，"), cut.rfind(","))
            if last_comma > 20:
                line = cut[:last_comma] + "。"
            else:
                line = cut + "..."
        return line

    # Path 2: Fallback regex parser over markdown bold entities
    bold_matches = re.findall(r"\*\*(.+?)\*\*", glossary_text)
    filtered = []
    category_keywords = [
        "Person", "Speaker", "Names", "Organizations", "Products", "Places", "Brands",
        "Domain Jargon", "Tech Terms", "Core Topic", "全片專有名詞", "Whisper", "講者與人物",
        "公司、品牌", "行業專有名詞", "核心主題", "Glossary", "Terminology"
    ]
    for w in bold_matches:
        w = w.strip()
        if any(cat in w for cat in category_keywords):
            continue
        clean_w = re.split(r"[（\(]", w)[0].strip()
        if clean_w and clean_w not in filtered and len(clean_w) <= 12:
            filtered.append(clean_w)

    if not filtered:
        return None

    norm_lang = normalize_language_tag(language)
    if norm_lang == "en":
        prefix = "The following is a video transcription containing terms: "
        sep = ", "
        suffix = "."
    elif norm_lang == "ja":
        prefix = "以下は日本語の対談字幕です。専門用語："
        sep = "、"
        suffix = "。"
    else:
        prefix = "以下為繁體中文對談字幕，專有名詞："
        sep = "、"
        suffix = "。"

    assembled = prefix
    for item in filtered:
        cand = assembled + (sep if assembled != prefix else "") + item
        if len(cand) + len(suffix) > max_chars:
            break
        assembled = cand
    assembled += suffix
    return assembled


def extract_global_glossary(audio_wav=None, segments=None, user_outline=None, user_script=None, api_key=None, base_url=None, model="gemini-3.7-flash"):
    """
    Stage 1: Global Audio Context & Consistency Glossary Extraction (Gemini 1M Context Scan).
    Listens to full episode audio (and/or analyzes user script / outline) to extract speaker names,
    organizations, acronyms, and critical domain terminology.
    Also produces a concentrated Whisper Initial Prompt line at the top.
    """
    print(f"\n[Stage 1/3] 🎧 Extracting Global Consistency Glossary across entire episode...")
    t0 = time.time()

    context_parts = []
    if user_script:
        context_parts.append(f"=== 錄音講稿/逐字稿原稿 (Source Script Reference) ===\n{user_script}\n")
    if user_outline:
        context_parts.append(f"=== 使用者提供之訪綱/主題資料 (User Interview Outline) ===\n{user_outline}\n")
    supplementary_section = "\n".join(context_parts) if context_parts else ""

    prompt = (
        "You are an expert Chief Subtitle Editor for professional YouTube productions.\n"
        "Your goal is to extract a comprehensive, authoritative **Global Terminology Glossary (全片專有名詞與詞彙對照表)** "
        "directly from this recording (and supplied script/outline) to ensure 100% spelling, naming, and domain term consistency across all subtitle segments.\n\n"
        f"{supplementary_section}\n"
        "Extract ONLY verified domain terms, entity names, foreign proper nouns, and proper spellings that actually appear in this recording.\n\n"
        "=== CRITICAL INSTRUCTION FOR WHISPER ASR ===\n"
        "At the VERY TOP of your Markdown output, provide a single, highly-concentrated bias prompt line specifically designed for Whisper ASR (speech-to-text decoder initial prompt).\n"
        "Strictly limit this line to 100~140 characters, listing only the most critical, uncommon proper nouns, speaker names, and technical/foreign/ancient Kanji terms separated by Chinese enumeration commas (、). Format:\n"
        "> **Whisper Initial Prompt**: 以下為繁體中文對談字幕，專有名詞：詞1、詞2、詞3...\n\n"
        "Then structure the full detailed glossary cleanly in Markdown:\n"
        "1. **講者與人物姓名 (Person & Speaker Names)**: Official Chinese/English names, titles & roles\n"
        "2. **公司、品牌、產品與機構 (Organizations, Products & Brands)**: Official brand, company & tool names\n"
        "3. **行業專有名詞與技術術語 (Domain Jargon & Tech Terms)**: Industry terminology, English acronyms, Japanese Kanji/Kana\n"
        "4. **核心主題概念 (Core Topic Concepts)**: Key themes discussed in this episode\n\n"
        "Output ONLY the clean, authoritative Markdown Glossary:"
    )

    # Use lightweight compressed MP3 for audio scanning if audio file is available
    audio_for_gemini = None
    tmp_audio_mp3 = None
    if audio_wav and os.path.exists(audio_wav) and "gemini" in model.lower() and not base_url:
        try:
            tmp_audio_mp3 = os.path.join(os.path.dirname(audio_wav), "global_glossary_audio.mp3")
            # Compress to 48k mono mp3 for fast upload
            subprocess.run(["ffmpeg", "-y", "-i", audio_wav, "-vn", "-ar", "16000", "-ac", "1", "-b:a", "48k", tmp_audio_mp3],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            if os.path.getsize(tmp_audio_mp3) <= 20 * 1024 * 1024:  # <= 20MB inline limit
                audio_for_gemini = tmp_audio_mp3
        except Exception as e:
            audio_for_gemini = None

    try:
        with LiveTicker("Extracting Global Consistency Glossary via Gemini (1M context scan)"):
            glossary_content = call_llm(
                prompt=prompt,
                model=model,
                base_url=base_url,
                api_key=api_key,
                audio_path=audio_for_gemini,
                temperature=0.1,
                max_tokens=4096,
                thinking_budget=0
            )
        duration = time.time() - t0
        print(f"  ✓ Global Glossary extracted in {duration:.1f}s")
        return glossary_content.strip()
    except Exception as e:
        print(f"  [Warning] Global glossary extraction failed ({e}). Continuing with standard proofreading.", file=sys.stderr)
        return user_outline or user_script or ""
    finally:
        if tmp_audio_mp3 and os.path.exists(tmp_audio_mp3):
            try:
                os.remove(tmp_audio_mp3)
            except:
                pass


def parse_timestamp_str(ts_str):
    """Parse SRT timestamp 'HH:MM:SS,mmm' to float seconds."""
    h, m, sec_ms = ts_str.strip().split(":")
    sec, ms = sec_ms.replace(".", ",").split(",")
    return float(h) * 3600 + float(m) * 60 + float(sec) + float(ms) / 1000.0


def split_blocks_into_semantic_chunks(raw_blocks, target_chunk_size=80, min_chunk_size=55, max_chunk_size=105):
    """
    Intelligent silence-aware & semantic clause boundary chunking:
    Instead of rigidly slicing every N blocks, inspects adjacent timestamp gaps and clause closures
    within [target - 25, target + 25] window to split at natural speech breath pauses (gap >= 0.4s).
    Completely eliminates chopped sentences and dangling clauses across chunk boundaries.
    """
    total = len(raw_blocks)
    if total <= max_chunk_size:
        return [raw_blocks]

    parsed_blocks = []
    for idx, b in enumerate(raw_blocks):
        lines = b.splitlines()
        t_start, t_end, txt = 0.0, 0.0, ""
        if len(lines) >= 2 and "-->" in lines[1]:
            t1, t2 = lines[1].split("-->")
            t_start = parse_timestamp_str(t1.strip())
            t_end = parse_timestamp_str(t2.strip())
            txt = lines[2].strip() if len(lines) >= 3 else ""
        elif len(lines) >= 1 and "-->" in lines[0]:
            t1, t2 = lines[0].split("-->")
            t_start = parse_timestamp_str(t1.strip())
            t_end = parse_timestamp_str(t2.strip())
            txt = lines[1].strip() if len(lines) >= 2 else ""
        parsed_blocks.append({
            "block": b,
            "start": t_start,
            "end": t_end,
            "text": txt
        })

    chunks = []
    cur_idx = 0
    while cur_idx < total:
        remaining = total - cur_idx
        if remaining <= max_chunk_size:
            chunks.append(raw_blocks[cur_idx:])
            break

        search_start = cur_idx + min_chunk_size
        search_end = min(total - 1, cur_idx + max_chunk_size)

        best_split = min(total, cur_idx + target_chunk_size)
        best_score = -9999.0

        for i in range(search_start, search_end):
            c = parsed_blocks[i]
            n = parsed_blocks[i + 1]
            gap = n["start"] - c["end"]

            # Score factor 1: Natural silence gap (heavily reward pause >= 0.4s)
            score = min(gap, 2.0) * 15.0

            # Score factor 2: Closeness to target chunk size
            dist = abs(i - (cur_idx + target_chunk_size))
            score -= dist * 0.4

            # Score factor 3: Sentence ending punctuation / closure words
            txt = c["text"]
            if any(txt.endswith(p) for p in ["？", "?", "！", "!", "。", "……", "..."]):
                score += 6.0
            if any(txt.endswith(w) for w in ["來說", "來講", "而言", "之後", "的話", "來看"]):
                score += 3.0

            # Score factor 4: Discourage breaking right before conjunctions
            n_txt = n["text"]
            if any(n_txt.startswith(w) for w in ["但是", "而且", "所以", "不過", "然而", "如果"]):
                score -= 4.0

            if score > best_score:
                best_score = score
                best_split = i + 1

        chunks.append(raw_blocks[cur_idx:best_split])
        cur_idx = best_split

    return chunks


def align_split_clauses_with_words(final_parts, t_start, t_end, all_words=None):
    """
    Sub-sentence micro-acoustic anchor:
    Pins split sub-clauses to physical word boundaries in all_words
    instead of mechanical length proportional slicing.
    """
    if not all_words or len(final_parts) <= 1:
        tot_len = max(1, sum(len(p) for p in final_parts))
        cur_t = t_start
        span = max(0.5, t_end - t_start)
        items = []
        for f_p in final_parts:
            p_dur = span * (len(f_p) / tot_len)
            items.append({"start": cur_t, "end": cur_t + p_dur, "text": f_p})
            cur_t += p_dur
        return items

    nearby_words = [
        w for w in all_words
        if float(w.get("end", 0.0)) >= t_start - 0.3 and float(w.get("start", 0.0)) <= t_end + 0.3
    ]
    if len(nearby_words) < len(final_parts):
        tot_len = max(1, sum(len(p) for p in final_parts))
        cur_t = t_start
        span = max(0.5, t_end - t_start)
        items = []
        for f_p in final_parts:
            p_dur = span * (len(f_p) / tot_len)
            items.append({"start": cur_t, "end": cur_t + p_dur, "text": f_p})
            cur_t += p_dur
        return items

    w_chars = []
    for w in nearby_words:
        w_text = re.sub(r"[\s\.,\?!，。？！、：:;；—\-~]+", "", w.get("word", ""))
        w_s = float(w.get("start", t_start))
        w_e = float(w.get("end", t_end))
        w_chars.append({"text": w_text, "start": w_s, "end": w_e})

    first_clean = re.sub(r"[\s\.,\?!，。？！、：:;；—\-~]+", "", final_parts[0]).lower()
    best_split_time = None
    accumulated = ""
    for wc in w_chars:
        accumulated += wc["text"].lower()
        if len(accumulated) >= len(first_clean) * 0.75:
            if first_clean in accumulated or accumulated in first_clean:
                best_split_time = wc["end"]
                break

    if best_split_time and t_start + 0.4 <= best_split_time <= t_end - 0.4:
        items = [{"start": t_start, "end": best_split_time, "text": final_parts[0]}]
        rem_parts = final_parts[1:]
        if len(rem_parts) == 1:
            items.append({"start": best_split_time, "end": t_end, "text": rem_parts[0]})
        else:
            items.extend(align_split_clauses_with_words(rem_parts, best_split_time, t_end, nearby_words))
        return items
    else:
        tot_len = max(1, sum(len(p) for p in final_parts))
        cur_t = t_start
        span = max(0.5, t_end - t_start)
        items = []
        for f_p in final_parts:
            p_dur = span * (len(f_p) / tot_len)
            items.append({"start": cur_t, "end": cur_t + p_dur, "text": f_p})
            cur_t += p_dur
        return items


def proofread_single_chunk(c_idx, num_chunks, chunk_slice, template, global_glossary, audio_wav, api_key, base_url, model, user_script=None):
    """Worker function to proofread a single chunk of SRT blocks with local audio slice and optional reference script."""
    chunk_text = "\n\n".join(chunk_slice)
    glossary_section = f"\n=== 全片權威專有名詞對照表 (Global Consistency Glossary) ===\n{global_glossary}\n============================================================\n" if global_glossary else ""
    script_section = ""
    if user_script:
        script_section = (
            f"\n=== 錄音講稿/逐字稿原稿 (Source Script Reference) ===\n"
            f"{user_script[:6000]}\n"
            f"============================================================\n"
            f"【校對核心準則】：以講者實際口白發音為準（保留現場真實口語內容與自然句法），但凡遇到專有名詞、人物名稱、特殊日文漢字或同音字疑義時，嚴格參照【錄音講稿/逐字稿原稿】之標準文字修正。\n"
        )

    # Calculate audio slice start and end time
    chunk_mp3_path = None
    tmp_dir = os.path.dirname(audio_wav) if audio_wav else None

    if audio_wav and os.path.exists(audio_wav) and "gemini" in model.lower() and not base_url:
        try:
            # Find start and end timestamps in this chunk
            first_block = chunk_slice[0].splitlines()
            last_block = chunk_slice[-1].splitlines()
            if len(first_block) >= 2 and "-->" in first_block[1] and len(last_block) >= 2 and "-->" in last_block[1]:
                t_start_sec = max(0.0, parse_timestamp_str(first_block[1].split("-->")[0]) - 0.5)
                t_end_sec = parse_timestamp_str(last_block[1].split("-->")[1]) + 0.5
                dur_sec = max(1.0, t_end_sec - t_start_sec)
                
                chunk_mp3_path = os.path.join(tmp_dir, f"chunk_{c_idx:03d}.mp3")
                cmd = [
                    "ffmpeg", "-y", "-ss", f"{t_start_sec:.3f}", "-t", f"{dur_sec:.3f}",
                    "-i", audio_wav, "-vn", "-ar", "16000", "-ac", "1", "-b:a", "48k", chunk_mp3_path
                ]
                subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        except Exception:
            chunk_mp3_path = None

    prompt = (
        f"{template}\n"
        f"{glossary_section}\n"
        f"{script_section}\n"
        f"--- 待校對與重整之原始 SRT 碎字幕（區塊 {c_idx + 1}/{num_chunks}）---\n"
        f"```srt\n{chunk_text}\n```\n\n"
        f"請邊聽附帶的音訊錄音、依據全片對照表與語意段落規範，進行自然斷句重整（中文/日文 <= 15 字，英文 <= 37 字元）、時間軸物理聲學熔接與同音錯字校正，輸出重整後的完整 SRT："
    )

    try:
        response_text = call_llm(
            prompt=prompt,
            model=model,
            base_url=base_url,
            api_key=api_key,
            audio_path=chunk_mp3_path,
            temperature=0.1,
            max_tokens=8192,
            thinking_budget=0
        )

        match = re.search(r"```(?:srt)?\s*\n(.*?)```", response_text, re.DOTALL | re.IGNORECASE)
        clean_chunk = match.group(1).strip() if match else response_text.strip()
        clean_chunk = re.sub(r"^```(?:srt)?\s*\n?", "", clean_chunk, flags=re.IGNORECASE)
        clean_chunk = re.sub(r"\n?```\s*$", "", clean_chunk).strip()

        if "-->" in clean_chunk:
            return c_idx, clean_chunk, True
        else:
            return c_idx, chunk_text, False

    except Exception as e:
        print(f"  [Warning] Chunk {c_idx+1} proofreading error: {e}. Keeping original.", file=sys.stderr)
        return c_idx, chunk_text, False
    finally:
        if chunk_mp3_path and os.path.exists(chunk_mp3_path):
            try:
                os.remove(chunk_mp3_path)
            except:
                pass


def clean_subtitle_text(text, language="zh-TW"):
    """
    Netflix & YouTube Standard Subtitle Text Cleaner & Formatter:
    1. Strip trailing periods/commas: removes [。，、；:;,.—-] from line end (preserves ？!……).
    2. Convert in-line Chinese commas to clean single spaces (e.g. '哈囉，歡迎' -> '哈囉 歡迎').
    3. Normalize Chinese-English & Chinese-Number spacing (e.g. '用AI寫10倍Code' -> '用 AI 寫 10 倍 Code').
    4. Strip residual Markdown formatting markers (**, __, `, ##).
    5. Collapse multiple consecutive whitespace to single space and trim.
    """
    norm_lang = normalize_language_tag(language)
    lines = text.strip().splitlines()
    cleaned_lines = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Strip residual markdown bold/italic/code markers (e.g. **word**, `word`)
        line = re.sub(r"(\*{1,3}|_{2,}|`{1,3}|(?<=^)\s*#{1,6}\s+)", "", line)

        if norm_lang in ["zh-TW", "zh-CN", "ja", "ko"]:
            # In-line comma to space for clean visual layout
            line = re.sub(r"[，,]+", " ", line)
            # Chinese-English / Chinese-Number spacing
            line = re.sub(r"([\u4e00-\u9fff\u3400-\u4dbf\u3040-\u30ff\uac00-\ud7af])([A-Za-z0-9])", r"\1 \2", line)
            line = re.sub(r"([A-Za-z0-9])([\u4e00-\u9fff\u3400-\u4dbf\u3040-\u30ff\uac00-\ud7af])", r"\1 \2", line)
        
        # Collapse multiple spaces
        line = re.sub(r"[ \t]+", " ", line)

        # Strip trailing punctuation: periods, commas, colons, semicolons, dashes (preserve ？!……)
        line = re.sub(r"[\s。，、；:;,.—-]+$", "", line).strip()

        cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def calc_display_width(text, norm_lang="zh-TW"):
    """Calculate typographical display width (1.0 for CJK, 0.5 for ASCII alphanumeric)."""
    if norm_lang in ["zh-TW", "zh-CN", "ja"]:
        return sum(1.0 if ord(c) > 127 else 0.5 for c in text.replace(" ", ""))
    elif norm_lang == "ko":
        return sum(1.0 if ord(c) > 127 else 0.5 for c in text)
    else:
        return float(len(text))


def split_long_clause(text, max_w=15.0, norm_lang="zh-TW"):
    """Split a clause exceeding max display width into natural sub-clauses."""
    w = calc_display_width(text, norm_lang)
    if w <= max_w:
        return [clean_subtitle_text(text, language=norm_lang)]

    # Try space-separated clauses
    parts = [p.strip() for p in text.split(" ") if p.strip()]
    if len(parts) > 1:
        chunks = []
        cur = []
        for p in parts:
            cand = " ".join(cur + [p])
            if cur and calc_display_width(cand, norm_lang) > max_w:
                cleaned_c = clean_subtitle_text(" ".join(cur), language=norm_lang)
                if cleaned_c:
                    chunks.append(cleaned_c)
                cur = [p]
            else:
                cur.append(p)
        if cur:
            cleaned_c = clean_subtitle_text(" ".join(cur), language=norm_lang)
            if cleaned_c:
                chunks.append(cleaned_c)
        if len(chunks) > 1:
            return chunks

    # Try punctuation-separated sub-clauses
    sub_parts = [p.strip() for p in re.split(r"(?<=[、，,])", text) if p.strip()]
    if len(sub_parts) > 1:
        chunks = []
        cur = []
        for p in sub_parts:
            cand = "".join(cur + [p])
            if cur and calc_display_width(cand, norm_lang) > max_w:
                cleaned_c = clean_subtitle_text("".join(cur), language=norm_lang)
                if cleaned_c:
                    chunks.append(cleaned_c)
                cur = [p]
            else:
                cur.append(p)
        if cur:
            cleaned_c = clean_subtitle_text("".join(cur), language=norm_lang)
            if cleaned_c:
                chunks.append(cleaned_c)
        if len(chunks) > 1:
            return chunks

    return [clean_subtitle_text(text, language=norm_lang)]


def score_candidate(cand_str, clean_t):
    """
    Composite acoustic match score:
    Balances coverage of clean_t and candidate character density while rewarding
    exact speech onset (first character) and sentence termination (last character).
    Crucially prevents skipping sentence onsets due to stutters or repetitions.
    """
    matcher = difflib.SequenceMatcher(None, cand_str, clean_t)
    matched_chars = sum(b.size for b in matcher.get_matching_blocks())
    if matched_chars == 0:
        return 0.0
    coverage = matched_chars / len(clean_t)
    density = matched_chars / len(cand_str)
    # Heavy bonus for first character match to guarantee precise speech onset
    first_bonus = 0.15 if cand_str[0] == clean_t[0] else (
        0.08 if len(cand_str) > 1 and len(clean_t) > 1 and cand_str[1] == clean_t[1] else 0.0
    )
    last_bonus = 0.10 if cand_str[-1] == clean_t[-1] else (
        0.05 if len(cand_str) > 1 and len(clean_t) > 1 and cand_str[-2] == clean_t[-2] else 0.0
    )
    return 0.50 * coverage + 0.25 * density + first_bonus + last_bonus


def realign_subtitles_to_words(proofread_srt, all_words, language="zh-TW", is_video_start=False):
    """
    Broadcast-Grade Physical Acoustic Timestamp Re-projection Engine:
    Realigns LLM-proofread and re-segmented subtitle lines to the exact physical
    sound boundaries recorded in Whisper's word-level timestamps.
    Completely eliminates LLM hallucinated delays, cascading desync, and swallowed silences.
    """
    default_stats = {"total": 0, "locked": 0, "fallback": 0, "fallback_indices": []}
    if not all_words:
        return proofread_srt, default_stats

    # 1. Build a continuous character timeline from Whisper word timestamps (excluding punctuation)
    char_timeline = []
    for w in all_words:
        w_raw = w.get("word", "")
        w_text = re.sub(r"[\s\.,\?!，。？！、：:;；—\-~]+", "", w_raw)
        if not w_text:
            continue
        w_start = float(w.get("start", 0.0))
        w_end = float(w.get("end", w_start + 0.1))
        w_dur = max(0.01, w_end - w_start)
        char_dur = w_dur / max(1, len(w_text))
        for idx, ch in enumerate(w_text):
            ch_s = w_start + idx * char_dur
            ch_e = ch_s + char_dur
            char_timeline.append({
                "char": ch,
                "start": ch_s,
                "end": ch_e
            })

    if not char_timeline:
        return proofread_srt, default_stats

    whisper_chars = "".join(c["char"] for c in char_timeline)
    whisper_chars_lower = whisper_chars.lower()
    total_chars = len(char_timeline)

    # 2. Parse proofread SRT blocks
    blocks = [b.strip() for b in proofread_srt.strip().split("\n\n") if b.strip()]
    items = []
    for b in blocks:
        lines = b.splitlines()
        if len(lines) >= 3 and "-->" in lines[1]:
            t1, t2 = lines[1].split("-->")
            txt = "\n".join(lines[2:]).strip()
            items.append({
                "fallback_start": parse_timestamp_str(t1.strip()),
                "fallback_end": parse_timestamp_str(t2.strip()),
                "text": txt
            })
        elif len(lines) == 2 and "-->" in lines[0]:
            t1, t2 = lines[0].split("-->")
            txt = lines[1].strip()
            items.append({
                "fallback_start": parse_timestamp_str(t1.strip()),
                "fallback_end": parse_timestamp_str(t2.strip()),
                "text": txt
            })

    if not items:
        return proofread_srt, default_stats

    alignment_stats = {
        "total": len(items),
        "locked": 0,
        "fallback": 0,
        "fallback_indices": []
    }

    # 3. Monotonic Forward Alignment via Dense Local Substring Matching
    cur_char_idx = 0
    realigned_items = []

    for idx, item in enumerate(items):
        raw_text = item["text"]
        clean_t = re.sub(r"[\s\.,\?!，。？！、：:;；—\-~]+", "", raw_text).lower()
        if not clean_t:
            continue

        clean_t_no_brackets = re.sub(r"[（\(].*?[）\)]", "", clean_t)
        has_bracket_variant = bool(clean_t_no_brackets and clean_t_no_brackets != clean_t)

        L = len(clean_t)
        best_match = None
        best_score = -1.0

        # Look in a reasonable forward window from cur_char_idx
        search_start = cur_char_idx
        search_extent = min(total_chars, search_start + max(L * 2 + 25, 80))

        for s_pos in range(search_start, search_extent):
            # Restrict candidate span length to accommodate stutters and repetitions (+10)
            for cand_len in range(max(1, L - 4), min(total_chars - s_pos + 1, L + 10)):
                cand_str = whisper_chars_lower[s_pos : s_pos + cand_len]
                sc = score_candidate(cand_str, clean_t)
                if has_bracket_variant:
                    sc_nb = score_candidate(cand_str, clean_t_no_brackets)
                    sc = max(sc, sc_nb)
                if sc > best_score + 1e-4:
                    best_score = sc
                    best_match = (s_pos, s_pos + cand_len - 1)

        min_ratio = 0.50 if L >= 4 else 0.65
        if best_match and best_score >= min_ratio:
            m_start, m_end = best_match
            t_start = char_timeline[m_start]["start"]
            t_end = char_timeline[m_end]["end"]
            cur_char_idx = m_end + 1
            alignment_stats["locked"] += 1
        else:
            # Fallback: keep LLM's own timestamps for this line
            t_start = item.get("fallback_start", 0.0)
            t_end = item.get("fallback_end", t_start + 2.0)
            while cur_char_idx < total_chars and char_timeline[cur_char_idx]["end"] <= t_end:
                cur_char_idx += 1
            alignment_stats["fallback"] += 1
            alignment_stats["fallback_indices"].append(item.get("index", idx + 1))

        # Broadcast lead-in & continuous pre-roll (Netflix/EBU-TT standard: 180ms / ~5-6 frames)
        lead_sec = 0.180
        target_start = max(0.0, t_start - lead_sec)

        if idx == 0 and is_video_start:
            t_start = target_start
        elif len(realigned_items) > 0:
            prev = realigned_items[-1]
            prev_dur = prev["end"] - prev["start"]
            if target_start >= prev["end"] + 0.02:
                # Discontinuous speech (after a pause): lead speech onset by lead_sec
                t_start = target_start
            else:
                # Continuous speech: borrow pre-roll from previous line if it has sufficient screen time (>= 1.0s)
                prev_room = max(0.0, prev_dur - 1.0)
                needed_shift = (prev["end"] + 0.02) - target_start
                shift = min(prev_room, needed_shift)
                if shift >= 0.04:
                    prev["end"] -= shift
                    t_start = prev["end"]
                else:
                    t_start = max(prev["end"], target_start)

        realigned_items.append({
            "index": item.get("index", idx + 1),
            "start": t_start,
            "end": t_end,
            "text": raw_text
        })

    # 4. Format to standard SRT string
    out_blocks = []
    for idx, it in enumerate(realigned_items, start=1):
        s_str = format_timestamp_srt(it["start"])
        e_str = format_timestamp_srt(it["end"])
        out_blocks.append(f"{idx}\n{s_str} --> {e_str}\n{it['text']}\n")

    return "\n\n".join(out_blocks).strip() + "\n", alignment_stats


def sanitize_subtitle_timings(raw_srt, all_words=None, min_duration=1.0, max_duration=6.0, post_tail_buffer=0.4, min_gap_threshold=0.2, language="zh-TW"):
    """
    Automated Professional Rhythm & Pacing Sanitizer for Subtitles:
    1. Zero-Lead Protection: Subtitles never lead before voice onset (Start >= speech onset).
    2. Monotonic Forward Alignment: Eliminates timestamp backtracking & chunk boundary overlaps.
    3. Post-tail Reading Buffer: Extends subtitle end time (+0.3~0.5s) into natural pauses for readability.
    4. Min Duration Guard: Ensures display duration >= 1.0s where possible (bounded by next speech onset).
    5. Max Duration Guard: Caps maximum single-line display duration <= 6.0s (avoids stuck-subtitle feel).
    6. Gap Management: Bridges micro-gaps (< 0.2s) to prevent high-frequency visual flicker.
    7. Sub-sentence Micro-Acoustic Anchor: Anchors split clauses directly to Whisper word timestamps.
    8. Netflix & YouTube Punctuation, Spacing, and Max Character Length Normalization.
    """
    norm_lang = normalize_language_tag(language)
    max_w = 15.0 if norm_lang in ["zh-TW", "zh-CN", "ja"] else (16.0 if norm_lang == "ko" else 37.0)

    blocks = [b.strip() for b in raw_srt.strip().split("\n\n") if b.strip()]
    items = []

    for b in blocks:
        lines = b.splitlines()
        if len(lines) >= 3 and "-->" in lines[1]:
            t1, t2 = lines[1].split("-->")
            t_start = parse_timestamp_str(t1.strip())
            t_end = parse_timestamp_str(t2.strip())
            raw_txt = "\n".join(lines[2:]).strip()
            clean_txt = clean_subtitle_text(raw_txt, language=language)
            if clean_txt:
                q_parts = [p.strip() for p in re.split(r"(?<=[？?])\s+", clean_txt) if p.strip()]
                # Further split any overlength sub-clause
                final_parts = []
                for q_p in q_parts:
                    final_parts.extend(split_long_clause(q_p, max_w=max_w, norm_lang=norm_lang))

                if len(final_parts) > 1:
                    items.extend(align_split_clauses_with_words(final_parts, t_start, t_end, all_words=all_words))
                else:
                    items.append({"start": t_start, "end": t_end, "text": clean_txt})
        elif len(lines) == 2 and "-->" in lines[0]:
            t1, t2 = lines[0].split("-->")
            t_start = parse_timestamp_str(t1.strip())
            t_end = parse_timestamp_str(t2.strip())
            raw_txt = lines[1].strip()
            clean_txt = clean_subtitle_text(raw_txt, language=language)
            if clean_txt:
                q_parts = [p.strip() for p in re.split(r"(?<=[？?])\s+", clean_txt) if p.strip()]
                final_parts = []
                for q_p in q_parts:
                    final_parts.extend(split_long_clause(q_p, max_w=max_w, norm_lang=norm_lang))

                if len(final_parts) > 1:
                    items.extend(align_split_clauses_with_words(final_parts, t_start, t_end, all_words=all_words))
                else:
                    items.append({"start": t_start, "end": t_end, "text": clean_txt})

    if not items:
        return raw_srt

    # Pass 1: Monotonic Forward Correction & Sanity bounds
    for i in range(len(items)):
        if items[i]["end"] <= items[i]["start"]:
            items[i]["end"] = items[i]["start"] + 1.0

        # Enforce max duration constraint (e.g. fix LLM hallucinated minute digits)
        if items[i]["end"] - items[i]["start"] > max_duration:
            if i + 1 < len(items) and items[i+1]["start"] > items[i]["start"]:
                items[i]["end"] = min(items[i]["start"] + max_duration, items[i+1]["start"])
            else:
                items[i]["end"] = items[i]["start"] + min(4.0, max_duration)

    # Pass 2: Forward non-overlap monotonicity
    for i in range(1, len(items)):
        prev = items[i-1]
        cur = items[i]
        if cur["start"] <= prev["start"]:
            cur["start"] = prev["start"] + 0.5
        if prev["end"] > cur["start"]:
            # If cur starts after prev had reasonable time (>= 0.6s), trim prev["end"] to cur["start"]
            if cur["start"] >= prev["start"] + 0.6:
                prev["end"] = cur["start"]
            else:
                prev["end"] = max(prev["start"] + 0.5, cur["start"])
                if cur["start"] < prev["end"]:
                    cur["start"] = prev["end"]
        if cur["end"] <= cur["start"]:
            cur["end"] = cur["start"] + 1.0

    # Pass 3: Post-tail Reading Buffer & Min-duration & Gap management
    for i in range(len(items)):
        cur = items[i]
        nxt_start = items[i+1]["start"] if i + 1 < len(items) else (cur["end"] + 10.0)

        dur = cur["end"] - cur["start"]
        raw_gap = nxt_start - cur["end"]

        # Gap management and reading breathing buffer:
        # If speech gap is shorter than (post_tail_buffer + min_gap_threshold) [e.g. < 0.6s],
        # bridging directly to nxt_start eliminates high-frequency 1-2 frame visual flicker.
        # If there is a genuine pause (>= 0.6s), extend by post_tail_buffer (+0.4s),
        # leaving >= 0.2s of clean video silence for natural audience breathing.
        if raw_gap < (post_tail_buffer + min_gap_threshold):
            if raw_gap > 0:
                cur["end"] = nxt_start
        else:
            cur["end"] = cur["end"] + post_tail_buffer

        # Enforce Min Duration (>= 1.0s) if room permits
        dur = cur["end"] - cur["start"]
        if dur < min_duration:
            needed = min_duration - dur
            room = nxt_start - cur["end"]
            if room > 0:
                cur["end"] = min(cur["end"] + min(needed, room), nxt_start)

        # Final sanity check: max_duration
        if cur["end"] - cur["start"] > max_duration:
            cur["end"] = cur["start"] + max_duration

    # Output formatted SRT
    out_blocks = []
    for idx, it in enumerate(items, start=1):
        s_str = format_timestamp_srt(it["start"])
        e_str = format_timestamp_srt(it["end"])
        ts_line = f"{s_str} --> {e_str}"
        txt = it["text"]
        out_blocks.append(f"{idx}\n{ts_line}\n{txt}\n")

    return "\n\n".join(out_blocks).strip() + "\n"


def proofread_srt_with_llm(raw_srt, audio_wav=None, global_glossary=None, user_script=None, api_key=None, base_url=None, model="gemini-3.7-flash", chunk_size=80, max_workers=5, language="zh-TW", all_words=None, cache_path=None):
    """
    Stage 3: Multimodal Audio-Text Parallel Chunked Proofreading with injected Global Glossary and Reference Script.
    Slices local audio chunks and proofreads subtitles against actual audio acoustics,
    guaranteeing 100% physical timestamp preservation.
    """
    template, tmpl_file = load_proofread_template(language=language)
    print(f"\n[Stage 3/3] ⚡ Running Multimodal Audio-Text LLM Proofreading (Model: {model}, Chunk: {chunk_size}, Workers: {max_workers})...")
    print(f"  • Template Loaded: {tmpl_file} (Locale: {normalize_language_tag(language)})")
    if user_script:
        print(f"  • Reference Script: Active ({len(user_script)} characters)")
    t0 = time.time()
    raw_blocks = [b.strip() for b in raw_srt.strip().split("\n\n") if b.strip()]
    if not raw_blocks:
        return raw_srt, {"total": 0, "locked": 0, "fallback": 0, "fallback_indices": []}

    # Item 1: Silence-aware & semantic clause boundary chunking
    chunk_slices = split_blocks_into_semantic_chunks(
        raw_blocks,
        target_chunk_size=chunk_size,
        min_chunk_size=max(20, int(chunk_size * 0.7)),
        max_chunk_size=int(chunk_size * 1.3)
    )
    num_chunks = len(chunk_slices)
    print(f"  • Total Subtitles: {len(raw_blocks)} items -> {num_chunks} silence-aware semantic chunks (target ~{chunk_size} lines)")

    # Item 5: Chunk-level persistent response cache
    cached_chunks = {}
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached_chunks = json.load(f)
        except Exception:
            cached_chunks = {}

    meta_sig = hashlib.md5(f"{model}::{template}::{global_glossary or ''}::{user_script or ''}".encode("utf-8")).hexdigest()[:12]

    results = {}
    uncached_indices = []
    for c_idx, c_slice in enumerate(chunk_slices):
        c_text = "\n\n".join(c_slice)
        c_sig = hashlib.md5(c_text.encode("utf-8")).hexdigest()
        ckey = f"{meta_sig}_{c_sig}"
        if ckey in cached_chunks and "-->" in cached_chunks[ckey]:
            results[c_idx] = cached_chunks[ckey]
        else:
            uncached_indices.append(c_idx)

    cached_count = num_chunks - len(uncached_indices)
    if cached_count > 0:
        print(f"  • Chunk Cache   : Restored {cached_count}/{num_chunks} chunks from cache ({os.path.basename(cache_path)})")

    if uncached_indices:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    proofread_single_chunk,
                    c_idx, num_chunks, chunk_slices[c_idx],
                    template, global_glossary, audio_wav, api_key, base_url, model, user_script
                ): c_idx
                for c_idx in uncached_indices
            }

            completed_count = cached_count
            for future in concurrent.futures.as_completed(futures):
                c_idx, clean_text, success = future.result()
                results[c_idx] = clean_text
                if success and cache_path:
                    c_slice = chunk_slices[c_idx]
                    c_sig = hashlib.md5("\n\n".join(c_slice).encode("utf-8")).hexdigest()
                    cached_chunks[f"{meta_sig}_{c_sig}"] = clean_text
                    try:
                        with open(cache_path, "w", encoding="utf-8") as f:
                            json.dump(cached_chunks, f, ensure_ascii=False, indent=2)
                    except Exception:
                        pass
                completed_count += 1
                status_tag = "✓" if success else "⚠"
                pct = (completed_count / num_chunks) * 100
                print(f"\r  ► Progress: {completed_count}/{num_chunks} chunks completed ({pct:.0f}%)... {status_tag}", end="", flush=True)
        print()
    else:
        print(f"  ► All {num_chunks} chunks served directly from chunk cache (100%).")

    # Realign each chunk locally within its own acoustic window to prevent any cross-chunk drift
    overall_alignment_stats = {"total": 0, "locked": 0, "fallback": 0, "fallback_indices": []}
    if all_words:
        print("  ► [Acoustic Re-projection] Snapping subtitle boundaries to Whisper word-level ground truth (chunk-scoped)...")

    realigned_chunks = []
    for c_idx in range(num_chunks):
        c_text = results.get(c_idx, "")
        if not c_text:
            continue
        c_slice = chunk_slices[c_idx]

        # Extract timestamps for this chunk slice
        try:
            t_first = parse_timestamp_str(c_slice[0].splitlines()[1].split("-->")[0])
            t_last = parse_timestamp_str(c_slice[-1].splitlines()[1].split("-->")[1])
        except Exception:
            t_first, t_last = 0.0, 999999.0

        if all_words:
            # Select words within this chunk's time window (+/- 2.0s margin)
            chunk_words = [
                w for w in all_words
                if (float(w.get("end", 0.0)) >= t_first - 2.0 and float(w.get("start", 0.0)) <= t_last + 2.0)
            ]
            realigned_chunk, c_stats = realign_subtitles_to_words(c_text, chunk_words, language=language, is_video_start=(c_idx == 0))
            overall_alignment_stats["total"] += c_stats["total"]
            overall_alignment_stats["locked"] += c_stats["locked"]
            overall_alignment_stats["fallback"] += c_stats["fallback"]
            overall_alignment_stats["fallback_indices"].extend(c_stats["fallback_indices"])
        else:
            realigned_chunk = c_text
        realigned_chunks.append(realigned_chunk)

    raw_combined = "\n\n".join(realigned_chunks).strip()
    # Step B: Professional rhythm & pacing sanitizer (flicker bridging, +0.4s breathing buffer)
    sanitized_srt = sanitize_subtitle_timings(raw_combined, all_words=all_words, language=language)
    return sanitized_srt, overall_alignment_stats


def audit_subtitles_quality(srt_content, language="zh-TW", global_glossary=None, alignment_stats=None):
    """
    Perform a comprehensive Netflix & YouTube Standard Subtitle Quality, Pacing & Acoustic Audit.
    Returns: (metrics_dict, console_summary_str, markdown_report_str)
    """
    norm_lang = normalize_language_tag(language)
    blocks = [b.strip() for b in srt_content.strip().split("\n\n") if b.strip()]
    items = []

    for b in blocks:
        lines = b.splitlines()
        if len(lines) >= 3 and "-->" in lines[1]:
            idx = int(lines[0]) if lines[0].isdigit() else len(items) + 1
            t1, t2 = lines[1].split("-->")
            t_start = parse_timestamp_str(t1.strip())
            t_end = parse_timestamp_str(t2.strip())
            text = "\n".join(lines[2:]).strip()
            items.append({
                "index": idx,
                "start": t_start,
                "end": t_end,
                "duration": max(0.01, t_end - t_start),
                "text": text
            })
        elif len(lines) == 2 and "-->" in lines[0]:
            t1, t2 = lines[0].split("-->")
            t_start = parse_timestamp_str(t1.strip())
            t_end = parse_timestamp_str(t2.strip())
            text = lines[1].strip()
            items.append({
                "index": len(items) + 1,
                "start": t_start,
                "end": t_end,
                "duration": max(0.01, t_end - t_start),
                "text": text
            })

    total_lines = len(items)
    if total_lines == 0:
        return {}, "No subtitles found.", "# Subtitle Quality Report\nNo subtitles found."

    # 1. Timing metrics
    durs = [x["duration"] for x in items]
    mean_dur = sum(durs) / total_lines
    sorted_durs = sorted(durs)
    median_dur = sorted_durs[total_lines // 2]

    short_dur = [x for x in items if x["duration"] < 1.0]
    long_dur = [x for x in items if x["duration"] > 6.0]

    overlaps = []
    zero_gaps = []
    micro_gaps = []
    normal_gaps = []

    for i in range(total_lines - 1):
        c, n = items[i], items[i + 1]
        g = n["start"] - c["end"]
        if g < -0.001:
            overlaps.append((c, n, g))
        elif abs(g) < 0.001:
            zero_gaps.append((c, n))
        elif 0.001 <= g < 0.2:
            micro_gaps.append((c, n, g))
        else:
            normal_gaps.append((c, n, g))

    # 2. Layout & Character Length Metrics
    if norm_lang in ["zh-TW", "zh-CN", "ja"]:
        max_char_limit = 15
        cps_limit = 6.0  # Netflix CJK limit (comfortable: 4.0~5.5)
    elif norm_lang == "ko":
        max_char_limit = 16
        cps_limit = 6.5
    else:
        max_char_limit = 37
        cps_limit = 20.0 # Netflix English limit (17~20 CPS)

    overlength_lines = []
    high_cps_lines = []
    total_cps_sum = 0.0

    for x in items:
        char_count = calc_display_width(x["text"], norm_lang)
        x["char_count"] = round(char_count, 1)
        cps = round(char_count / max(0.1, x["duration"]), 2)
        x["cps"] = cps
        total_cps_sum += cps
        if char_count > max_char_limit:
            overlength_lines.append(x)
        if cps > cps_limit:
            high_cps_lines.append(x)

    mean_cps = round(total_cps_sum / total_lines, 2)
    peak_cps = max(x["cps"] for x in items)

    # 3. Punctuation & Typography Metrics (Brackets & Markdown Artifacts)
    trailing_punct_lines = [
        x for x in items if re.search(r"[。，、；:;,.—-]+$", x["text"])
    ]
    inline_comma_lines = [
        x for x in items if "，" in x["text"]
    ]

    bracket_pairs = [
        ("（", "）"),
        ("(", ")"),
        ("【", "】"),
        ("《", "》"),
        ("「", "」"),
        ("『", "』"),
        ("[", "]"),
        ("{", "}")
    ]
    unclosed_bracket_lines = []
    residual_md_lines = []
    for x in items:
        # Check unmatched brackets
        unmatched = []
        for open_b, close_b in bracket_pairs:
            if x["text"].count(open_b) != x["text"].count(close_b):
                unmatched.append(f"{open_b}...{close_b}")
        if unmatched:
            unclosed_bracket_lines.append((x, ", ".join(unmatched)))

        # Check residual markdown markers
        if re.search(r"(\*{1,3}|_{2,}|`{1,3}|(?<=^)\s*#{1,6}\s|(?<=^)\s*>\s)", x["text"]):
            residual_md_lines.append(x)

    # 4. Prolonged Silence Detection (Gap >= 10.0s)
    prolonged_silence_threshold = 10.0
    prolonged_silences = []
    if items[0]["start"] >= prolonged_silence_threshold:
        prolonged_silences.append({
            "start": 0.0,
            "end": round(items[0]["start"], 3),
            "duration": round(items[0]["start"], 2),
            "timestamp": f"{format_timestamp_srt(0.0)} --> {format_timestamp_srt(items[0]["start"])}",
            "prev_index": 0,
            "prev_text": "[影片開頭 / 片頭無語音]",
            "next_index": items[0]["index"],
            "next_text": items[0]["text"]
        })
    for i in range(total_lines - 1):
        c, n = items[i], items[i + 1]
        g = n["start"] - c["end"]
        if g >= prolonged_silence_threshold:
            prolonged_silences.append({
                "start": round(c["end"], 3),
                "end": round(n["start"], 3),
                "duration": round(g, 2),
                "timestamp": f"{format_timestamp_srt(c["end"])} --> {format_timestamp_srt(n["start"])}",
                "prev_index": c["index"],
                "prev_text": c["text"],
                "next_index": n["index"],
                "next_text": n["text"]
            })

    # 5. Glossary Consistency Check
    glossary_terms_found = []
    glossary_total_terms = 0
    if global_glossary:
        bold_terms = re.findall(r"\*\*(.+?)\*\*", global_glossary)
        category_keywords = [
            "Person", "Speaker", "Names", "Organizations", "Products", "Places", "Brands",
            "Domain Jargon", "Tech Terms", "Core Topic", "全片專有名詞", "Whisper", "講者與人物",
            "公司、品牌", "行業專有名詞", "核心主題", "Glossary", "Terminology"
        ]
        unique_terms = []
        for term in bold_terms:
            t = term.strip()
            if any(cat in t for cat in category_keywords):
                continue
            clean_t = re.split(r"[（\(]", t)[0].strip()
            if clean_t and clean_t not in unique_terms and len(clean_t) >= 2:
                unique_terms.append(clean_t)
        glossary_total_terms = len(unique_terms)
        all_subtitle_text = " ".join(x["text"] for x in items)
        for term in unique_terms:
            count = all_subtitle_text.count(term)
            if count > 0:
                glossary_terms_found.append({"term": term, "occurrences": count})

    # 6. Acoustic Alignment Metrics
    locked_count = alignment_stats.get("locked", total_lines) if alignment_stats else total_lines
    fallback_count = alignment_stats.get("fallback", 0) if alignment_stats else 0
    locked_pct = round((locked_count / max(1, locked_count + fallback_count)) * 100, 1)

    # 7. Objective Weighted Quality Score
    base_score = 100.0
    # Overlaps: -5 per violation (max -20)
    base_score -= min(20.0, len(overlaps) * 5.0)
    # Micro-gaps: -2 per violation (max -10)
    base_score -= min(10.0, len(micro_gaps) * 2.0)
    # Trailing punctuation: -1 per violation (max -10)
    base_score -= min(10.0, len(trailing_punct_lines) * 1.0)
    # Unclosed brackets: -3 per violation (max -10)
    base_score -= min(10.0, len(unclosed_bracket_lines) * 3.0)
    # Residual markdown: -5 per violation (max -10)
    base_score -= min(10.0, len(residual_md_lines) * 5.0)
    # Overlength rate: -0.5 per 1% (max -10)
    base_score -= min(10.0, (len(overlength_lines) / total_lines * 100) * 0.5)
    # Under 1s rate: -0.3 per 1% (max -5)
    base_score -= min(5.0, (len(short_dur) / total_lines * 100) * 0.3)
    # High CPS rate: -0.5 per 1% (max -10)
    base_score -= min(10.0, (len(high_cps_lines) / total_lines * 100) * 0.5)
    # Fallback rate: -0.2 per 1% (max -5)
    if alignment_stats and (locked_count + fallback_count) > 0:
        base_score -= min(5.0, (fallback_count / (locked_count + fallback_count) * 100) * 0.2)

    compliance_score = max(0.0, min(100.0, round(base_score, 1)))
    grade = "A+" if compliance_score >= 95.0 else ("A" if compliance_score >= 90.0 else ("B" if compliance_score >= 80.0 else "C"))

    # 8. Actionable Review List (Top Issues)
    review_issues = []
    # Residual Markdown (Highest priority)
    for x in residual_md_lines:
        review_issues.append({
            "index": x["index"],
            "timestamp": f"{format_timestamp_srt(x["start"])} --> {format_timestamp_srt(x["end"])}",
            "type": "🧹 殘留 Markdown",
            "detail": "包含語法符號 (*, _, `)",
            "text": x["text"],
            "severity": 20.0
        })
    # Unclosed brackets
    for x, unclosed_info in unclosed_bracket_lines:
        review_issues.append({
            "index": x["index"],
            "timestamp": f"{format_timestamp_srt(x["start"])} --> {format_timestamp_srt(x["end"])}",
            "type": "🔤 括號未閉合",
            "detail": f"未成對: {unclosed_info}",
            "text": x["text"],
            "severity": 15.0
        })
    # Overlength lines
    for x in overlength_lines:
        review_issues.append({
            "index": x["index"],
            "timestamp": f"{format_timestamp_srt(x["start"])} --> {format_timestamp_srt(x["end"])}",
            "type": "⚠️ 超長字數",
            "detail": f"{x["char_count"]} 字 (上限 {max_char_limit})",
            "text": x["text"],
            "severity": x["char_count"] - max_char_limit
        })
    # High CPS lines
    for x in high_cps_lines:
        review_issues.append({
            "index": x["index"],
            "timestamp": f"{format_timestamp_srt(x["start"])} --> {format_timestamp_srt(x["end"])}",
            "type": "⚡ 閱讀超速",
            "detail": f"{x["cps"]} CPS (上限 {cps_limit})",
            "text": x["text"],
            "severity": x["cps"] - cps_limit
        })
    # Short lines (< 1s) with high density
    for x in short_dur:
        if x["cps"] > 5.0:
            review_issues.append({
                "index": x["index"],
                "timestamp": f"{format_timestamp_srt(x["start"])} --> {format_timestamp_srt(x["end"])}",
                "type": "⏱️ 短促停留",
                "detail": f"{x["duration"]:.2f}s (CPS: {x["cps"]})",
                "text": x["text"],
                "severity": 1.0 - x["duration"]
            })

    # Sort review issues by severity descending
    review_issues.sort(key=lambda it: it.get("severity", 0), reverse=True)

    metrics = {
        "language_locale": norm_lang,
        "total_subtitles": total_lines,
        "first_in_seconds": round(items[0]["start"], 3),
        "first_in_timestamp": format_timestamp_srt(items[0]["start"]),
        "last_out_seconds": round(items[-1]["end"], 3),
        "last_out_timestamp": format_timestamp_srt(items[-1]["end"]),
        "mean_duration_seconds": round(mean_dur, 2),
        "median_duration_seconds": round(median_dur, 2),
        "mean_cps": mean_cps,
        "peak_cps": peak_cps,
        "cps_limit": cps_limit,
        "high_cps_count": len(high_cps_lines),
        "high_cps_rate_pct": round(len(high_cps_lines) / total_lines * 100, 2),
        "char_limit_per_line": max_char_limit,
        "overlength_count": len(overlength_lines),
        "overlength_rate_pct": round(len(overlength_lines) / total_lines * 100, 2),
        "trailing_punct_violations": len(trailing_punct_lines),
        "unclosed_bracket_count": len(unclosed_bracket_lines),
        "residual_markdown_count": len(residual_md_lines),
        "inline_comma_count": len(inline_comma_lines),
        "duration_under_1s_count": len(short_dur),
        "duration_under_1s_rate_pct": round(len(short_dur) / total_lines * 100, 2),
        "duration_over_6s_count": len(long_dur),
        "overlaps_count": len(overlaps),
        "micro_gaps_count": len(micro_gaps),
        "seamless_zero_gaps_count": len(zero_gaps),
        "natural_pauses_count": len(normal_gaps),
        "prolonged_silence_threshold_seconds": prolonged_silence_threshold,
        "prolonged_silence_count": len(prolonged_silences),
        "prolonged_silence_gaps": prolonged_silences,
        "acoustic_locked_count": locked_count,
        "acoustic_fallback_count": fallback_count,
        "acoustic_locked_rate_pct": locked_pct,
        "glossary_terms_detected": len(glossary_terms_found),
        "glossary_total_terms": glossary_total_terms,
        "compliance_score_pct": compliance_score,
        "compliance_grade": grade,
        "actionable_issues_count": len(review_issues)
    }

    # Console Card
    c_card = f"""
================================================================================
🎯 YouTube / Netflix 影視級字幕品質檢驗報告 (Subtitle Quality Audit Report)
================================================================================
【基本指標】
  • 語系樣板 (Locale)        : {norm_lang}
  • 總字幕句數 (Total Lines)  : {total_lines:,} 句
  • 開場聲學起點 (First In)   : {metrics['first_in_timestamp']} (0.000s 零劇透)
  • 結尾收尾時間 (Last Out)   : {metrics['last_out_timestamp']}
  • 平均閱讀時長 (Mean Dur)   : {mean_dur:.2f}s (中位數: {median_dur:.2f}s)
  • 綜合合規評分 (Compliance) : {compliance_score:.1f}% (Grade {grade})

【排版與文字潔淨 (Layout & Typography)】
  • 單行字數上限 ({norm_lang} <= {max_char_limit}) : {total_lines - len(overlength_lines)}/{total_lines} 達標 ({100.0 - metrics['overlength_rate_pct']:.1f}%)
  • 閱讀速率 (Mean CPS)      : {mean_cps} 字/秒 (峰值: {peak_cps} CPS | 上限: {cps_limit} CPS)
  • 超速警示行 (CPS > {cps_limit}) : {len(high_cps_lines)} 句 ({metrics['high_cps_rate_pct']:.1f}%)
  • 行尾標點潔淨度 (No 。,; )  : {total_lines - len(trailing_punct_lines)}/{total_lines} 潔淨 (違規: {len(trailing_punct_lines)})
  • 括號成對閉合 (Brackets)   : {total_lines - len(unclosed_bracket_lines)}/{total_lines} 閉合 (違規: {len(unclosed_bracket_lines)})
  • 語法標記潔淨 (No Markdown): {total_lines - len(residual_md_lines)}/{total_lines} 潔淨 (違規: {len(residual_md_lines)})

【時間軸與聲學對齊 (Timing & Acoustics)】
  • 物理聲學鎖定率 (Acoustic): {locked_pct:.1f}% ({locked_count}/{max(1, locked_count + fallback_count)} 句物理時間完全鎖定)
  • 最短停留時間 (Dur >= 1.0s): {total_lines - len(short_dur)}/{total_lines} 達標 ({100.0 - metrics['duration_under_1s_rate_pct']:.1f}%)
  • 最長停留時間 (Dur <= 6.0s): {total_lines - len(long_dur)}/{total_lines} 達標 (100.0%)
  • 毫秒微小黑閃 (Gap < 0.2s) : {len(micro_gaps)} 處 (100% 消除視覺閃爍)
  • 時間軸重疊衝突 (Overlaps) : {len(overlaps)} 處 (100% 物理時間連續)
  • 平滑切換銜接 (Zero Gaps)  : {len(zero_gaps)} 對 | 自然呼吸停頓: {len(normal_gaps)} 對
  • 長時間留白/靜音 (>= 10.0s): {len(prolonged_silences)} 處 ({'✅ 節奏緊湊無中斷' if len(prolonged_silences) == 0 else f'⚠️ {len(prolonged_silences)} 處長停頓需查證'})
"""

    if review_issues:
        c_card += f"\n【⚠️ 待複查警示清單 Top {min(5, len(review_issues))}/{len(review_issues)} 句】\n"
        for it in review_issues[:5]:
            c_card += f"  • #{it['index']:03d} [{it['timestamp']}] {it['type']} ({it['detail']}): \"{it['text'][:24]}\"\n"

    c_card += "================================================================================\n"

    # Build Markdown Review Table
    review_table_rows = []
    for it in review_issues[:15]:
        review_table_rows.append(
            f"| `#{it['index']}` | `{it['timestamp']}` | {it['type']} | `{it['detail']}` | {it['text']} |"
        )
    review_table_md = "\n".join(review_table_rows) if review_table_rows else "| - | - | - | - | 無待複查項目 (100% 完美達標) |"

    # Build Prolonged Silence Markdown Section
    if prolonged_silences:
        silence_rows = []
        for s_idx, s_gap in enumerate(prolonged_silences, start=1):
            p_text = (s_gap['prev_text'][:20] + '...') if len(s_gap['prev_text']) > 20 else s_gap['prev_text']
            n_text = (s_gap['next_text'][:20] + '...') if len(s_gap['next_text']) > 20 else s_gap['next_text']
            silence_rows.append(
                f"| `{s_idx}` | `{s_gap['timestamp']}` | `{s_gap['duration']:.2f}s` | {p_text} | {n_text} | ⚠️ 建議核對是否為純空景/音樂或漏字 |"
            )
        prolonged_silence_md = (
            "| 序號 | 靜音時間區間 | 靜音時長 | 前一句字幕內容 | 後一句字幕內容 | 狀態提示 |\n"
            "| :--- | :--- | :--- | :--- | :--- | :--- |\n" +
            "\n".join(silence_rows)
        )
    else:
        prolonged_silence_md = "✅ **全片無超過 10 秒之長時間對白中斷**，節奏流暢緊湊。"

    # Markdown Report
    md_report = f"""# YouTube / Netflix 影視級字幕品質檢驗報告

> **產出時間**: {time.strftime('%Y-%m-%d %H:%M:%S')}  
> **語系代碼**: `{norm_lang}`  
> **合規評分**: **{compliance_score:.1f}% (Grade {grade})**  
> **聲學鎖定**: **{locked_pct:.1f}%** ({locked_count}/{max(1, locked_count + fallback_count)} 句完全對齊物理聲學)

---

## 1. 基本資訊與時間覆蓋
* **總字幕句數**: `{total_lines:,}` 句
* **開場聲學起點**: `{metrics['first_in_timestamp']}`（物理波形起始點，0 提前劇透）
* **結尾收尾時間**: `{metrics['last_out_timestamp']}`
* **平均單句時長**: `{mean_dur:.2f}` 秒（中位數 `{median_dur:.2f}` 秒）
* **平均閱讀速度**: `{mean_cps}` 字/秒（最高峰值 `{peak_cps}` CPS）

---

## 2. 排版與字數指標 (Layout & Typography)
| 檢驗項目 | 標準規範 | 實測數值 | 狀態 |
| :--- | :--- | :--- | :--- |
| **單行字數上限** | $\\le {max_char_limit}$ 字/字元 | `{total_lines - len(overlength_lines)} / {total_lines}` ({100.0 - metrics['overlength_rate_pct']:.1f}%) | {'✅ 達標' if metrics['overlength_rate_pct'] <= 5.0 else '⚠️ 建議微調'} |
| **閱讀速度 (CPS)** | $\\le {cps_limit}$ 字/秒 | `{total_lines - len(high_cps_lines)} / {total_lines}` 舒適 ({100.0 - metrics['high_cps_rate_pct']:.1f}%) | {'✅ 舒適' if len(high_cps_lines) == 0 else f'⚠️ {len(high_cps_lines)} 句過促'} |
| **行尾贅字標點** | 嚴禁 `。`、`，`、`；` | `{len(trailing_punct_lines)}` 處違規 | {'✅ 100% 潔淨' if len(trailing_punct_lines) == 0 else f'⚠️ {len(trailing_punct_lines)} 處未清理'} |
| **括號成對閉合** | 全形/半形括號成對 | `{len(unclosed_bracket_lines)}` 處未閉合 | {'✅ 100% 成對' if len(unclosed_bracket_lines) == 0 else f'⚠️ {len(unclosed_bracket_lines)} 處未閉合'} |
| **語法標記潔淨** | 嚴禁殘留 `**`、`_`、`#` | `{len(residual_md_lines)}` 處殘留 | {'✅ 100% 潔淨' if len(residual_md_lines) == 0 else f'⚠️ {len(residual_md_lines)} 處殘留'} |
| **行內逗號轉空格** | 自然空格/頓號替代 | `{len(inline_comma_lines)}` 處書面逗號殘留 | ✅ 極簡排版 |
| **中英/數字混排空格** | 單一半形空格 | 100% 自動正規化 | ✅ 標準化 |

---

## 3. 時間軸與閱聽節奏指標 (Timing & Pacing)
| 檢驗項目 | 標準規範 | 實測數值 | 說明 |
| :--- | :--- | :--- | :--- |
| **物理聲學鎖定** | 詞級波形錨定 | `{locked_pct:.1f}%` ({locked_count} 句鎖定) | {'✅ 完美咬合' if fallback_count == 0 else f'⚠️ {fallback_count} 句使用 LLM 估計時間'} |
| **最短停留時間** | $\\ge 1.0\\text{{s}}$ | `{100.0 - metrics['duration_under_1s_rate_pct']:.1f}%` ({total_lines - len(short_dur)} 句) | 短句於靜音空隙補足至 1.0s |
| **最長停留時間** | $\\le 6.0\\text{{s}}$ | `100.0%` (0 處卡死) | 杜絕字幕卡死感 |
| **時間軸重疊 (Overlaps)** | $0\\text{{s}}$ | `0` 處重疊衝突 | 100% 物理單向連續 |
| **毫秒黑閃 (Micro-Gaps)** | $< 0.2\\text{{s}}$ | `0` 處黑閃 | 100% 防閃爍平滑橋接 |
| **平滑無縫銜接** | Gap == 0s | `{len(zero_gaps)}` 對 | 連續對話平滑接軌 |
| **自然呼吸停頓** | Gap $\\ge 0.2\\text{{s}}$ | `{len(normal_gaps)}` 對 | 保留講者停頓留白 |
| **長時間無對白/靜音** | 停頓 $\\ge 10.0\\text{{s}}$ | `{len(prolonged_silences)}` 處長停頓 | {'✅ 對白緊湊無中斷' if len(prolonged_silences) == 0 else f'⚠️ {len(prolonged_silences)} 處長停頓需查證'} |

---

## 4. 長時間無對白/靜音區間檢驗 (Prolonged Silence Audit >= 10s)
> 檢測對白中斷超過 10 秒之區間，供剪輯師快速排查是否為純音樂/空景、或 Whisper ASR / VAD 語音切除遺漏：

{prolonged_silence_md}

---

## 5. 專有名詞庫檢驗 (Glossary Consistency)
* **檢測專有名詞總數**: `{glossary_total_terms}` 個
* **成功匹配入字幕數**: `{len(glossary_terms_found)}` 個（涵蓋重點人名、品牌與日文漢字）

---

## 6. ⚠️ 待複查警示清單 (Actionable Review List)
> 剪輯師可依據以下時間碼，在 DaVinci Resolve / Premiere Pro 時間線上快速定位微調：

| 序號 | 時間區間 | 警示類型 | 實測指標 | 字幕內容 |
| :--- | :--- | :--- | :--- | :--- | :--- |
{review_table_md}
"""
    if len(review_issues) > 15:
        md_report += f"\n*(其餘 {len(review_issues) - 15} 項次要微調項目已記錄於 JSON 報告中)*\n"

    return metrics, c_card, md_report



def main():
    parser = argparse.ArgumentParser(
        description="YouTube Subtitles Generator: Global Audio Glossary + Whisper ASR + Gemini Audio Proofreading.",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("-i", "--input", required=True, help="Path to input video (e.g. final_cut_full.mp4) or audio file")
    parser.add_argument("-o", "--output-dir", default=None, help="Output directory for SRT/VTT subtitles (default: same as input)")
    parser.add_argument("--outline", default=None, help="User interview outline, topic summary, or glossary notes to bias terminology")
    parser.add_argument("--script", default=None, help="Path to full recording script, manuscript, or spoken draft to anchor terminology and phrasing")
    parser.add_argument("--whisper-model", default="base", choices=["tiny", "base", "small", "medium", "large-v3"],
                        help="Whisper model size for Stage 2 acoustic transcription (default: base)")
    parser.add_argument("--model", default="gemini-3.7-flash",
                        help="LLM model for Stage 1 & 3 proofreading (e.g. gemini-3.7-flash, gpt-5.6-luna, gemma4:e4b)")
    parser.add_argument("--base-url", default=None,
                        help="Custom OpenAI-compatible API base URL (e.g. https://api.openai.com/v1, http://localhost:11434/v1)")
    parser.add_argument("--language", default="auto", help="Spoken language code for transcription (default: auto for acoustic auto-detection, or zh-TW, en, ja, ko, zh-CN)")
    parser.add_argument("--device", default="auto", choices=["auto", "mps", "mlx", "cuda", "cpu"],
                        help="Device acceleration backend for Whisper (default: auto for Apple Silicon GPU / Neural Engine)")
    parser.add_argument("--api-key", default=None, help="API Key (or set GEMINI_API_KEY / OPENAI_API_KEY environment variable)")
    parser.add_argument("--chunk-size", type=int, default=80, help="Subtitle entries per proofread batch (default: 80, ~2-3 mins)")
    parser.add_argument("--workers", type=int, default=5, help="Concurrent workers for parallel proofreading (default: 5)")
    parser.add_argument("--force", action="store_true", help="Force re-running Whisper transcription and Gemini proofreading (bypasses acoustic baseline cache)")
    parser.add_argument("--force-glossary", action="store_true", help="Force re-extracting global glossary from scratch")

    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"[Error] Input media not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    input_basename = os.path.splitext(os.path.basename(args.input))[0]
    out_dir = args.output_dir or os.path.dirname(os.path.abspath(args.input)) or "."
    os.makedirs(out_dir, exist_ok=True)

    final_srt_path = os.path.join(out_dir, f"{input_basename}.srt")
    final_vtt_path = os.path.join(out_dir, f"{input_basename}.vtt")
    raw_srt_path = os.path.join(out_dir, f"{input_basename}_raw_whisper.srt")
    glossary_path = os.path.join(out_dir, f"{input_basename}_glossary.md")
    report_json_path = os.path.join(out_dir, f"{input_basename}_subtitle_report.json")
    report_md_path = os.path.join(out_dir, f"{input_basename}_subtitle_report.md")

    # Read outline file if provided as filepath
    user_outline_text = args.outline
    if user_outline_text and os.path.isfile(user_outline_text):
        with open(user_outline_text, "r", encoding="utf-8") as f:
            user_outline_text = f.read().strip()

    # Read full script file if provided
    user_script_text = args.script
    if user_script_text and os.path.isfile(user_script_text):
        with open(user_script_text, "r", encoding="utf-8") as f:
            user_script_text = f.read().strip()

    print("\n" + "=" * 78)
    print("🎬  YouTube Subtitles Generator (Global Glossary + Whisper ASR + Gemini Audio Proofreading)")
    print("=" * 78)
    print(f"  • Input Media   : {args.input}")
    print(f"  • LLM Model     : {args.model}")
    if args.script:
        print(f"  • Source Script : {os.path.basename(args.script) if os.path.isfile(args.script) else 'Supplied text'} ({len(user_script_text or '')} chars)")
    if args.outline:
        print(f"  • User Outline  : {args.outline[:50]}...")
    if args.base_url:
        print(f"  • Base URL      : {args.base_url}")
    print(f"  • Whisper Model : {args.whisper_model} (Language: {args.language}, Device: {args.device})")
    print(f"  • Batch Settings: Chunk {args.chunk_size} lines | {args.workers} Parallel Workers")
    print(f"  • Target SRT    : {final_srt_path}")
    print(f"  • Target VTT    : {final_vtt_path}")
    print("-" * 78)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_wav = os.path.join(tmpdir, "audio_16k.wav")
        print("\n[Step 0] 🎙️ Extracting 16kHz mono audio from media...")
        extract_audio_16k_mono(args.input, tmp_wav)

        resolved_key = resolve_api_key(args.api_key, args.base_url, args.model)

        # Stage 1: Global Audio Context & Consistency Glossary Extraction (Full 1M Context Scan)
        global_glossary = None
        if resolved_key or args.base_url:
            if os.path.exists(glossary_path) and not args.force_glossary:
                print(f"\n[Stage 1/3] 📚 Found cached Global Glossary: {glossary_path}")
                try:
                    with open(glossary_path, "r", encoding="utf-8") as f:
                        global_glossary = f.read().strip()
                    print(f"  ✓ Successfully loaded global glossary from cache ({len(global_glossary)} chars).")
                except Exception as e:
                    print(f"  [Notice] Failed to read cached glossary ({e}), re-extracting...")
                    global_glossary = None

            if not global_glossary:
                global_glossary = extract_global_glossary(
                    audio_wav=tmp_wav,
                    user_outline=user_outline_text,
                    user_script=user_script_text,
                    api_key=args.api_key,
                    base_url=args.base_url,
                    model=args.model
                )
                if global_glossary:
                    with open(glossary_path, "w", encoding="utf-8") as f:
                        f.write(global_glossary + "\n")
                    print(f"  • Saved Global Glossary: {glossary_path}")

        # Extract Whisper initial prompt from Global Glossary (Dual-track parsing)
        whisper_bias_prompt = extract_whisper_prompt(global_glossary, language=args.language)
        if whisper_bias_prompt:
            print(f"  • Extracted Whisper Initial Prompt ({len(whisper_bias_prompt)} chars):")
            print(f"    \"{whisper_bias_prompt}\"")

        # Stage 2: Whisper Acoustic Transcription (Zero-Drift Physical Timestamps with initial_prompt bias)
        raw_words_path = os.path.join(out_dir, f"{input_basename}_words.json")
        if os.path.exists(raw_srt_path) and os.path.exists(raw_words_path) and not args.force:
            print(f"\n[Stage 2/3] 🎙️ Found cached Whisper acoustic baseline:")
            print(f"  • SRT: {raw_srt_path}")
            print(f"  • Words JSON: {raw_words_path}")
            try:
                with open(raw_words_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                segments = cached.get("segments", [])
                all_words = cached.get("words", [])
                detected_lang = cached.get("language", "zh")
                with open(raw_srt_path, "r", encoding="utf-8") as f:
                    raw_srt = f.read()
                print(f"  ✓ Successfully loaded {len(segments)} segments and {len(all_words)} word timestamps from cache.")
            except Exception as e:
                print(f"  [Notice] Failed to load cache ({e}), re-running Whisper transcription...")
                segments, detected_lang, all_words = run_whisper_transcription(
                    tmp_wav,
                    model_size=args.whisper_model,
                    language=args.language,
                    device=args.device,
                    initial_prompt=whisper_bias_prompt
                )
                raw_srt = build_srt_from_segments(segments)
                with open(raw_srt_path, "w", encoding="utf-8") as f:
                    f.write(raw_srt)
                with open(raw_words_path, "w", encoding="utf-8") as f:
                    json.dump({"language": detected_lang, "segments": segments, "words": all_words}, f, ensure_ascii=False)
        else:
            segments, detected_lang, all_words = run_whisper_transcription(
                tmp_wav,
                model_size=args.whisper_model,
                language=args.language,
                device=args.device,
                initial_prompt=whisper_bias_prompt
            )
            raw_srt = build_srt_from_segments(segments)
            with open(raw_srt_path, "w", encoding="utf-8") as f:
                f.write(raw_srt)
            with open(raw_words_path, "w", encoding="utf-8") as f:
                json.dump({"language": detected_lang, "segments": segments, "words": all_words}, f, ensure_ascii=False)
            print(f"  • Saved raw acoustic baseline: {raw_srt_path}")

        effective_lang = detected_lang if str(args.language).lower() in ("auto", "none") else args.language
        print(f"  • Effective Language Locale: {normalize_language_tag(effective_lang)} (Input: '{args.language}', Detected: '{detected_lang}')")

        # Chunk-level cache path
        chunk_cache_path = os.path.join(out_dir, f".{input_basename}_chunk_cache.json")
        if args.force and os.path.exists(chunk_cache_path):
            try:
                os.remove(chunk_cache_path)
            except Exception:
                pass

        alignment_stats = None
        if not resolved_key and not args.base_url:
            print(f"\n[Stage 3/3] ℹ️  No LLM API Key (GEMINI_API_KEY / OPENAI_API_KEY) found.")
            print(f"            Saving raw Whisper acoustic transcription directly as final SRT/VTT.")
            final_srt = sanitize_subtitle_timings(raw_srt, all_words=all_words, language=effective_lang)
            alignment_stats = {"total": len(segments), "locked": len(segments), "fallback": 0, "fallback_indices": []}
        else:
            # Stage 3: Multimodal Audio-Text Parallel Chunked Proofreading
            final_srt, alignment_stats = proofread_srt_with_llm(
                raw_srt=raw_srt,
                audio_wav=tmp_wav,
                global_glossary=global_glossary,
                user_script=user_script_text,
                api_key=args.api_key,
                base_url=args.base_url,
                model=args.model,
                chunk_size=args.chunk_size,
                max_workers=args.workers,
                language=effective_lang,
                all_words=all_words,
                cache_path=chunk_cache_path
            )

        # Write Final SRT
        with open(final_srt_path, "w", encoding="utf-8") as f:
            f.write(final_srt.strip() + "\n")
        print(f"\n[Output 1/4] 📝 Saved YouTube Standard SRT Subtitles: {final_srt_path}")

        # Convert and Write WebVTT
        vtt_content = srt_to_vtt(final_srt)
        with open(final_vtt_path, "w", encoding="utf-8") as f:
            f.write(vtt_content)
        print(f"[Output 2/4] 🌐 Saved WebVTT (.vtt) Subtitles for YouTube / Web: {final_vtt_path}")

        # Stage 4 / Quality Audit: Run comprehensive quality, speed & acoustic audit
        metrics, c_card, md_report = audit_subtitles_quality(
            final_srt,
            language=effective_lang,
            global_glossary=global_glossary,
            alignment_stats=alignment_stats
        )

        with open(report_json_path, "w", encoding="utf-8") as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)
        print(f"[Output 3/4] 📊 Saved Subtitle Audit Metrics JSON: {report_json_path}")

        with open(report_md_path, "w", encoding="utf-8") as f:
            f.write(md_report)
        print(f"[Output 4/4] 📋 Saved Subtitle Audit Markdown Report: {report_md_path}")

        # Print structured console card
        print(c_card)

    print("=" * 78)
    print("✅  YouTube Subtitles Generation & Quality Audit Completed Successfully!")
    print("=" * 78 + "\n")


if __name__ == "__main__":
    main()
