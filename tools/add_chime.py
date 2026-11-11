r"""Put a chime in front of your own alert voice clips.

For each sound file in a folder (MP3, WAV or M4A), this makes a new file with
the chime first and the voice starting 1.84 seconds in. The new file keeps the
same name, so settings.toml doesn't change. The chime is set 3 LU quieter than
that voice (EBU R128 loudness). The original goes into "originals (no chime)"
inside the same folder, and files already done are skipped.

Why: a speaker or TV that wakes up slowly cuts off the start of a sound. With a
chime in front, it cuts into the chime instead of the first word. It also sets
camera alerts apart from other spoken alerts.

Needs numpy and ffmpeg:
    py -m pip install numpy
    winget install Gyan.FFmpeg        (then open a new Terminal)

Use it (the chime file in this repo is the default):
    py tools\add_chime.py "C:\ReolinkAlerts\sound files"
    py tools\add_chime.py "C:\ReolinkAlerts\sound files" --chime "my chime.mp3" --voice-at 1.5
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

SR = 44100
DEFAULT_CHIME = Path(__file__).resolve().parent.parent / "sound files" / "Westminster chime.mp3"
TYPES = {".mp3", ".wav", ".m4a"}
BACKUP = "originals (no chime)"


def decode(path: Path, work: Path) -> np.ndarray:
    raw = work / "decoded.f32"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-ac", "1", "-ar", str(SR),
                    "-f", "f32le", str(raw)], check=True)
    return np.fromfile(raw, dtype="<f4").astype(np.float64)


def write_wav(path: Path, x: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def loudness(x: np.ndarray, work: Path) -> float | None:
    """Integrated loudness in LUFS, measured by ffmpeg's ebur128 filter."""
    tmp = work / "measure.wav"
    write_wav(tmp, x)
    result = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(tmp), "-af", "ebur128",
                             "-f", "null", "-"], capture_output=True, text=True)
    values = [line.split("I:")[1].split("LUFS")[0] for line in result.stderr.splitlines()
              if line.strip().startswith("I:")]
    try:
        value = float(values[-1])
    except (IndexError, ValueError):
        return None
    return value if value > -70 else None


def encode(wav: Path, out: Path) -> None:
    args = ["ffmpeg", "-v", "error", "-y", "-i", str(wav)]
    if out.suffix.lower() == ".mp3":
        args += ["-codec:a", "libmp3lame", "-q:a", "2"]
    elif out.suffix.lower() == ".m4a":
        args += ["-codec:a", "aac", "-b:a", "128k"]
    subprocess.run(args + [str(out)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Put a chime in front of each alert voice clip in a folder.")
    parser.add_argument("folder", type=Path, help='the folder with your voice clips, e.g. "sound files"')
    parser.add_argument("--chime", type=Path, default=DEFAULT_CHIME,
                        help="the chime file (default: sound files/Westminster chime.mp3)")
    parser.add_argument("--voice-at", type=float, default=1.84,
                        help="seconds into the new file where the voice starts (default 1.84)")
    parser.add_argument("--chime-level", type=float, default=-3.0,
                        help="chime loudness compared with the voice, in LU (default -3)")
    args = parser.parse_args()

    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg wasn't found. Install it (winget install Gyan.FFmpeg), open a new Terminal and try again.")
    if not args.folder.is_dir():
        sys.exit(f"Folder not found: {args.folder}")
    if not args.chime.is_file():
        sys.exit(f"Chime file not found: {args.chime}")

    chime_path = args.chime.resolve()
    files = sorted(p for p in args.folder.iterdir()
                   if p.is_file() and p.suffix.lower() in TYPES and p.resolve() != chime_path
                   and ".new." not in p.name)
    if not files:
        sys.exit(f"No MP3, WAV or M4A voice clips in {args.folder}")
    backup = args.folder / BACKUP

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        chime = decode(chime_path, work)
        chime_lufs = loudness(chime, work)
        start = int(args.voice_at * SR)
        for src in files:
            if (backup / src.name).exists():
                print(f"skip (already done): {src.name}")
                continue
            voice = decode(src, work)
            voice_lufs = loudness(voice, work)
            gain = 1.0
            if chime_lufs is not None and voice_lufs is not None:
                gain = 10 ** ((voice_lufs + args.chime_level - chime_lufs) / 20)
            total = start + len(voice) + int(0.1 * SR)
            mix = np.zeros(max(total, len(chime)))
            mix[:len(chime)] += chime * gain
            mix[start:start + len(voice)] += voice
            mix = mix[:total]
            peak = float(np.max(np.abs(mix)))
            if peak > 0.98:
                mix *= 0.98 / peak
            wav = work / "mix.wav"
            write_wav(wav, mix)
            new = src.with_name(f"{src.stem}.new{src.suffix}")
            encode(wav, new)
            backup.mkdir(exist_ok=True)
            os.replace(src, backup / src.name)      # the original goes to the backup folder
            os.replace(new, src)                    # the new file takes the original name
            print(f"{src.name}: chime added ({total / SR:.1f} s; original in '{BACKUP}')")


if __name__ == "__main__":
    main()
