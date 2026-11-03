"""ffmpeg helpers: probing, scene-based frame sampling, and small frame thumbnails."""
import json
import re
import subprocess
from pathlib import Path

from PIL import Image

PTS_TIME = re.compile(r"pts_time:\s*(-?[0-9.]+)")
FRAME_EDGE = 960  # long edge of sampled frames; plenty for CLIP (384px) and OCR (736px)
THUMB_EDGE = 360


def probe(path: str) -> tuple[float, bool, bool]:
    """(duration seconds, has video, has audio)"""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, text=True, check=True, timeout=120,
    )
    info = json.loads(out.stdout)
    streams = info.get("streams", [])
    duration = float(info.get("format", {}).get("duration") or 0)
    if not duration:
        duration = max((float(s.get("duration") or 0) for s in streams), default=0.0)
    has_video = any(s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1
                    for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return duration, has_video, has_audio


def sample_frames(path: str, out_dir: Path, duration: float, max_frames: int) -> list[tuple[int, Path]]:
    """One frame per scene change, at least every few seconds, capped at max_frames.
    Returns (milliseconds, jpeg path) pairs."""
    max_gap = max(3.0, duration / max(max_frames, 1))
    min_gap = max(1.0, max_gap / 3)
    # Long videos: decode keyframes only (scene detection would mean decoding everything).
    keyframes_only = duration > 600
    select = f"isnan(prev_selected_t)+gte(t-prev_selected_t,{max_gap:.3f})"
    if not keyframes_only:
        select += f"+gt(scene,0.3)*gte(t-prev_selected_t,{min_gap:.3f})"
    scale = (f"scale='if(gt(iw,ih),min({FRAME_EDGE},iw),-2)':"
             f"'if(gt(iw,ih),-2,min({FRAME_EDGE},ih))'")
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "info"]
    if keyframes_only:
        cmd += ["-skip_frame", "nokey"]
    cmd += ["-i", path, "-an", "-sn", "-dn",
            "-vf", f"select='{select}',{scale},showinfo",
            "-fps_mode", "vfr", "-q:v", "3", str(out_dir / "%05d.jpg")]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=max(900, duration * 4))
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-5:])
        raise RuntimeError(f"ffmpeg frame sampling failed: {tail}")
    times = [float(m.group(1)) for line in proc.stderr.splitlines()
             if "Parsed_showinfo" in line and (m := PTS_TIME.search(line))]
    files = sorted(out_dir.glob("*.jpg"))
    if len(times) != len(files):
        raise RuntimeError(f"frame/timestamp mismatch: {len(files)} frames, {len(times)} timestamps")
    frames, seen = [], set()
    for t, f in zip(times, files):
        ms = max(0, round(t * 1000))
        if ms not in seen:
            seen.add(ms)
            frames.append((ms, f))
    return frames


def thumbnail(src: Path, dest: Path) -> None:
    with Image.open(src) as im:
        im.thumbnail((THUMB_EDGE, THUMB_EDGE))
        im.save(dest, "WEBP", quality=72)
