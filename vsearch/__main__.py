import logging
import os

import uvicorn

from . import config
from .services import services
from .web import app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "huggingface_hub", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if os.environ.get("VSEARCH_INDEXER", "on") != "off":
        services.start_indexer()
    uvicorn.run(app, host="0.0.0.0", port=config.PORT, log_level="warning", access_log=False,
                proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
