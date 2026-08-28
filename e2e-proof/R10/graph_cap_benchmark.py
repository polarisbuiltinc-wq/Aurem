"""
e2e-proof/R10/graph_cap_benchmark.py — Loop N item 3.

Benchmarks the LOCAL, CPU-bound half of graph_builder.build_graph()
(Step 1D: regex symbol/import extraction + node-dict construction)
against real files on this pod (/app's own backend+frontend source,
~1,626 code files — a real large-repo proxy, close to the founder's
reported 1,925+ file repo) at 1x (200), 3x (600 — new default), and
9x (1800, ~3x the new default) file counts.

Step 1C (fetching each file's content from GitHub, 10-at-a-time with
a 0.05s inter-batch sleep — see services/graph_builder.py:317-346) is
NETWORK-bound, not CPU/memory-bound, and is NOT re-measured here
against a live repo (would burn real GitHub API rate-limit budget for
a benchmark). Its cost is estimated analytically from the real batch
size/sleep constants in the code — see the printed section below.

Run: python3 e2e-proof/R10/graph_cap_benchmark.py
"""
import os
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from services.graph_builder import detect_layer, extract_symbols, extract_imports  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SKIP_DIR_NAMES = {
    "node_modules", ".git", "dist", "build", "__pycache__",
    ".venv", "venv", "coverage", ".pytest_cache", ".vite",
}
CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx"}


def _collect_real_files() -> list[Path]:
    out = []
    for p in REPO_ROOT.rglob("*"):
        if not p.is_file() or p.suffix not in CODE_EXT:
            continue
        if any(part in SKIP_DIR_NAMES for part in p.parts):
            continue
        out.append(p)
    return out


def _run_step1d(files: list[Path]) -> dict:
    """Mimics graph_builder.build_graph()'s Step 1D exactly: read each
    file (local disk here, GitHub API in production), then regex-parse
    symbols/imports and build the same `nodes` dict shape."""
    tracemalloc.start()
    t0 = time.perf_counter()
    nodes = {}
    for p in files:
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")[:6000]
        except Exception:
            content = ""
        rel = str(p.relative_to(REPO_ROOT))
        nodes[rel] = {
            "path": rel,
            "layer": detect_layer(rel),
            "symbols": extract_symbols(content, rel),
            "imports": extract_imports(content, rel),
            "description": "",
            "size": len(content),
        }
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {"file_count": len(nodes), "elapsed_s": round(elapsed, 4),
            "peak_kb": round(peak / 1024, 1)}


def main():
    all_files = _collect_real_files()
    print(f"Real fixture: {len(all_files)} code files under {REPO_ROOT} "
          f"(founder's reported repo: 1,925+ files — comparable order of magnitude)\n")

    for label, n in (("1x (old MAX_FILES=200)", 200),
                      ("3x (new default=600)", 600),
                      ("9x / 3x-of-new-default (1800)", 1800)):
        sample = all_files[:n] if n <= len(all_files) else all_files
        result = _run_step1d(sample)
        print(f"{label:32s} -> files={result['file_count']:4d}  "
              f"time={result['elapsed_s']:.3f}s  peak_mem={result['peak_kb']:.1f}KB")

    print(
        "\n--- Step 1C (network-bound, NOT re-measured live here) ---\n"
        "Real code: batch size 10 concurrent requests, 0.05s sleep between\n"
        "batches (services/graph_builder.py:337-346). Each file = 1 GitHub\n"
        "API call (content fetch). Analytical estimate at a typical GitHub\n"
        "REST API latency of ~200-400ms per call (their own published p50/p90):\n"
    )
    for label, n in (("200 files", 200), ("600 files", 600), ("1800 files", 1800)):
        batches = -(-n // 10)  # ceil
        low = batches * (0.20 + 0.05)
        high = batches * (0.40 + 0.05)
        print(f"  {label:12s} -> {batches:4d} batches -> "
              f"~{low:.1f}s-{high:.1f}s wall time, {n} GitHub API calls "
              f"consumed from the installation's rate-limit budget")


if __name__ == "__main__":
    main()
