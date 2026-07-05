#!/usr/bin/env python3
"""
Video Transcription — Qwen3-ASR-1.7B

Batch transcription for the video shorts pipeline.
Extracts audio from video, transcribes with timestamps,
outputs JSON + plain text.

Usage:
    python3 transcribe-video.py /path/to/video.mp4
    python3 transcribe-video.py /path/to/video.mp4 --output /path/to/output/
    python3 transcribe-video.py /path/to/audio.wav

Requires: pip install qwen-asr torch ffmpeg (system)
First run downloads ~3.4GB model from HuggingFace.

Session 145, June 2026 — Qwen3-ASR chosen over Whisper for batch
transcription accuracy. Whisper Large V3 Turbo stays in pipecat-voice
for real-time dictation.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# --- Audio extraction ---

def extract_audio(video_path: str, output_dir: str) -> str:
    """Extract audio from video file as 16kHz mono WAV."""
    video = Path(video_path)
    audio_path = os.path.join(output_dir, f"{video.stem}.wav")

    if os.path.exists(audio_path):
        logger.info(f"Audio already extracted: {audio_path}")
        return audio_path

    logger.info(f"Extracting audio from {video.name}...")
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vn",                    # no video
        "-acodec", "pcm_s16le",   # 16-bit PCM
        "-ar", "16000",           # 16kHz (Qwen3-ASR expects this)
        "-ac", "1",               # mono
        "-y",                     # overwrite
        audio_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"ffmpeg failed: {result.stderr[-500:]}")
        sys.exit(1)

    size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    logger.info(f"Audio extracted: {audio_path} ({size_mb:.1f} MB)")
    return audio_path


# --- Transcription ---

def transcribe(audio_path: str, use_timestamps: bool = True) -> dict:
    """Transcribe audio with Qwen3-ASR-1.7B. Returns transcript dict."""
    import torch
    from qwen_asr import Qwen3ASRModel

    logger.info("Loading Qwen3-ASR-1.7B...")
    load_start = time.time()

    model_kwargs = dict(
        dtype=torch.bfloat16,
        device_map="cuda:0",
        max_inference_batch_size=32,
        max_new_tokens=4096,  # long audio support
    )

    if use_timestamps:
        model_kwargs["forced_aligner"] = "Qwen/Qwen3-ForcedAligner-0.6B"
        model_kwargs["forced_aligner_kwargs"] = dict(
            dtype=torch.bfloat16,
            device_map="cuda:0",
        )

    model = Qwen3ASRModel.from_pretrained(
        "Qwen/Qwen3-ASR-1.7B",
        **model_kwargs,
    )
    logger.info(f"Model loaded in {time.time() - load_start:.1f}s")

    logger.info(f"Transcribing {Path(audio_path).name}...")
    t_start = time.time()

    results = model.transcribe(
        audio=audio_path,
        language="English",
        return_time_stamps=use_timestamps,
    )

    elapsed = time.time() - t_start
    result = results[0]

    # Build output structure
    transcript = {
        "source_audio": audio_path,
        "language": result.language,
        "text": result.text,
        "transcription_time_seconds": round(elapsed, 2),
        "model": "Qwen3-ASR-1.7B",
    }

    if use_timestamps and hasattr(result, "time_stamps") and result.time_stamps:
        segments = []
        for stamp_group in result.time_stamps:
            if hasattr(stamp_group, "__iter__"):
                for stamp in stamp_group:
                    segments.append({
                        "text": stamp.text,
                        "start": stamp.start_time,
                        "end": stamp.end_time,
                    })
            else:
                segments.append({
                    "text": stamp_group.text,
                    "start": stamp_group.start_time,
                    "end": stamp_group.end_time,
                })
        transcript["segments"] = segments

    # Audio duration from file
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", audio_path],
            capture_output=True, text=True,
        )
        if probe.returncode == 0:
            duration = float(probe.stdout.strip())
            transcript["audio_duration_seconds"] = round(duration, 2)
            transcript["realtime_factor"] = round(duration / elapsed, 1)
            logger.info(
                f"Done: {duration:.0f}s audio transcribed in {elapsed:.1f}s "
                f"({duration/elapsed:.0f}x realtime)"
            )
    except Exception:
        logger.info(f"Transcribed in {elapsed:.1f}s")

    return transcript


# --- Output ---

def save_outputs(transcript: dict, output_dir: str, stem: str):
    """Save transcript as JSON and plain text."""
    os.makedirs(output_dir, exist_ok=True)

    # JSON with full data
    json_path = os.path.join(output_dir, f"{stem}-transcript.json")
    with open(json_path, "w") as f:
        json.dump(transcript, f, indent=2, ensure_ascii=False)
    logger.info(f"JSON: {json_path}")

    # Plain text (for reading/editing)
    txt_path = os.path.join(output_dir, f"{stem}-transcript.txt")
    with open(txt_path, "w") as f:
        f.write(f"# Transcript: {stem}\n")
        f.write(f"# Model: {transcript['model']}\n")
        if "audio_duration_seconds" in transcript:
            mins = int(transcript["audio_duration_seconds"] // 60)
            secs = int(transcript["audio_duration_seconds"] % 60)
            f.write(f"# Duration: {mins}:{secs:02d}\n")
        f.write(f"# Language: {transcript['language']}\n")
        f.write("\n")

        if "segments" in transcript:
            for seg in transcript["segments"]:
                start = seg.get("start", "?")
                end = seg.get("end", "?")
                f.write(f"[{start} → {end}] {seg['text']}\n")
        else:
            f.write(transcript["text"])
            f.write("\n")

    logger.info(f"Text: {txt_path}")
    return json_path, txt_path


# --- Main ---

def main():
    parser = argparse.ArgumentParser(
        description="Transcribe video/audio with Qwen3-ASR-1.7B"
    )
    parser.add_argument("input", help="Path to video or audio file")
    parser.add_argument(
        "--output", "-o",
        help="Output directory (default: same as input file)",
    )
    parser.add_argument(
        "--no-timestamps", action="store_true",
        help="Skip timestamp generation (faster, text only)",
    )
    args = parser.parse_args()

    input_path = os.path.abspath(args.input)
    if not os.path.isfile(input_path):
        logger.error(f"File not found: {input_path}")
        sys.exit(1)

    stem = Path(input_path).stem
    output_dir = args.output or str(Path(input_path).parent)

    # If video, extract audio first
    video_exts = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
    audio_exts = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".opus"}
    ext = Path(input_path).suffix.lower()

    if ext in video_exts:
        audio_path = extract_audio(input_path, output_dir)
    elif ext in audio_exts:
        audio_path = input_path
    else:
        logger.warning(f"Unknown extension '{ext}', treating as audio")
        audio_path = input_path

    # Transcribe
    transcript = transcribe(audio_path, use_timestamps=not args.no_timestamps)
    transcript["source_video"] = input_path if ext in video_exts else None

    # Save
    json_path, txt_path = save_outputs(transcript, output_dir, stem)

    # Summary
    print(f"\n{'='*50}")
    print(f"Transcription complete: {stem}")
    if "audio_duration_seconds" in transcript:
        print(f"Duration: {transcript['audio_duration_seconds']:.0f}s | "
              f"Speed: {transcript.get('realtime_factor', '?')}x realtime")
    print(f"Words: ~{len(transcript['text'].split())}")
    if "segments" in transcript:
        print(f"Segments: {len(transcript['segments'])}")
    print(f"Output: {json_path}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
