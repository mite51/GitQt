"""Repeatable local history budget check against an isolated 5,000-commit repository."""

import json
import subprocess
import tempfile
import time
import tracemalloc
from pathlib import Path

from gitqt.parsers.graph_layout import build_lane_layout
from gitqt.services.git import GitLocator, GitRunner
from gitqt.services.history import HistoryService


def main():
    runner = GitRunner(GitLocator().discover())
    with tempfile.TemporaryDirectory(prefix="gitqt-history-benchmark-") as temporary:
        repo = Path(temporary)
        runner.run(["init", "--initial-branch=main", str(repo)])
        records = []
        for index in range(5000):
            message = f"Commit {index}\n"
            records.append(
                "commit refs/heads/main\n"
                f"committer Benchmark <benchmark@example.invalid> {1700000000 + index} +0000\n"
                f"data {len(message)}\n{message}\n"
            )
        subprocess.run(
            [runner._executable.path, "fast-import", "--quiet"],
            cwd=repo,
            input="".join(records),
            text=True,
            check=True,
        )
        history = HistoryService(runner)
        tracemalloc.start()
        started = time.perf_counter()
        tips = history.get_tips(repo)
        first = history.get_graph(repo, limit=100, refs=tips)
        first_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        second = history.get_graph(repo, limit=100, skip=100, refs=tips)
        page_ms = (time.perf_counter() - started) * 1000
        all_rows = history.get_graph(repo, limit=5000, refs=tips)
        started = time.perf_counter()
        build_lane_layout(all_rows)
        layout_ms = (time.perf_counter() - started) * 1000
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert len(first) == len(second) == 100 and len(all_rows) == 5000
        result = dict(
            commits=5000,
            first_page_ms=round(first_ms, 1),
            next_page_ms=round(page_ms, 1),
            layout_5000_ms=round(layout_ms, 1),
            python_peak_mib=round(peak / 1024**2, 1),
        )
        print(json.dumps(result, indent=2))
        # Broad smoke budgets, measured on a warm local filesystem; see RELEASE.md.
        assert first_ms < 2000 and page_ms < 2000 and layout_ms < 1000 and peak < 64 * 1024**2


if __name__ == "__main__":
    main()
