# Operations

Day-to-day running, updates, backups, re-indexing, removal, and troubleshooting.
Paths assume the [setup guide](setup.md) layout (`/srv/immich/app`); adjust if yours differs.

## Health at a glance

```bash
cd /srv/immich/app
docker compose ps                                   # all (healthy)?
curl -s http://127.0.0.1:2284/health                # indexer: idle | indexing | paused, plus last error
docker compose logs --since 1h vsearch | grep -E "indexed|failed|restored|paused"
nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv
```

Index state, straight from the database:

```bash
docker exec immich_postgres psql -U postgres -d immich -c "
  select status, count(*), round(avg(took_seconds)::numeric, 1) as avg_s
  from vsearch.video group by status;"
docker exec immich_postgres psql -U postgres -d immich -c "
  select a.\"originalFileName\", v.attempts, v.error
  from vsearch.video v join asset a on a.id = v.asset_id where v.status = 'failed';"
```

## Updating Immich

```bash
cd /srv/immich/app
sudo btrfs subvolume snapshot -r /srv/immich /srv/immich-before-update-$(date +%F)   # if on btrfs
wget -O docker-compose.yml https://github.com/immich-app/immich/releases/latest/download/docker-compose.yml
docker compose pull && docker compose up -d
docker compose logs --since 5m vsearch | grep -iE "paused|layout"
```

If an Immich release changes a table or column vsearch uses, the indexer logs
`Immich's database layout changed, missing: ...` and `/health` shows `"indexer": "paused"`.
Nothing is written while paused. Search keeps working on what's already indexed. Update
`db.REQUIRED` and the SQL for the new layout, then rebuild.

## Updating vsearch

```bash
cd /srv/immich/app/vsearch && git pull
cd .. && docker compose up -d --build vsearch
```

If the change bumps `PIPELINE_VERSION` in `vsearch/config.py`, every video is re-indexed in the
background. Search keeps serving the old results until each video is redone.

## Re-indexing

| Want to redo | Do |
|---|---|
| everything | bump `PIPELINE_VERSION` and rebuild, or `update vsearch.video set pipeline = 0;` |
| one video | `delete from vsearch.video where asset_id = '<uuid>';` |
| failed videos now (instead of after backoff) | `delete from vsearch.video where status = 'failed';` |
| visuals only (e.g. after changing the CLIP model) | automatic: changing the model in Immich triggers it |

## Backups

**The index is the least important thing here; the photos are what matter.** Until a second
copy of the library exists, nobody should delete originals from phones or pendrives.

- **Snapshot, same disk:** `sudo btrfs subvolume snapshot -r /srv/immich /srv/immich-snap-$(date +%F)`.
  Instant and cheap, and good before risky changes. It is not a backup, because it sits on the same SSD.
- **Real backup to an external disk:** btrfs incremental send/receive, e.g. with
  [btrbk](https://digint.ch/btrbk/), or `restic`/`borg` for an encrypted copy elsewhere.
- **The database:** Immich writes nightly dumps to `UPLOAD_LOCATION/backups`. The `vsearch`
  schema is included in them, so restoring Immich's database restores the index too.
- **Not worth backing up:** `/srv/immich/vsearch` (thumbnails, regenerated on re-index) and the
  `immich-vsearch-models` volume (re-downloadable).

Watch real disk usage with `sudo btrfs filesystem usage /`, not `df`. Snapshots keep deleted
files alive.

## Resource use

| | |
|---|---|
| vsearch image | ~7 GB (CUDA libraries + PyTorch CPU) |
| models volume | ~5 GB (Whisper large-v3 3 GB, bge-m3 2.2 GB) |
| thumbnails | ~10 KB per sampled frame (≤ 300 per video) |
| RAM | ~2–3 GB (bge-m3 stays loaded for query-time search) |
| VRAM | Whisper ~1.8 GB while transcribing; freed after 5 idle minutes |
| speed | ~3–8 s per short clip; long videos scale with length (keyframes only above 10 min) |

## Removing vsearch cleanly

Its additions to Immich are merged in, never replaced, so they can be taken out precisely:

```sql
-- in: docker exec -it immich_postgres psql -U postgres -d immich
-- 1. take our transcript blocks out of descriptions
update asset_exif e set description = btrim(replace(e.description, v.desc_block, ''), E' \n')
from vsearch.video v where v.asset_id = e."assetId" and v.desc_block is not null;
-- 2. take our OCR text out of Immich's OCR search
update ocr_search o set text = btrim(replace(o.text, v.ocr_text, ''))
from vsearch.video v where v.asset_id = o."assetId" and v.ocr_text is not null;
-- 3. drop the index
drop schema vsearch cascade;
```

Then remove the `vsearch` service from the override, run `docker compose up -d --remove-orphans`,
and in Immich run **Jobs → Smart Search → All** to put back Immich's own one-frame video vectors.
Finally delete `/srv/immich/vsearch` and `docker volume rm immich-vsearch-models`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/health` says `paused` | Immich schema changed after an upgrade | see [Updating Immich](#updating-immich) |
| Videos stay `pending` | uploaded < 60 s ago, or Immich's metadata job hasn't run yet | wait; check Immich's Jobs page |
| `failed` with `ffmpeg frame sampling failed` | corrupt or unusual file | try `ffprobe` on it; it's retried twice, then skipped |
| `whisper on cuda failed … trying next device` | CUDA libraries or VRAM | check `nvidia-smi`; it falls back to CPU (slow but works) |
| `transcription failed: … metadata_errors` | old build using faster-whisper's PyAV decoding | update; audio is now decoded with ffmpeg |
| Model download stuck at the same size | Hugging Face Xet transfer stalled | keep `HF_HUB_DISABLE_XET=1`; restart the container |
| GPU not visible in containers | NVIDIA Container Toolkit missing or Docker not restarted | `sudo bash deploy/02-gpu-setup.sh` |
| Page keeps showing the login form | no Immich session for this host | log in on the page, which sets the same cookie Immich uses |
| Page loads but every search is 401 | session expired | log in again |
| `/vsearch` shows Immich instead of the page | Tailscale serve path missing | `tailscale serve status` should list `/vsearch → 127.0.0.1:2284` |
| False "Said" hits | thresholds too loose for your library | `test/calibrate.py`, then adjust `search.py` ([models.md](models.md#calibration)) |
| A video's transcript is wrong language / gibberish | music or very noisy audio | expected sometimes; VAD and the hallucination filter catch most of it |
