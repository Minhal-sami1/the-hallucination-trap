"""Master a Hallucination Trap screen recording with narration and captions.

The tool keeps narration at its recorded 1.0x speed. If narration is longer
than the silent picture, FFmpeg clones the final picture frame. Loudness is
normalized in two passes before AAC encoding, and the encoded audio is then
measured again. The saved JSON report separates configured targets from the
measurements of the delivered file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

TARGET_INTEGRATED_LUFS = -16.0
TARGET_TRUE_PEAK_DBTP = -1.5
TARGET_LOUDNESS_RANGE_LU = 7.0
OUTPUT_SAMPLE_RATE = 48_000
OUTPUT_CHANNELS = 2
OUTPUT_AUDIO_BITRATE = "192k"


@dataclass(frozen=True)
class LoudnessStats:
    integrated_lufs: float
    true_peak_dbtp: float
    loudness_range_lu: float
    threshold_lufs: float
    target_offset_db: float


@dataclass(frozen=True)
class TimelinePlan:
    target_duration: float
    extend_last_frame: float
    pad_audio: float
    narration_speed: float = 1.0


def _number(value: float) -> str:
    """Return a stable decimal accepted by FFmpeg, without needless zeroes."""
    return f"{value:.6f}".rstrip("0").rstrip(".")


def parse_loudnorm_stats(stderr: str) -> LoudnessStats:
    """Extract the last JSON measurement emitted by FFmpeg loudnorm."""
    candidates = re.findall(r'\{\s*"input_i".*?\}', stderr, flags=re.DOTALL)
    if not candidates:
        raise ValueError("FFmpeg did not return a loudnorm JSON measurement")
    try:
        payload = json.loads(candidates[-1])
        stats = LoudnessStats(
            integrated_lufs=float(payload["input_i"]),
            true_peak_dbtp=float(payload["input_tp"]),
            loudness_range_lu=float(payload["input_lra"]),
            threshold_lufs=float(payload["input_thresh"]),
            target_offset_db=float(payload.get("target_offset", 0.0)),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("FFmpeg returned an invalid loudnorm measurement") from error

    if not all(math.isfinite(value) for value in asdict(stats).values()):
        raise ValueError("FFmpeg could not measure finite loudness values")
    return stats


def plan_timeline(*, video_duration: float, audio_duration: float) -> TimelinePlan:
    """Plan padding without changing either source playback speed."""
    if video_duration <= 0 or audio_duration <= 0:
        raise ValueError("Video and narration durations must be greater than zero")
    target = max(video_duration, audio_duration)
    return TimelinePlan(
        target_duration=target,
        extend_last_frame=max(0.0, audio_duration - video_duration),
        pad_audio=max(0.0, video_duration - audio_duration),
    )


def build_second_pass_filter(first_pass: LoudnessStats) -> str:
    """Build the measured-value filter for EBU R128 loudness pass two."""
    return (
        "loudnorm=I=-16:TP=-1.5:LRA=7"
        f":measured_I={_number(first_pass.integrated_lufs)}"
        f":measured_TP={_number(first_pass.true_peak_dbtp)}"
        f":measured_LRA={_number(first_pass.loudness_range_lu)}"
        f":measured_thresh={_number(first_pass.threshold_lufs)}"
        f":offset={_number(first_pass.target_offset_db)}"
        ":linear=true:print_format=json"
    )


def _validate_font_name(font_name: str) -> None:
    if not font_name.strip() or any(character in font_name for character in "',:;"):
        raise ValueError("Font name contains a character that FFmpeg force_style cannot use")


def build_mux_command(
    *,
    ffmpeg: Path,
    video: Path,
    audio: Path,
    output: Path,
    caption_filename: str,
    font_name: str,
    target_duration: float,
    extend_last_frame: float,
) -> list[str]:
    """Build the list-form FFmpeg command. No shell quoting is required."""
    _validate_font_name(font_name)
    if Path(caption_filename).name != caption_filename:
        raise ValueError("Caption filename must be relative to the FFmpeg working directory")

    video_filters: list[str] = []
    if extend_last_frame > 0:
        video_filters.append(
            f"tpad=stop_mode=clone:stop_duration={_number(extend_last_frame)}"
        )
    force_style = (
        f"FontName={font_name},FontSize=16,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,"
        "MarginV=24,Alignment=2"
    )
    video_filters.append(
        f"subtitles=filename={caption_filename}:force_style='{force_style}'"
    )
    filter_complex = (
        f"[0:v]{','.join(video_filters)}[v];"
        f"[1:a]apad=whole_dur={_number(target_duration)}[a]"
    )

    return [
        str(ffmpeg),
        "-hide_banner",
        "-y",
        "-filter_threads",
        "2",
        "-i",
        str(video),
        "-i",
        str(audio),
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-threads",
        "6",
        "-c:a",
        "aac",
        "-profile:a",
        "aac_low",
        "-b:a",
        OUTPUT_AUDIO_BITRATE,
        "-ar",
        str(OUTPUT_SAMPLE_RATE),
        "-ac",
        str(OUTPUT_CHANNELS),
        "-t",
        _number(target_duration),
        "-movflags",
        "+faststart",
        str(output),
    ]


def _existing_binary(path: Path | None) -> Path | None:
    return path.resolve() if path is not None and path.is_file() else None


def discover_media_tools(
    *,
    ffmpeg_override: Path | None = None,
    ffprobe_override: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[Path, Path]:
    """Find the pinned DemoMaker media tools, with explicit and PATH fallbacks."""
    environment = os.environ if env is None else env
    ffmpeg = _existing_binary(ffmpeg_override)
    ffprobe = _existing_binary(ffprobe_override)

    if ffmpeg is not None and ffprobe is None:
        ffprobe = _existing_binary(ffmpeg.with_name("ffprobe.exe"))
        ffprobe = ffprobe or _existing_binary(ffmpeg.with_name("ffprobe"))
    if ffprobe is not None and ffmpeg is None:
        ffmpeg = _existing_binary(ffprobe.with_name("ffmpeg.exe"))
        ffmpeg = ffmpeg or _existing_binary(ffprobe.with_name("ffmpeg"))

    local_app_data = environment.get("LOCALAPPDATA")
    if local_app_data:
        media_dir = Path(local_app_data) / "BrainLM" / "DemoMaker" / "vendor" / "media"
        ffmpeg = ffmpeg or _existing_binary(media_dir / "ffmpeg.exe")
        ffprobe = ffprobe or _existing_binary(media_dir / "ffprobe.exe")

    if ffmpeg is None:
        discovered_ffmpeg = shutil.which("ffmpeg", path=environment.get("PATH"))
        ffmpeg = Path(discovered_ffmpeg).resolve() if discovered_ffmpeg else None
    if ffprobe is None:
        discovered_ffprobe = shutil.which("ffprobe", path=environment.get("PATH"))
        ffprobe = Path(discovered_ffprobe).resolve() if discovered_ffprobe else None

    if ffmpeg is None or ffprobe is None:
        raise FileNotFoundError(
            "Could not find ffmpeg and ffprobe. Install the pinned Supercut media tools "
            "or pass --ffmpeg and --ffprobe."
        )
    return ffmpeg, ffprobe


def _run(command: Sequence[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a list-form command without a shell and return captured UTF-8 text."""
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    result = subprocess.run(
        list(command),
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        creationflags=creation_flags,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise RuntimeError(f"Media command failed with exit code {result.returncode}: {detail}")
    return result


def probe_media(ffprobe: Path, media: Path) -> dict[str, object]:
    result = _run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_entries",
            (
                "format=format_name,duration:"
                "stream=index,codec_type,codec_name,profile,pix_fmt,sample_rate,channels,"
                "bit_rate,width,height"
            ),
            "-of",
            "json",
            str(media),
        ]
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(f"ffprobe returned invalid JSON for {media}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"ffprobe returned an invalid object for {media}")
    return payload


def _streams(probe: Mapping[str, object]) -> list[dict[str, object]]:
    streams = probe.get("streams")
    if not isinstance(streams, list):
        return []
    return [stream for stream in streams if isinstance(stream, dict)]


def _stream(probe: Mapping[str, object], codec_type: str) -> dict[str, object] | None:
    return next(
        (stream for stream in _streams(probe) if stream.get("codec_type") == codec_type),
        None,
    )


def _duration(probe: Mapping[str, object]) -> float:
    media_format = probe.get("format")
    if not isinstance(media_format, dict):
        raise ValueError("ffprobe output has no format information")
    try:
        duration = float(media_format["duration"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("ffprobe output has no valid duration") from error
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Media duration must be a finite value greater than zero")
    return duration


def validate_narration_source(narration: Path, probe: Mapping[str, object]) -> None:
    if narration.suffix.lower() != ".wav":
        raise ValueError("Narration source must be a WAV file")
    audio = _stream(probe, "audio")
    if audio is None:
        raise ValueError("Narration WAV has no audio stream")
    if str(audio.get("sample_rate")) != "44100":
        raise ValueError("Narration WAV must use a 44100 Hz source sample rate")
    _duration(probe)


def validate_final_output(probe: Mapping[str, object], target_duration: float) -> None:
    video = _stream(probe, "video")
    audio = _stream(probe, "audio")
    if video is None or video.get("codec_name") != "h264":
        raise ValueError("Final output does not contain H.264 video")
    if video.get("pix_fmt") != "yuv420p":
        raise ValueError("Final output does not use yuv420p video")
    if audio is None or audio.get("codec_name") != "aac":
        raise ValueError("Final output does not contain AAC audio")
    if str(audio.get("profile", "")).upper() not in {"LC", "AAC LC"}:
        raise ValueError("Final output does not use the AAC-LC profile")
    if str(audio.get("sample_rate")) != str(OUTPUT_SAMPLE_RATE):
        raise ValueError("Final output audio does not use 48000 Hz")
    if int(audio.get("channels", 0)) != OUTPUT_CHANNELS:
        raise ValueError("Final output audio is not stereo")
    if abs(_duration(probe) - target_duration) > 0.2:
        raise ValueError("Final output duration does not match the planned timeline")


def measure_loudness(ffmpeg: Path, media: Path) -> LoudnessStats:
    result = _run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-nostats",
            "-i",
            str(media),
            "-vn",
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=7:print_format=json",
            "-f",
            "null",
            os.devnull,
        ]
    )
    return parse_loudnorm_stats(result.stderr)


def normalize_narration(ffmpeg: Path, narration: Path, output_wav: Path) -> LoudnessStats:
    """Run loudnorm analysis, then render a measured two-pass PCM master."""
    first_pass = measure_loudness(ffmpeg, narration)
    _run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-y",
            "-i",
            str(narration),
            "-vn",
            "-af",
            build_second_pass_filter(first_pass),
            "-c:a",
            "pcm_s24le",
            "-ar",
            str(OUTPUT_SAMPLE_RATE),
            "-ac",
            str(OUTPUT_CHANNELS),
            str(output_wav),
        ]
    )
    return first_pass


def _encoding_report(probe: Mapping[str, object]) -> dict[str, object]:
    return {
        "duration_seconds": _duration(probe),
        "video": _stream(probe, "video"),
        "audio": _stream(probe, "audio"),
    }


def make_report(
    *,
    video: Path,
    narration: Path,
    captions: Path,
    output: Path,
    timeline: TimelinePlan,
    source_video_duration: float,
    source_audio_duration: float,
    first_pass: LoudnessStats,
    final_measurement: LoudnessStats,
    final_probe: Mapping[str, object],
    font_name: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "inputs": {
            "silent_video": str(video.resolve()),
            "narration_wav": str(narration.resolve()),
            "captions_srt": str(captions.resolve()),
        },
        "output": str(output.resolve()),
        "timeline": {
            "source_video_seconds": source_video_duration,
            "source_narration_seconds": source_audio_duration,
            "target_seconds": timeline.target_duration,
            "extended_last_frame_seconds": timeline.extend_last_frame,
            "padded_audio_seconds": timeline.pad_audio,
            "narration_speed": timeline.narration_speed,
        },
        "captions": {"burned_in": True, "font_name": font_name},
        "mastering": {
            "method": "EBU R128 loudnorm two-pass",
            "target": {
                "integrated_lufs": TARGET_INTEGRATED_LUFS,
                "true_peak_dbtp": TARGET_TRUE_PEAK_DBTP,
                "loudness_range_lu": TARGET_LOUDNESS_RANGE_LU,
            },
            "first_pass_measurement": asdict(first_pass),
            "final_measurement": asdict(final_measurement),
            "final_delta_from_target": {
                "integrated_lu": round(
                    final_measurement.integrated_lufs - TARGET_INTEGRATED_LUFS, 3
                ),
                "true_peak_db": round(
                    final_measurement.true_peak_dbtp - TARGET_TRUE_PEAK_DBTP, 3
                ),
                "loudness_range_lu": round(
                    final_measurement.loudness_range_lu - TARGET_LOUDNESS_RANGE_LU, 3
                ),
            },
        },
        "encoding": {
            **_encoding_report(final_probe),
            "requested_audio": {
                "codec": "AAC-LC",
                "sample_rate_hz": OUTPUT_SAMPLE_RATE,
                "channels": OUTPUT_CHANNELS,
                "bitrate": OUTPUT_AUDIO_BITRATE,
            },
        },
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def master_video(
    *,
    video: Path,
    narration: Path,
    captions: Path,
    output: Path,
    font_name: str,
    report_path: Path | None = None,
    ffmpeg_override: Path | None = None,
    ffprobe_override: Path | None = None,
) -> dict[str, object]:
    """Run the full local master and return its evidence report."""
    video = video.resolve()
    narration = narration.resolve()
    captions = captions.resolve()
    output = output.resolve()
    for source in (video, narration, captions):
        if not source.is_file():
            raise FileNotFoundError(f"Required input does not exist: {source}")
    if captions.suffix.lower() != ".srt" or not captions.read_text(
        encoding="utf-8-sig"
    ).strip():
        raise ValueError("Captions must be a non-empty UTF-8 SRT file")
    _validate_font_name(font_name)

    ffmpeg, ffprobe = discover_media_tools(
        ffmpeg_override=ffmpeg_override,
        ffprobe_override=ffprobe_override,
    )
    video_probe = probe_media(ffprobe, video)
    if _stream(video_probe, "video") is None:
        raise ValueError("Silent recording has no video stream")
    narration_probe = probe_media(ffprobe, narration)
    validate_narration_source(narration, narration_probe)
    source_video_duration = _duration(video_probe)
    source_audio_duration = _duration(narration_probe)
    timeline = plan_timeline(
        video_duration=source_video_duration,
        audio_duration=source_audio_duration,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hallucination-trap-master-") as temp_name:
        temp_dir = Path(temp_name)
        local_captions = temp_dir / "captions.srt"
        normalized_audio = temp_dir / "mastered.wav"
        shutil.copyfile(captions, local_captions)
        first_pass = normalize_narration(ffmpeg, narration, normalized_audio)

        # PCM duration can differ by one sample after the 44.1 kHz to 48 kHz conversion.
        normalized_duration = _duration(probe_media(ffprobe, normalized_audio))
        timeline = plan_timeline(
            video_duration=source_video_duration,
            audio_duration=normalized_duration,
        )
        command = build_mux_command(
            ffmpeg=ffmpeg,
            video=video,
            audio=normalized_audio,
            output=output,
            caption_filename=local_captions.name,
            font_name=font_name,
            target_duration=timeline.target_duration,
            extend_last_frame=timeline.extend_last_frame,
        )
        _run(command, cwd=temp_dir)

    final_probe = probe_media(ffprobe, output)
    validate_final_output(final_probe, timeline.target_duration)
    final_measurement = measure_loudness(ffmpeg, output)
    report = make_report(
        video=video,
        narration=narration,
        captions=captions,
        output=output,
        timeline=timeline,
        source_video_duration=source_video_duration,
        source_audio_duration=source_audio_duration,
        first_pass=first_pass,
        final_measurement=final_measurement,
        final_probe=final_probe,
        font_name=font_name,
    )
    report["output_sha256"] = _sha256(output)
    destination = (
        report_path.resolve()
        if report_path is not None
        else output.with_suffix(".mastering.json")
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Master one Hallucination Trap recording at natural narration speed."
    )
    parser.add_argument("--video", type=Path, required=True, help="Silent Supercut MP4")
    parser.add_argument("--audio", type=Path, required=True, help="44.1 kHz narration WAV")
    parser.add_argument("--captions", type=Path, required=True, help="UTF-8 SRT captions")
    parser.add_argument("--output", type=Path, required=True, help="Final MP4 path")
    parser.add_argument("--font-name", default="Arial", help="libass caption font")
    parser.add_argument("--report", type=Path, help="Optional mastering JSON path")
    parser.add_argument("--ffmpeg", type=Path, help="Optional ffmpeg binary override")
    parser.add_argument("--ffprobe", type=Path, help="Optional ffprobe binary override")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = master_video(
            video=args.video,
            narration=args.audio,
            captions=args.captions,
            output=args.output,
            font_name=args.font_name,
            report_path=args.report,
            ffmpeg_override=args.ffmpeg,
            ffprobe_override=args.ffprobe,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        print(f"master_video: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
