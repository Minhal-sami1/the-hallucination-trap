from __future__ import annotations

import argparse
import json
import os
import secrets
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

FISH_API_BASE = "https://api.fish.audio"
FISH_VOICE_ID = "ddc683e8d4434089a00877a179668b51"
FISH_TTS_MODEL = "s2.1-pro"
FISH_SAMPLE_RATE = 44_100


def _request(
    url: str,
    *,
    key: str,
    data: bytes,
    content_type: str,
    model: str | None = None,
) -> bytes:
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": content_type,
        "User-Agent": "hallucination-trap-demo/1.0",
    }
    if model:
        headers["model"] = model
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Fish Audio returned HTTP {error.code}: {detail[:500]}") from error


def synthesize(*, text: str, key: str, output: Path) -> None:
    payload = {
        "text": text,
        "reference_id": FISH_VOICE_ID,
        "temperature": 0.7,
        "top_p": 0.7,
        "prosody": {"speed": 1.0, "volume": 0, "normalize_loudness": True},
        "chunk_length": 300,
        "normalize": True,
        "format": "wav",
        "sample_rate": FISH_SAMPLE_RATE,
        "latency": "normal",
        "max_new_tokens": 4096,
        "repetition_penalty": 1.15,
        "min_chunk_length": 50,
        "condition_on_previous_chunks": True,
    }
    audio = _request(
        f"{FISH_API_BASE}/v1/tts",
        key=key,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        content_type="application/json",
        model=FISH_TTS_MODEL,
    )
    output.write_bytes(audio)


def _multipart(fields: dict[str, str], file_path: Path) -> tuple[bytes, str]:
    boundary = f"----HallucinationTrap{secrets.token_hex(16)}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    parts.extend(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="audio"; filename="{file_path.name}"\r\n'
                "Content-Type: audio/wav\r\n\r\n"
            ).encode(),
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def transcribe(*, audio: Path, key: str, language: str) -> dict[str, Any]:
    body, content_type = _multipart(
        {"language": language, "ignore_timestamps": "false"},
        audio,
    )
    result = _request(
        f"{FISH_API_BASE}/v1/asr",
        key=key,
        data=body,
        content_type=content_type,
    )
    payload = json.loads(result.decode("utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise RuntimeError("Fish Audio ASR returned an unexpected response.")
    return payload


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def write_srt(segments: list[dict[str, Any]], output: Path) -> None:
    blocks: list[str] = []
    for index, segment in enumerate(segments, start=1):
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        start = float(segment.get("start", 0))
        end = max(start + 0.2, float(segment.get("end", start + 0.2)))
        blocks.append(f"{index}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}")
    output.write_text("\n\n".join(blocks) + "\n", encoding="utf-8-sig")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate and transcribe Fish Audio demo narration."
    )
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--language", choices=("en", "ar"), required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    key = os.environ.get("FISH_API_KEY", "").strip()
    if not key:
        raise SystemExit("FISH_API_KEY is required and must not be stored in the repository.")

    script = args.script.read_text(encoding="utf-8").strip()
    if not script:
        raise SystemExit("The narration script is empty.")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    audio_path = args.out_dir / "narration.wav"
    synthesize(text=script, key=key, output=audio_path)
    asr = transcribe(audio=audio_path, key=key, language=args.language)

    (args.out_dir / "fish-asr.json").write_text(
        json.dumps(asr, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.out_dir / "fish-asr.txt").write_text(
        str(asr.get("text", "")).strip() + "\n",
        encoding="utf-8",
    )
    write_srt(asr["segments"], args.out_dir / "captions.asr.srt")
    (args.out_dir / "fish-audio-manifest.json").write_text(
        json.dumps(
            {
                "provider": "Fish Audio",
                "tts_model": FISH_TTS_MODEL,
                "voice_id": FISH_VOICE_ID,
                "voice_name": "صوت رجل سعودي مميز ⭐",
                "voice_creator": "Alnsra18ldahbe18",
                "language": args.language,
                "script": args.script.as_posix(),
                "duration_seconds": asr.get("duration"),
                "source_audio": {
                    "format": "wav",
                    "sample_rate_hz": FISH_SAMPLE_RATE,
                },
                "generation": {
                    "speed": 1.0,
                    "temperature": 0.7,
                    "top_p": 0.7,
                    "chunk_length": 300,
                    "latency": "normal",
                    "volume_adjustment": 0,
                    "loudness_normalization": True,
                },
                "api_key_persisted": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"audio": str(audio_path), "duration_seconds": asr.get("duration")},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
