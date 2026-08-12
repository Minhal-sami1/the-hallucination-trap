from __future__ import annotations

import json
from pathlib import Path

from demo import generate_fish_audio as fish


def test_synthesize_uses_the_locked_fish_voice_profile(
    monkeypatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_request(
        url: str,
        *,
        key: str,
        data: bytes,
        content_type: str,
        model: str | None = None,
    ) -> bytes:
        captured.update(
            url=url,
            key=key,
            payload=json.loads(data.decode("utf-8")),
            content_type=content_type,
            model=model,
        )
        return b"RIFF-test-wave"

    monkeypatch.setattr(fish, "_request", fake_request)
    output = tmp_path / "narration.wav"

    fish.synthesize(text="Clear legal narration.", key="process-only-key", output=output)

    assert captured == {
        "url": "https://api.fish.audio/v1/tts",
        "key": "process-only-key",
        "content_type": "application/json",
        "model": "s2.1-pro",
        "payload": {
            "text": "Clear legal narration.",
            "reference_id": "ddc683e8d4434089a00877a179668b51",
            "temperature": 0.7,
            "top_p": 0.7,
            "prosody": {
                "speed": 1.0,
                "volume": 0,
                "normalize_loudness": True,
            },
            "chunk_length": 300,
            "normalize": True,
            "format": "wav",
            "sample_rate": 44100,
            "latency": "normal",
            "max_new_tokens": 4096,
            "repetition_penalty": 1.15,
            "min_chunk_length": 50,
            "condition_on_previous_chunks": True,
        },
    }
    assert output.read_bytes() == b"RIFF-test-wave"


def test_transcribe_uploads_wav_audio_with_the_correct_mime_type(
    monkeypatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_request(
        url: str,
        *,
        key: str,
        data: bytes,
        content_type: str,
        model: str | None = None,
    ) -> bytes:
        captured.update(
            url=url,
            key=key,
            data=data,
            content_type=content_type,
            model=model,
        )
        return json.dumps({"text": "test", "segments": []}).encode()

    monkeypatch.setattr(fish, "_request", fake_request)
    audio = tmp_path / "narration.wav"
    audio.write_bytes(b"RIFF-test-wave")

    result = fish.transcribe(audio=audio, key="process-only-key", language="en")

    assert result["text"] == "test"
    assert captured["url"] == "https://api.fish.audio/v1/asr"
    assert captured["key"] == "process-only-key"
    assert captured["model"] is None
    assert str(captured["content_type"]).startswith("multipart/form-data; boundary=")
    body = captured["data"]
    assert isinstance(body, bytes)
    assert b'filename="narration.wav"' in body
    assert b"Content-Type: audio/wav" in body
    assert b"RIFF-test-wave" in body
