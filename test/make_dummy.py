"""Builds the dummy test library: short videos with known content at known timestamps.

usage: python3 make_dummy.py <dir with source jpgs + tts mp3s> <output dir>
"""
import shutil
import subprocess
import sys
from pathlib import Path

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# name -> capture date, scenes (image, seconds, optional on-screen text), speech (mp3, start second)
VIDEOS = {
    # The cat only shows up 20s in, so Immich's single thumbnail frame misses it.
    "trip_cat_mid.mp4": ("2025-01-14T09:30:00", [("lake", 10), ("food", 10), ("cat", 6), ("lake", 4)], None),
    "bday_party.mp4": ("2025-03-22T19:05:00", [("cake", 12), ("dog", 8)], ("en_bday.mp3", 1.0)),
    "park_hindi.mp4": ("2025-06-08T18:10:00", [("dog", 16)], ("hi_park.mp3", 1.0)),
    "goa_text.mp4": ("2025-10-20T21:00:00", [("beach", 8, "GOA 2023"), ("fireworks", 8, "HAPPY DIWALI")],
                     ("mr_goa.mp3", 1.0)),
}


def build(src: Path, out: Path, name: str, date: str, scenes: list, speech) -> None:
    total = sum(s[1] for s in scenes)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    filters = []
    for i, scene in enumerate(scenes):
        image, seconds = scene[0], scene[1]
        cmd += ["-loop", "1", "-framerate", "30", "-t", str(seconds), "-i", str(src / f"{image}.jpg")]
        chain = f"[{i}:v]scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,setsar=1,format=yuv420p"
        if len(scene) > 2:
            chain += (f",drawtext=fontfile={FONT}:text='{scene[2]}':fontsize=104:fontcolor=white"
                      f":borderw=5:bordercolor=black:x=(w-tw)/2:y=h*0.12")
        filters.append(chain + f"[v{i}]")
    n = len(scenes)
    filters.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]")
    if speech:
        mp3, start = speech
        cmd += ["-i", str(src / mp3)]
        delay = int(start * 1000)
        filters.append(f"[{n}:a]aresample=48000,adelay={delay}|{delay},apad,atrim=0:{total}[a]")
    else:
        cmd += ["-f", "lavfi", "-t", str(total), "-i", "anullsrc=r=48000:cl=stereo"]
        filters.append(f"[{n}:a]anull[a]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]", "-t", str(total),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "128k",
            "-metadata", f"creation_time={date}Z", "-movflags", "+faststart", str(out / name)]
    subprocess.run(cmd, check=True)


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    for name, (date, scenes, speech) in VIDEOS.items():
        build(src, out, name, date, scenes, speech)
        print("built", name)
    shutil.copy(src / "cat.jpg", out / "cat_photo.jpg")  # a plain photo, to check photos are untouched
    print("copied cat_photo.jpg")


if __name__ == "__main__":
    main()
