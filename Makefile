# Benchmark dataset for video moment search. Everything lands in eval/data/ (gitignored).
# Each step skips work whose output already exists, so re-running is fast.
.PHONY: eval-data

eval-data:
	uv run --with remotezip python eval/data_charades.py
	python3 eval/data_fleurs.py
	python3 eval/build_queries.py
