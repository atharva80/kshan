# Setup guide

From an empty Linux machine to Immich + video search, reachable privately from anywhere.
This is the exact path the project was built on (Ubuntu 24.04, btrfs root, RTX 2000 Ada, Immich
3.3.5). Each step says what it's for, so you can skip the ones that don't apply.

1. [Prerequisites](#1-prerequisites)
2. [Storage layout (btrfs)](#2-storage-layout-btrfs)
3. [Immich](#3-immich)
4. [GPU for containers](#4-gpu-for-containers)
5. [Video search + GPU in the stack](#5-video-search--gpu-in-the-stack)
6. [Immich settings](#6-immich-settings)
7. [Private remote access with Tailscale](#7-private-remote-access-with-tailscale)
8. [Accounts](#8-accounts)
9. [Check everything](#9-check-everything)

---

## 1. Prerequisites

- **Docker Engine + Compose ≥ 2.24.** The override file uses the `!override` tag.
- **NVIDIA driver** installed on the host (`nvidia-smi` works). An 8 GB card is enough.
  Without a GPU everything still works on CPU, just slowly; drop the `deploy:` GPU blocks from
  the override.
- **Disk:** your library + ~15–20% for Immich thumbnails/transcodes, ~14 GB for container
  images, ~5 GB for vsearch models, ~4 GB for Immich's CLIP model.
- The user who runs `docker` is in the `docker` group.

## 2. Storage layout (btrfs)

*Skip if you're not on btrfs. Point Immich's `UPLOAD_LOCATION`/`DB_DATA_LOCATION` anywhere.*

```bash
sudo bash deploy/01-btrfs-setup.sh
```

It creates:

```
/srv/immich            its own btrfs subvolume: snapshot / send it as one unit,
                       and it stays out of system snapshots
  app/                 docker-compose.yml, .env, override, vsearch source
  library/             photos and videos (copy-on-write + checksums, so `btrfs scrub` catches bit rot)
  postgres/            database, with chattr +C (NOCOW), because copy-on-write fragments databases badly
```

`chattr +C` only applies to files created afterwards, so the script sets it while `postgres/`
is still empty.

## 3. Immich

The official way ([docs](https://docs.immich.app/install/docker-compose)):

```bash
cd /srv/immich/app
wget -O docker-compose.yml https://github.com/immich-app/immich/releases/latest/download/docker-compose.yml
wget -O .env https://github.com/immich-app/immich/releases/latest/download/example.env
```

Edit `.env`:

```ini
UPLOAD_LOCATION=/srv/immich/library
DB_DATA_LOCATION=/srv/immich/postgres
TZ=Asia/Kolkata
DB_PASSWORD=<32 random letters/digits, e.g. from: tr -dc A-Za-z0-9 </dev/urandom | head -c 32>
```

```bash
chmod 600 .env
docker compose up -d
```

Leave the official `docker-compose.yml` untouched. All changes go in an override file (step 5),
so an Immich update is just re-downloading that file.

## 4. GPU for containers

```bash
sudo bash deploy/02-gpu-setup.sh
```

This installs the NVIDIA Container Toolkit from NVIDIA's apt repo, registers the `nvidia`
runtime in `/etc/docker/daemon.json` (backing up the original), and restarts Docker. Check it with:

```bash
docker run --rm --gpus all --entrypoint nvidia-smi ghcr.io/immich-app/immich-machine-learning:v3-cuda -L
```

## 5. Video search + GPU in the stack

```bash
cd /srv/immich/app
git clone https://github.com/atharva80/immich-video-search vsearch
cp vsearch/deploy/docker-compose.override.yml .
sudo mkdir -p /srv/immich/vsearch
docker volume create immich-vsearch-models
docker compose up -d --build
```

The override does four things:
- binds Immich to `127.0.0.1:2283` only (Tailscale publishes it, step 7)
- gives `immich-server` the GPU for NVENC transcoding
- switches `immich-machine-learning` to the `-cuda` image
- adds the `vsearch` service

The first `--build` downloads about 7 GB (CUDA libraries, PyTorch CPU). On first start, vsearch
downloads bge-m3 (~2.3 GB) and, on the first video, Whisper large-v3 (~3 GB) into the
`immich-vsearch-models` volume. Hugging Face's Xet transfers stalled during development, so the
override sets `HF_HUB_DISABLE_XET=1` for plain, resumable HTTP downloads.

Check it:

```bash
docker compose ps                       # 5 containers, all (healthy)
curl -s http://127.0.0.1:2284/health    # {"ok":true,"indexer":"idle",...}
docker compose logs -f vsearch          # "indexed <file>: 30s video, 12 frames, ..."
```

## 6. Immich settings

Do these **before** uploading a big library, so nothing has to be processed twice. All are
under **Administration → Settings**.

| Setting | Value | Why |
|---|---|---|
| Machine Learning → Smart Search → CLIP model | `ViT-SO400M-16-SigLIP2-384__webli` | best English recall, mixed-language queries ([models.md](models.md)) |
| Video Transcoding → Hardware acceleration | NVENC (also enable hardware decoding) | transcodes on the GPU |
| Storage Template | on, e.g. `{{y}}/{{MM}}/{{dd}}/{{filename}}` | originals on disk sorted by date |

After changing the CLIP model on a library that already has photos, run **Jobs → Smart Search →
All**. vsearch notices the new model by itself and redoes the video frames.

## 7. Private remote access with Tailscale

Goal: someone in another city opens Immich in the app, without the server being on the public
internet and without them using *your* Tailscale account.

**a. Publish Immich and the page to your tailnet** (on the server):

```bash
sudo bash vsearch/deploy/03-tailscale-serve.sh
# https://<machine>.<tailnet>.ts.net:8443/                   -> Immich
# https://<machine>.<tailnet>.ts.net:8443/vsearch            -> the companion page
# https://<machine>.<tailnet>.ts.net:8443/api/search/smart   -> vsearch, which asks Immich and
#                                                               blends in video moments
```

The third route is what makes **Immich's own search bar** (apps and web) search inside videos.
Check it's in place: a search request is answered by `server: uvicorn` (vsearch), while
everything else is answered by Immich:

```bash
curl -s -D - -o /dev/null -X POST -H 'Content-Type: application/json' -d '{"query":"x"}' \
  https://<machine>.<tailnet>.ts.net:8443/api/search/smart | grep -i '^server'
```

Both get a valid HTTPS certificate from Tailscale. Port 8443 is used because 443 is often
already taken by another `tailscale serve`.

**b. Restrict what shared users can reach.** Machine sharing exposes *every* port on the machine
to the person you share it with, under the default allow-all policy. That includes SSH, remote
desktop and whatever dev servers you run. In **Admin console → Access controls → JSON editor**,
replace the `{"src": ["*"], "dst": ["*"], "ip": ["*"]}` grant with the two grants in
[`deploy/tailscale-policy.hujson`](../deploy/tailscale-policy.hujson). Fill in your server's
addresses from `tailscale ip`. You keep full access, and they get port 8443 on that one machine only.

**c. Share the machine:** Admin console → Machines → your server → ⋯ → **Share** → send the link.
They sign up for Tailscale with their *own* Google/Apple account and accept it. They can't see
your other devices, and you can revoke access any time.

*Why not the alternatives?* Tailscale Funnel or a public reverse proxy puts Immich on the open
internet. Cloudflare Tunnel's free plan caps uploads at 100 MB, so phone videos fail. Direct
port forwarding breaks behind carrier-grade NAT, common on Indian ISPs. See [decisions.md](decisions.md).

## 8. Accounts

- Create your admin account on first visit to Immich.
- **Administration → Users → Create user** for each person, as normal (non-admin) users.
- vsearch has no accounts of its own: people log in on the page with their Immich email and
  password, and each person only ever sees their own videos.

## 9. Check everything

```bash
# Immich and vsearch healthy
cd /srv/immich/app && docker compose ps

# Immich is happy with the extra schema
docker exec immich_server immich-admin schema-check      # "No schema drift detected"

# Through the tailnet
curl -s https://<machine>.<tailnet>.ts.net:8443/api/server/ping       # {"res":"pong"}
curl -s https://<machine>.<tailnet>.ts.net:8443/vsearch/health        # {"ok":true,...}
curl -s -o /dev/null -w '%{http_code}\n' https://<machine>.<tailnet>.ts.net:8443/vsearch/api/status   # 401 (needs login)
```

Then upload a video with someone talking and something visible, wait a minute, and search for
it on the page and in the Immich app. To run the full automated test suite against a throwaway
Immich, see [testing.md](testing.md).

Next: [user guide](user-guide.md) for the people using it, [operations](operations.md) for
keeping it running.
