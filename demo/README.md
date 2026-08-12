# Demo recording workflow

This folder defines two separate, local-only demo videos: English and Arabic. Both use the real application in visible Cached mode, deterministic Supercut capture, Fish Audio S2.1 Pro narration, Fish Audio ASR transcription, and FFmpeg assembly. Nothing in this workflow deploys the application.

## Truth boundaries

- Cached output is always visible as cached.
- A green verdict proves exact citation existence in the loaded law. It does not prove that every sentence is legally correct.
- The 80-of-80 fabrication result is a curated repeatability challenge set, not a population estimate for a model.
- The Fish Audio key is read only from `FISH_API_KEY`. It must never be committed.

## Pinned tools

- Supercut: `Co-Messi/supercut` commit `bd47f71e939c0e377fd2f684941bce17f3949e06`
- Fish Audio TTS: `s2.1-pro`
- English and Arabic Fish voice: `صوت رجل سعودي مميز ⭐`
- Voice creator: `Alnsra18ldahbe18`
- Voice reference: `ddc683e8d4434089a00877a179668b51`

The same voice speaks both tracks. Arabic uses clear, professional,
conversational Gulf Arabic. English uses natural conversational English. The
generation profile is fixed in the script: speed `1.0x`, temperature `0.7`,
top-p `0.7`, chunk length `300`, normal latency, volume adjustment `0`, and
loudness normalization enabled. Fish returns WAV at 44.1 kHz. The CLI does not
accept a voice override, so both tracks always use the pinned profile.

Supercut records at most 60 seconds per recipe. The scripts are written for a natural 1.0x voice. Do not accelerate narration to force a fit.

## Local recording target

Start the application on host port `8899`. Port `8000` is not required:

```powershell
docker compose up -d db
docker build -t hallucination-trap:recording .
docker run -d --name halltrap-prod-qa --add-host host.docker.internal:host-gateway -p 8899:8000 -e CACHE_MODE=cached -e DATABASE_URL=postgresql+psycopg://trap:trap@host.docker.internal:5432/hallucination_trap hallucination-trap:recording
```

Confirm the corpus before recording:

```powershell
Invoke-RestMethod http://127.0.0.1:8899/health
node scripts\check_primary_trap.mjs http://127.0.0.1:8899 10 2000
```

The ten-run check must catch the fabricated citation within two seconds and
must verify expected Article 1042 or 1047 on the grounded Arabic path.

## Generate and transcribe narration

Set `FISH_API_KEY` only in the current process. Do not put the key in a file:

```powershell
$env:FISH_API_KEY = "<Fish Audio API key>"

python demo\generate_fish_audio.py --script demo\scripts\english.txt --language en --out-dir results\demo\english
python demo\generate_fish_audio.py --script demo\scripts\arabic.txt --language ar --out-dir results\demo\arabic

Remove-Item Env:FISH_API_KEY
```

Each command writes the narration, the raw Fish ASR response, a plain-text
transcript, raw word captions, and a key-free manifest.

## Record and render with Supercut

```powershell
$supercut = Join-Path $env:LOCALAPPDATA "BrainLM\DemoMaker\vendor\supercut\dist\cli\index.js"
$media = Join-Path $env:LOCALAPPDATA "BrainLM\DemoMaker\vendor\media"
$env:PATH = "$media;$env:PATH"

node $supercut record --recipe demo\supercut\english.recipe.json --out results\demo\english\take --seed 1
node $supercut render --take results\demo\english\take --out results\demo\english\silent.mp4 --bg midnight --music off

node $supercut record --recipe demo\supercut\arabic.recipe.json --out results\demo\arabic\take --seed 1
node $supercut render --take results\demo\arabic\take --out results\demo\arabic\silent.mp4 --bg midnight --music off
```

## Build captions from Fish ASR

The narration generator saves the complete Fish ASR response at
`results/demo/<language>/fish-asr.json`. Build both subtitle files from those
saved responses. This step makes no network call and does not need an API key:

```powershell
python demo\build_captions.py
```

The command writes `results/demo/english/captions.srt` and
`results/demo/arabic/captions.srt` as UTF-8 with LF line endings. Cue boundaries
come from the Fish word timestamps. The script applies only the reviewed ASR
repairs listed in its `repair_word` function. It does not use the narration
scripts as replacement transcripts.

To rebuild one language, use `--language english` or `--language arabic`.

## Add narration and captions

Supercut produces `silent.mp4`. The following PowerShell commands burn the
Fish-derived captions, normalize narration to -16 LUFS with a -1.5 dBTP true
peak and 7 LU loudness range, and add AAC audio at 48 kHz stereo and 192 kbps.
`master_video.py` finds the FFmpeg and FFprobe binaries installed with the
local DemoMaker tools. It uses measured two-pass EBU R128 normalization. The
video and narration stay at natural 1.0x speed. If narration is longer than the
screen recording, the tool holds the final video frame. It never accelerates
the voice.

```powershell
python demo\master_video.py --video results\demo\english\silent.mp4 --audio results\demo\english\narration.wav --captions results\demo\english\captions.srt --output results\demo\english\final.mp4 --font-name "Arial"
python demo\master_video.py --video results\demo\arabic\silent.mp4 --audio results\demo\arabic\narration.wav --captions results\demo\arabic\captions.srt --output results\demo\arabic\final.mp4 --font-name "Segoe UI"
```

FFmpeg uses libass for the SRT filter. English uses Arial. Arabic uses Segoe UI
because it renders the full Arabic glyph set without fallback boxes; libass
performs Arabic shaping and bidirectional layout. The output files are
`results/demo/english/final.mp4` and
`results/demo/arabic/final.mp4`. Each command also writes a key-free
`final.mastering.json` file next to the video. This report keeps the configured
targets separate from the measured loudness of the final AAC track. A short
speech track can have a measured loudness range below the 7 LU target; the
report shows the real value.

## Verify final files

`results/demo/video-manifest.json` records each duration, size, codec, and
SHA-256 value. Reproduce the hashes with:

```powershell
Get-FileHash -Algorithm SHA256 results\demo\english\final.mp4
Get-FileHash -Algorithm SHA256 results\demo\arabic\final.mp4
```

Use the bundled `ffprobe.exe` for codec and duration checks. Decode each file
with `ffmpeg.exe -v error -i <file> -f null NUL`; no output and exit code zero
means that the complete video and audio streams decoded successfully.
