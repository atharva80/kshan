#!/usr/bin/env bash
# Creates the btrfs layout for Immich. Run with: sudo bash 01-btrfs-setup.sh
#
# /srv/immich            btrfs subvolume (snapshot/backup it as one unit)
#   app/                 docker-compose.yml + .env
#   library/             photos/videos (CoW + checksums, scrub catches bit rot)
#   postgres/            database files (NOCOW: avoids CoW fragmentation)
set -euo pipefail

OWNER="${SUDO_USER:?run this with sudo}"
SUB=/srv/immich

if btrfs subvolume show "$SUB" >/dev/null 2>&1; then
  echo "subvolume $SUB already exists"
elif [ -e "$SUB" ]; then
  echo "$SUB exists but is not a btrfs subvolume, aborting" >&2
  exit 1
else
  btrfs subvolume create "$SUB"
fi

mkdir -p "$SUB/app" "$SUB/library" "$SUB/postgres"

# +C only takes effect for files created after it is set, so the dir must be empty
if [ -z "$(ls -A "$SUB/postgres")" ]; then
  chattr +C "$SUB/postgres"
else
  echo "warning: $SUB/postgres is not empty, skipping chattr +C" >&2
fi

chown "$OWNER:$OWNER" "$SUB" "$SUB/app" "$SUB/library"

echo
echo "== result =="
btrfs subvolume show "$SUB" | sed -n '1,4p'
lsattr -d "$SUB/postgres"
ls -ld "$SUB" "$SUB"/*
