#!/usr/bin/env python
"""Generate the synthetic narrated teardown fixtures for the Video SOP/BOM Extraction smoke round.

The pipeline needs narrated video and the sandbox has none, so the fixtures are made here: Amazon
Polly narrates a scripted ten-step Cognex-style teardown (one `SynthesizeSpeech` call per step, neural
voice, 16 kHz PCM), PIL renders one captioned frame per second showing the step being narrated, and
the ffmpeg bundled with `imageio-ffmpeg` muxes them into H.264/AAC MP4 clips. The smoke suite imports
this module for `STEPS`, `KEYWORDS` and `COMPONENT_NOUNS` -- what R1 asserts the transcript and the
SOP against -- so nothing here touches AWS at import time; clients are built inside `main()`.

Fixture set, written to `--out-dir` (default `_video_sop_bom_fixtures/` beside this file):

    teardown-part1.mp4, teardown-part2.MP4   the happy pair (~75 s each; the second is upper-case .MP4)
    tiny-1.mp4 .. tiny-5.mp4                 3 s silent clips for the count-rejection arm (R2)
    teardown-long-200s.mp4                   200 s low-bitrate clip for the duration-rejection arm (R4)
    size-small.mp4, size-big.mp4             the per-file size pair (R3): well under and well over 8 MB
    silent.mp4                               30 s with an audio stream that carries no speech: the
                                             no-speech rejection arm (R9, VideoSopBomInputRejected)
    silence-30s.flac                         30 s anullsrc FLAC for the R0 Amazon Transcribe probes
    manifest.json                            bytes, duration and sha256 per file, plus the script itself

Fixtures are generated, never committed: the output directory gets a `.gitignore` of its own, and the
directory is listed in the repository's `.prettierignore` so the manifest never fails prettier-check.

Usage:
    python tools/smoketest/v260/make_teardown_videos.py --region us-east-1 [--aws-profile <profile>] [--out-dir <dir>]
    python tools/smoketest/v260/make_teardown_videos.py --without-polly   # offline mux check; silence stands in
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import textwrap
from typing import Dict, List, Sequence, Tuple, Union

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "_video_sop_bom_fixtures")

PRODUCT_NAME = "Cognex DataMan 80 barcode reader"

# Amazon Polly: neural engine, PCM 16 kHz signed 16-bit little-endian mono, at most 3000 billed
# characters per SynthesizeSpeech call.
POLLY_VOICE_ID = "Matthew"
POLLY_ENGINE = "neural"
POLLY_MAX_CHARS = 3000
PCM_SAMPLE_RATE = 16000
PCM_BYTES_PER_SECOND = PCM_SAMPLE_RATE * 2

FRAME_SIZE = (640, 360)
SMALL_FRAME_SIZE = (320, 180)
FPS = 1

TINY_CLIP_COUNT = 5
TINY_CLIP_SECONDS = 3
SILENT_CLIP_SECONDS = 30
SILENT_FLAC_SECONDS = 30
# Exceeds a 3-minute cap while the happy pair (about 150 s together) stays under it.
LONG_CLIP_SECONDS = 200
# Silence per step when --without-polly stands in for the narration.
WITHOUT_POLLY_STEP_SECONDS = 8
NOISE_SEED = 20260909


@dataclasses.dataclass(frozen=True)
class NarrationStep:
    number: int
    component: str
    text: str


STEPS: Tuple[NarrationStep, ...] = (
    NarrationStep(1, "shipping box", "Step one. Place the shipping box on the bench and cut the packing tape with a box cutter. Remove the lid of the corrugated cardboard shipping box and lift out the white foam insert."),
    NarrationStep(2, "quick start guide", "Step two. Take the reader out of its polyethylene bag. Remove the printed quick start guide and the warranty card from the box and set the paper aside."),
    NarrationStep(3, "housing", "Step three. Weigh the complete reader on the scale. It reads sixty seven point six grams. Note the aluminum die cast housing and the black polycarbonate front window."),
    NarrationStep(4, "back cover", "Step four. Pick up the Torx T eight screwdriver. Unscrew the four Torx T eight screws at each corner of the back cover. Set the screws in the parts tray."),
    NarrationStep(5, "gasket", "Step five. Lift the back cover straight up. Do not slide it sideways, the rubber gasket under the cover will tear. Peel off the rubber gasket and weigh it."),
    NarrationStep(6, "flex cable", "Step six. Disconnect the flat flex cable from the main board connector by flipping up the latch with a plastic spudger. Pull the cable straight out."),
    NarrationStep(7, "main board", "Step seven. Unscrew the two Phillips number one screws holding the main board, the printed circuit board assembly. Lift the board out and put the Phillips screwdriver away."),
    NarrationStep(8, "illumination board", "Step eight. Remove the illumination board with the red LEDs. It is held by two plastic clips at the top edge. Press the clips inward and lift the board."),
    NarrationStep(9, "imager module", "Step nine. Remove the imager module with its glass lens. It is held by a thermal pad and one Torx T six screw. Take the Torx T six screwdriver back out for this."),
    NarrationStep(10, "mid frame", "Step ten. Remove the aluminum mid frame and the steel mounting bracket. Weigh the housing, thirty seven point eight grams. Put all tools away. The teardown is complete."),
)
PART_STEPS: Dict[int, Tuple[NarrationStep, ...]] = {1: STEPS[:5], 2: STEPS[5:]}

# Phrases the transcript must contain (R1 asserts at least 80 % of them, case-insensitive).
KEYWORDS: Tuple[str, ...] = (
    "shipping box", "packing tape", "foam insert", "quick start guide", "aluminum", "polycarbonate",
    "torx", "back cover", "gasket", "flex cable", "spudger", "phillips", "circuit board",
    "illumination board", "clips", "imager", "lens", "thermal pad", "mid frame", "mounting bracket", "grams",
)
# Component nouns the SOP steps should name (R1 asserts at least 50 % of them in steps[].component).
COMPONENT_NOUNS: Tuple[str, ...] = tuple(step.component for step in STEPS)

HAPPY_PAIR = ("teardown-part1.mp4", "teardown-part2.MP4")
TINY_CLIPS = tuple(f"tiny-{i}.mp4" for i in range(1, TINY_CLIP_COUNT + 1))
LONG_CLIP = "teardown-long-200s.mp4"
SIZE_PAIR = ("size-small.mp4", "size-big.mp4")
SILENT_CLIP = "silent.mp4"
SILENT_FLAC = "silence-30s.flac"

# Role -> file name(s); the smoke suite seeds and selects fixtures by role, never by file name.
FIXTURE_ROLES = ("happy", "tiny", "long", "size_small", "size_big", "silent", "probe_flac")
FIXTURE_FILES: Dict[str, Union[str, List[str]]] = {
    "happy": list(HAPPY_PAIR),
    "tiny": list(TINY_CLIPS),
    "long": LONG_CLIP,
    "size_small": SIZE_PAIR[0],
    "size_big": SIZE_PAIR[1],
    "silent": SILENT_CLIP,
    "probe_flac": SILENT_FLAC,
}

# File name -> what the file is for (written into manifest.json).
FIXTURE_DESCRIPTIONS: Dict[str, str] = {
    HAPPY_PAIR[0]: "happy path part 1: steps 1-5 narrated, captioned frames",
    HAPPY_PAIR[1]: "happy path part 2: steps 6-10 narrated; upper-case .MP4 extension",
    **{name: f"tiny silent clip {i} for the count-rejection arm" for i, name in enumerate(TINY_CLIPS, 1)},
    LONG_CLIP: f"{LONG_CLIP_SECONDS} s low-bitrate clip for the duration-rejection arm",
    SIZE_PAIR[0]: "under-cap clip of the per-file size pair",
    SIZE_PAIR[1]: "over-cap clip of the per-file size pair (lossless seeded-noise frames)",
    SILENT_CLIP: (f"{SILENT_CLIP_SECONDS} s clip with an audio stream and no speech: exercises the "
                  "no-speech rejection (VideoSopBomInputRejected on the task token)"),
    SILENT_FLAC: f"{SILENT_FLAC_SECONDS} s anullsrc FLAC for the R0 Amazon Transcribe probes",
}

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def chunk_text(text: str, limit: int = POLLY_MAX_CHARS) -> List[str]:
    """Split narration into sentence-aligned chunks of at most `limit` characters.

    Polly bills SynthesizeSpeech at 3000 characters and refuses more, so a long script is sent in
    chunks. Splitting only at sentence ends keeps the prosody natural; a single sentence longer than
    the limit cannot be split safely and is a script error.
    """
    sentences = [s for s in _SENTENCE_END.split(" ".join(text.split())) if s]
    chunks: List[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > limit:
            raise ValueError(f"a sentence exceeds the {limit}-character limit: {sentence[:60]}...")
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > limit:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def frame_captions(captions: Sequence[str], durations: Sequence[float]) -> List[str]:
    """One caption per whole second of the clip: the caption whose segment contains that second."""
    starts: List[float] = []
    cumulative = 0.0
    for duration in durations:
        starts.append(cumulative)
        cumulative += duration
    result: List[str] = []
    for second in range(int(math.ceil(cumulative))):
        active = 0
        for index, start in enumerate(starts):
            if start <= second:
                active = index
        result.append(captions[active])
    return result


def silence_pcm(seconds: float) -> bytes:
    return b"\x00\x00" * int(round(seconds * PCM_SAMPLE_RATE))


def pcm_seconds(pcm: bytes) -> float:
    return len(pcm) / PCM_BYTES_PER_SECOND


def ffmpeg_exe() -> str:
    import imageio_ffmpeg  # noqa: PLC0415 -- the wheel ships the binary; resolved when generation starts

    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(argv: Sequence[str]) -> None:
    proc = subprocess.run(list(argv), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"{os.path.basename(argv[0])} exited {proc.returncode}: {proc.stderr[-800:]}")


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class Narrator:
    """Text to PCM through Amazon Polly, cached per step so a rerun costs nothing."""

    def __init__(self, polly, cache_dir: str) -> None:
        self.polly = polly
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def check_voice(self) -> None:
        voices = self.polly.describe_voices(Engine=POLLY_ENGINE, LanguageCode="en-US")["Voices"]
        ids = sorted(voice["Id"] for voice in voices)
        if POLLY_VOICE_ID not in ids:
            raise SystemExit(f"Polly voice {POLLY_VOICE_ID!r} is not a neural en-US voice here; available: {ids}")

    def pcm(self, step: NarrationStep) -> bytes:
        digest = hashlib.sha256(f"{POLLY_ENGINE}|{POLLY_VOICE_ID}|{step.text}".encode("utf-8")).hexdigest()[:8]
        path = os.path.join(self.cache_dir, f"step-{step.number:02d}-{digest}.pcm")
        if os.path.exists(path):
            with open(path, "rb") as fh:
                return fh.read()
        data = b""
        for chunk in chunk_text(step.text):
            response = self.polly.synthesize_speech(
                Engine=POLLY_ENGINE, VoiceId=POLLY_VOICE_ID, OutputFormat="pcm",
                SampleRate=str(PCM_SAMPLE_RATE), Text=chunk)
            data += response["AudioStream"].read()
        with open(path, "wb") as fh:
            fh.write(data)
        return data


class SilentNarrator:
    """Stands in for Polly under --without-polly: a fixed length of silence per step."""

    def check_voice(self) -> None:
        return None

    def pcm(self, step: NarrationStep) -> bytes:
        return silence_pcm(WITHOUT_POLLY_STEP_SECONDS)


def render_caption_frames(frames_dir: str, header: str, captions: Sequence[str], size=FRAME_SIZE) -> None:
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    os.makedirs(frames_dir, exist_ok=True)
    width, height = size
    body_font = ImageFont.load_default(size=max(12, height // 14))
    header_font = ImageFont.load_default(size=max(12, height // 18))
    wrap = max(20, width // 14)
    for index, caption in enumerate(captions):
        image = Image.new("RGB", size, (20, 24, 32))
        draw = ImageDraw.Draw(image)
        draw.text((width // 24, height // 24), f"{header}  t={index:04d}s", fill=(160, 200, 255), font=header_font)
        draw.multiline_text((width // 24, height // 6), "\n".join(textwrap.wrap(caption, wrap)),
                            fill=(240, 240, 240), font=body_font, spacing=4)
        image.save(os.path.join(frames_dir, f"frame_{index:04d}.png"))


def render_noise_frames(frames_dir: str, count: int, size=SMALL_FRAME_SIZE, seed: int = NOISE_SEED) -> None:
    """Seeded random RGB noise: incompressible, so the clip's size is set by the frame count."""
    from PIL import Image  # noqa: PLC0415

    os.makedirs(frames_dir, exist_ok=True)
    rng = random.Random(seed)
    width, height = size
    for index in range(count):
        Image.frombytes("RGB", size, rng.randbytes(width * height * 3)).save(
            os.path.join(frames_dir, f"frame_{index:04d}.png"))


def mux_frames_and_pcm(ff: str, frames_dir: str, pcm_path: str, out_path: str, crf: int = 28,
                       lossless: bool = False) -> None:
    video = (["-c:v", "libx264", "-preset", "veryfast", "-qp", "0", "-pix_fmt", "yuv444p"] if lossless
             else ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p"])
    _run([ff, "-hide_banner", "-loglevel", "error", "-y",
          "-framerate", str(FPS), "-i", os.path.join(frames_dir, "frame_%04d.png"),
          "-f", "s16le", "-ar", str(PCM_SAMPLE_RATE), "-ac", "1", "-i", pcm_path,
          *video, "-r", str(FPS), "-c:a", "aac", "-b:a", "64k", "-shortest",
          "-movflags", "+faststart", out_path])


def mux_frames_and_silence(ff: str, frames_dir: str, out_path: str, crf: int = 28) -> None:
    _run([ff, "-hide_banner", "-loglevel", "error", "-y",
          "-framerate", str(FPS), "-i", os.path.join(frames_dir, "frame_%04d.png"),
          "-f", "lavfi", "-i", f"anullsrc=r={PCM_SAMPLE_RATE}:cl=mono",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p", "-r", str(FPS),
          "-c:a", "aac", "-b:a", "64k", "-shortest", "-movflags", "+faststart", out_path])


def write_silent_flac(ff: str, out_path: str, seconds: int) -> None:
    _run([ff, "-hide_banner", "-loglevel", "error", "-y",
          "-f", "lavfi", "-i", f"anullsrc=r={PCM_SAMPLE_RATE}:cl=mono",
          "-t", str(seconds), "-c:a", "flac", out_path])


def build_narrated_clip(ff: str, work: str, out_path: str, header: str, steps: Sequence[NarrationStep],
                        narrator, size=FRAME_SIZE, crf: int = 28, pad_to_seconds: float = 0.0,
                        noise: bool = False) -> float:
    """Narration plus captions (or noise frames) muxed to MP4; returns the audio duration in seconds."""
    pcm_parts = [narrator.pcm(step) for step in steps]
    durations = [pcm_seconds(part) for part in pcm_parts]
    captions = [step.text for step in steps]
    pcm = b"".join(pcm_parts)
    if pad_to_seconds and pcm_seconds(pcm) < pad_to_seconds:
        padding = pad_to_seconds - pcm_seconds(pcm)
        pcm += silence_pcm(padding)
        durations.append(padding)
        captions.append("(silence)")
    frames_dir = os.path.join(work, "frames")
    shutil.rmtree(frames_dir, ignore_errors=True)
    per_second = frame_captions(captions, durations)
    if noise:
        render_noise_frames(frames_dir, len(per_second), size)
    else:
        render_caption_frames(frames_dir, header, per_second, size)
    pcm_path = os.path.join(work, "narration.pcm")
    with open(pcm_path, "wb") as fh:
        fh.write(pcm)
    mux_frames_and_pcm(ff, frames_dir, pcm_path, out_path, crf=crf, lossless=noise)
    return pcm_seconds(pcm)


def build_silent_clip(ff: str, work: str, out_path: str, header: str, seconds: int, size=FRAME_SIZE) -> float:
    frames_dir = os.path.join(work, "frames")
    shutil.rmtree(frames_dir, ignore_errors=True)
    render_caption_frames(frames_dir, header, [f"{header} (no narration)"] * seconds, size)
    mux_frames_and_silence(ff, frames_dir, out_path)
    return float(seconds)


def run(out_dir: str, narrator, ff: str) -> Dict[str, Dict[str, object]]:
    """Write every fixture and manifest.json into `out_dir`; returns the per-file manifest entries."""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, ".gitignore"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("*\n!.gitignore\n")
    work = os.path.join(out_dir, "_work")
    os.makedirs(work, exist_ok=True)
    narrator.check_voice()
    produced: Dict[str, Dict[str, object]] = {}

    def record(name: str, seconds: float) -> None:
        path = os.path.join(out_dir, name)
        produced[name] = {"bytes": os.path.getsize(path), "duration_seconds": round(seconds, 2),
                          "sha256": _sha256(path), "description": FIXTURE_DESCRIPTIONS[name]}
        print(f"  {name:26s} {produced[name]['bytes']:>12,d} bytes  {seconds:7.1f} s")

    for part, name in enumerate(HAPPY_PAIR, 1):
        record(name, build_narrated_clip(ff, work, os.path.join(out_dir, name), f"Teardown part {part}",
                                         PART_STEPS[part], narrator))
    for index, name in enumerate(TINY_CLIPS, 1):
        record(name, build_silent_clip(ff, work, os.path.join(out_dir, name), f"tiny clip {index}", TINY_CLIP_SECONDS))
    record(LONG_CLIP, build_narrated_clip(ff, work, os.path.join(out_dir, LONG_CLIP), "Teardown (long)", STEPS,
                                          narrator, size=SMALL_FRAME_SIZE, crf=35, pad_to_seconds=LONG_CLIP_SECONDS))
    record(SIZE_PAIR[0], build_narrated_clip(ff, work, os.path.join(out_dir, SIZE_PAIR[0]), "Size pair (small)",
                                             PART_STEPS[1], narrator, size=SMALL_FRAME_SIZE, crf=35))
    record(SIZE_PAIR[1], build_narrated_clip(ff, work, os.path.join(out_dir, SIZE_PAIR[1]), "Size pair (big)",
                                             PART_STEPS[1], narrator, size=SMALL_FRAME_SIZE, noise=True))
    record(SILENT_CLIP, build_silent_clip(ff, work, os.path.join(out_dir, SILENT_CLIP), "silent clip", SILENT_CLIP_SECONDS))
    write_silent_flac(ff, os.path.join(out_dir, SILENT_FLAC), SILENT_FLAC_SECONDS)
    record(SILENT_FLAC, float(SILENT_FLAC_SECONDS))
    shutil.rmtree(work, ignore_errors=True)

    manifest = {
        "product_name": PRODUCT_NAME,
        "voice": {"engine": POLLY_ENGINE, "voice_id": POLLY_VOICE_ID, "narrator": type(narrator).__name__},
        "keywords": list(KEYWORDS),
        "component_nouns": list(COMPONENT_NOUNS),
        "steps": [dataclasses.asdict(step) for step in STEPS],
        "files": produced,
    }
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=2)
    return produced


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", default=DEFAULT_OUT, help="output directory (generated, gitignored)")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION") or "us-east-1",
                        help="Amazon Polly Region (any Region with neural voices; default us-east-1)")
    parser.add_argument("--aws-profile", default=os.environ.get("AWS_PROFILE") or None,
                        help="boto3 profile name (default: the environment's credentials)")
    parser.add_argument("--without-polly", action="store_true",
                        help="offline mux check: silence stands in for narration; writes to <out-dir>/without-polly/")
    args = parser.parse_args(argv)

    ff = ffmpeg_exe()
    if args.without_polly:
        out_dir = os.path.join(args.out_dir, "without-polly")
        print(f"WITHOUT POLLY: {out_dir} carries no speech; never use it for R1, R6 or R9")
        narrator = SilentNarrator()
    else:
        import boto3  # noqa: PLC0415 -- built here, never at import
        from botocore.config import Config  # noqa: PLC0415

        session = boto3.Session(profile_name=args.aws_profile, region_name=args.region)
        polly = session.client("polly", config=Config(retries={"max_attempts": 5, "mode": "adaptive"}))
        print(f"Amazon Polly region={polly.meta.region_name} voice={POLLY_VOICE_ID} engine={POLLY_ENGINE}")
        out_dir = args.out_dir
        narrator = Narrator(polly, os.path.join(out_dir, "_polly_cache"))
    print(f"ffmpeg: {ff}")
    print(f"out:    {out_dir}")
    produced = run(out_dir, narrator, ff)
    print(f"wrote {len(produced)} fixtures + manifest.json to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
