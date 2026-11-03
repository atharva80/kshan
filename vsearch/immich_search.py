"""Merges video moments into Immich's own smart search, so the search bar in the Immich
apps finds videos by any frame, anything said and any on-screen text.

Tailscale serve routes POST /api/search/smart here; the request goes to Immich unchanged,
and our matches are blended into its response (same JSON shape, so the apps can't tell).
Anything unexpected falls back to Immich's response as-is."""
import logging

log = logging.getLogger("vsearch.immich_search")

# Requests with only these fields get merged. Anything narrower (people, places, dates,
# albums, "similar to this photo", shared links...) is passed through untouched, because
# our results don't honour those filters.
MERGEABLE_FIELDS = {"query", "page", "size", "withExif", "withDeleted", "language", "visibility", "type",
                    "withStacked", "isNotInAlbum"}
EXACT = 0.95  # exact words said or written (and the very best visual match) score >= this


def mergeable(body: dict) -> bool:
    if not isinstance(body, dict) or not str(body.get("query") or "").strip():
        return False
    if set(body) - MERGEABLE_FIELDS:
        return False
    if body.get("type") not in (None, "VIDEO"):
        return False
    if body.get("withDeleted") or body.get("isNotInAlbum"):
        return False
    return body.get("visibility") in (None, "timeline")


def blend(immich_items: list[dict], ours: list[dict]) -> list[str]:
    """Order of asset ids after merging. Videos where the exact words were said or written
    go first; other video matches are woven in near the top (but never pushed below where
    Immich already ranked them); everything else keeps Immich's order."""
    key: dict[str, float] = {}
    for i, item in enumerate(immich_items):
        key[item["id"]] = float(i)
    exact = [r for r in ours
             if any(m["kind"] in ("speech", "text") and m["score"] >= EXACT for m in r["moments"])]
    rest = [r for r in ours if r not in exact]
    for k, r in enumerate(exact):
        key[r["assetId"]] = -1000.0 + k
    for k, r in enumerate(rest):
        key[r["assetId"]] = min(key.get(r["assetId"], float("inf")), 2.0 * k + 0.5)
    return sorted(key, key=key.__getitem__)
