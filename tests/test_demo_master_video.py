from __future__ import annotations

import json
from pathlib import Path

import pytest

from demo import master_video


def test_parse_loudnorm_stats_uses_measured_input_values() -> None:
    stderr = """
    [Parsed_loudnorm_0 @ 000001] {
        "input_i" : "-22.41",
        "input_tp" : "-4.18",
        "input_lra" : "3.20",
        "input_thresh" : "-32.70",
        "output_i" : "-15.98",
        "output_tp" : "-1.50",
        "output_lra" : "3.10",
        "output_thresh" : "-26.20",
        "normalization_type" : "dynamic",
        "target_offset" : "-0.02"
    }
    """

    stats = master_video.parse_loudnorm_stats(stderr)

    assert stats.integrated_lufs == -22.41
    assert stats.true_peak_dbtp == -4.18
    assert stats.loudness_range_lu == 3.20
    assert stats.threshold_lufs == -32.70
    assert stats.target_offset_db == -0.02


def test_timeline_extends_picture_without_changing_narration_speed() -> None:
    plan = master_video.plan_timeline(video_duration=55.184, audio_duration=58.25)

    assert plan.target_duration == pytest.approx(58.25)
    assert plan.extend_last_frame == pytest.approx(3.066)
    assert plan.pad_audio == 0
    assert plan.narration_speed == 1.0


def test_mux_command_burns_arabic_captions_and_uses_required_codecs(tmp_path: Path) -> None:
    command = master_video.build_mux_command(
        ffmpeg=tmp_path / "ffmpeg.exe",
        video=tmp_path / "silent.mp4",
        audio=tmp_path / "mastered.wav",
        output=tmp_path / "final.mp4",
        caption_filename="captions.srt",
        font_name="Segoe UI",
        target_duration=58.25,
        extend_last_frame=3.066,
    )
    rendered = " ".join(command)

    assert "tpad=stop_mode=clone:stop_duration=3.066" in rendered
    assert "subtitles=filename=captions.srt" in rendered
    assert "FontName=Segoe UI" in rendered
    assert "-c:v libx264" in rendered
    assert "-pix_fmt yuv420p" in rendered
    assert "-c:a aac" in rendered
    assert "-profile:a aac_low" in rendered
    assert "-b:a 192k" in rendered
    assert "-ar 48000" in rendered
    assert "-ac 2" in rendered
    assert "atempo" not in rendered
    assert "setpts" not in rendered


def test_second_pass_filter_uses_all_first_pass_measurements() -> None:
    first_pass = master_video.LoudnessStats(
        integrated_lufs=-22.41,
        true_peak_dbtp=-4.18,
        loudness_range_lu=3.2,
        threshold_lufs=-32.7,
        target_offset_db=-0.02,
    )

    audio_filter = master_video.build_second_pass_filter(first_pass)

    assert audio_filter == (
        "loudnorm=I=-16:TP=-1.5:LRA=7"
        ":measured_I=-22.41:measured_TP=-4.18:measured_LRA=3.2"
        ":measured_thresh=-32.7:offset=-0.02:linear=true:print_format=json"
    )


def test_discover_media_tools_prefers_bundled_windows_binaries(tmp_path: Path) -> None:
    media = tmp_path / "BrainLM" / "DemoMaker" / "vendor" / "media"
    media.mkdir(parents=True)
    (media / "ffmpeg.exe").write_bytes(b"")
    (media / "ffprobe.exe").write_bytes(b"")

    ffmpeg, ffprobe = master_video.discover_media_tools(env={"LOCALAPPDATA": str(tmp_path)})

    assert ffmpeg == media / "ffmpeg.exe"
    assert ffprobe == media / "ffprobe.exe"


def test_validate_narration_requires_wav_at_44100_hz(tmp_path: Path) -> None:
    wav = tmp_path / "narration.wav"
    wav.write_bytes(b"RIFF")

    master_video.validate_narration_source(
        wav,
        {
            "format": {"format_name": "wav", "duration": "12.4"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": "44100",
                }
            ],
        },
    )

    with pytest.raises(ValueError, match="44100 Hz"):
        master_video.validate_narration_source(
            wav,
            {
                "format": {"format_name": "wav", "duration": "12.4"},
                "streams": [
                    {
                        "codec_type": "audio",
                        "codec_name": "pcm_s16le",
                        "sample_rate": "48000",
                    }
                ],
            },
        )


def test_report_keeps_target_and_measured_truth_separate(tmp_path: Path) -> None:
    report = master_video.make_report(
        video=tmp_path / "silent.mp4",
        narration=tmp_path / "narration.wav",
        captions=tmp_path / "captions.srt",
        output=tmp_path / "final.mp4",
        timeline=master_video.TimelinePlan(
            target_duration=58.25,
            extend_last_frame=3.066,
            pad_audio=0,
        ),
        source_video_duration=55.184,
        source_audio_duration=58.25,
        first_pass=master_video.LoudnessStats(-22.41, -4.18, 3.2, -32.7, -0.02),
        final_measurement=master_video.LoudnessStats(-16.08, -1.62, 3.1, -26.4, 0.08),
        final_probe={
            "format": {"duration": "58.250"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"},
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "profile": "LC",
                    "sample_rate": "48000",
                    "channels": 2,
                },
            ],
        },
        font_name="Segoe UI",
    )

    encoded = json.loads(json.dumps(report))
    assert encoded["mastering"]["target"] == {
        "integrated_lufs": -16.0,
        "true_peak_dbtp": -1.5,
        "loudness_range_lu": 7.0,
    }
    assert encoded["mastering"]["final_measurement"]["integrated_lufs"] == -16.08
    assert encoded["mastering"]["final_measurement"]["true_peak_dbtp"] == -1.62
    assert encoded["timeline"]["narration_speed"] == 1.0
    assert encoded["encoding"]["audio"]["codec_name"] == "aac"
    assert encoded["encoding"]["audio"]["sample_rate"] == "48000"
    assert encoded["encoding"]["requested_audio"] == {
        "codec": "AAC-LC",
        "sample_rate_hz": 48000,
        "channels": 2,
        "bitrate": "192k",
    }
    assert encoded["captions"]["font_name"] == "Segoe UI"
