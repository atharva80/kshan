"""Downloads the source material for the dummy test videos: 7 freely licensed photos from
Wikimedia Commons, plus 3 short speech clips (English, Hindi, Marathi) made with edge-tts.

usage: python3 fetch_media.py <output dir>
then:  python3 make_dummy.py <output dir> <library dir>

Speech needs `pip install edge-tts` (it uses Microsoft's online voices). The photos are
not stored in this repo; ATTRIBUTION.md is written next to them.
"""
import json
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

UA = "immich-video-search-tests/1.0 (https://github.com/atharva80/immich-video-search)"

PHOTOS = {
    "cat": "File:Felis catus-cat on snow.jpg",
    "dog": "File:DSC09611a Search and Rescue Dog, Austrian Red Cross Perchtoldsdorf, 2024-10.jpg",
    "beach": "File:Havelock Island, Mangrove tree on the beach, Andaman Islands.jpg",
    "fireworks": "File:Fireworks lugano 2011-2.jpg",
    "lake": "File:Gurudongmar Lake Sikkim, India (edit).jpg",
    "food": "File:Christmas table (Serbian cuisine).jpg",
    "cake": "File:Italy - birthday cake with candles 1.jpg",
}

SPEECH = {
    "en_bday": ("en-IN-NeerjaNeural", "Happy birthday Priya! Make a wish and blow out all the candles."),
    "hi_park": ("hi-IN-SwaraNeural", "चलो आज शाम को कुत्ते के साथ पार्क में घूमने चलते हैं।"),
    "mr_goa": ("mr-IN-AarohiNeural", "आपण पुढच्या महिन्यात गोव्याला सुट्टीसाठी जाऊया."),
}


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    query = urllib.parse.urlencode({
        "action": "query", "format": "json", "titles": "|".join(PHOTOS.values()), "prop": "imageinfo",
        "iiprop": "url|extmetadata", "iiurlwidth": "1280", "iiextmetadatafilter": "Artist|LicenseShortName",
    })
    pages = json.loads(get(f"https://commons.wikimedia.org/w/api.php?{query}"))["query"]["pages"].values()
    info = {p["title"]: p["imageinfo"][0] for p in pages}
    credits = ["# Attribution\n", "Photos from Wikimedia Commons, used unmodified except for scaling/cropping "
               "into the test videos.\n"]
    for name, title in PHOTOS.items():
        ii = info[title]
        (out / f"{name}.jpg").write_bytes(get(ii["thumburl"]))
        meta = ii.get("extmetadata", {})
        artist = meta.get("Artist", {}).get("value", "unknown")
        artist = " ".join(re.sub("<[^>]+>", "", artist).split())
        credits.append(f"- `{name}.jpg`: [{title[5:]}]({ii['descriptionurl']}) by {artist}, "
                       f"{meta.get('LicenseShortName', {}).get('value', '?')}")
        print("photo", name)
    (out / "ATTRIBUTION.md").write_text("\n".join(credits) + "\n")

    tts = shutil.which("edge-tts")
    if not tts:
        print("edge-tts not found: `pip install edge-tts`, then run this again for the speech clips")
        return
    for name, (voice, text) in SPEECH.items():
        subprocess.run([tts, "--voice", voice, "--text", text, "--write-media", str(out / f"{name}.mp3")], check=True)
        print("speech", name)


if __name__ == "__main__":
    main()
