#!/usr/bin/env bash
# Publishes Immich and the video search page to your tailnet only (not the internet):
#   https://<machine>.<tailnet>.ts.net:8443/          -> Immich      (127.0.0.1:2283)
#   https://<machine>.<tailnet>.ts.net:8443/vsearch   -> video search (127.0.0.1:2284)
# Same origin for both, so the page can reuse the Immich login cookie.
# Run with: sudo bash 03-tailscale-serve.sh   (or without sudo if you set `tailscale set --operator=$USER`)
set -euo pipefail

PORT="${IMMICH_HTTPS_PORT:-8443}"   # 443 is often taken by other serve configs; 8443 works fine in the apps

tailscale serve --bg --https="$PORT" http://127.0.0.1:2283
tailscale serve --bg --https="$PORT" --set-path=/vsearch http://127.0.0.1:2284
# Immich's own search bar (apps + web) -> vsearch, which asks Immich and blends in video moments
tailscale serve --bg --https="$PORT" --set-path=/api/search/smart http://127.0.0.1:2284/hook/smart-search
tailscale serve status
