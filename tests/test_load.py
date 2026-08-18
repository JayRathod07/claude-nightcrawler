"""
Load Tests — Performance and scalability validation for Claude Nightcrawler

Tests:
    • Concurrent database writes (simulating multiple API clients)
    • Task queue throughput (tasks/second)
    • Statistics query performance under large dataset
    • WAL mode concurrent reader/writer throughput
    • Memory stability: no obvious leaks over repeated task cycles
    • Result file I/O throughput
    • Worker backoff math at scale

These tests run in CI but are skipped automatically if the
SKIP_LOAD_TESTS environment variable is set to "true".

Run manually:
    pytest tests/test_load.py -v
    pytest tests/test_load.py -v -s   (with timing output)
"""

import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── Skip guard ────────────────────────────────────────────────────────────────
SKIP = os.getenv("SKIP_LOAD_TESTS", "false").lower() == "true"
skip_load = pytest.mark.skipif(SKIP, reason="SKIP_LOAD_TESTS=true")


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def load_db(tmp_path):
    """Fresh isolated DB for each load test."""
    db_file = str(tmp_path / "load_test.db")
    with patch.dict(os.environ, {"DB_PATH": db_file}):
        import importlib
        import src.database as db_mod
        importlib.reload(db_mod)
        db_mod.init_db()
        yield db_mod


# ══════════════════════════════════════════════════════════════════════════════
# 7.3.1  Task insertion throughput
# ══════════════════════════════════════════════════════════════════════════════

class TestInsertionThroughput:

    @skip_load
    def test_sequential_inserts_100_tasks(self, load_db):
        """100 sequential task inserts should complete in under 2 seconds."""
        N = 100
        start = time.perf_counter()
        for i in range(N):
            load_db.add_task(f"Load test task {i} — " + "x" * 200)
        elapsed = time.perf_counter() - start

        stats = load_db.get_statistics()
        assert stats["total_tasks"] == N
        assert elapsed < 2.0, f"100 sequential inserts took {elapsed:.2f}s (limit: 2s)"
        print(f"\n  Sequential inserts: {N} tasks in {elapsed:.3f}s ({N/elapsed:.0f} tasks/s)")

    @skip_load
    def test_concurrent_inserts_10_threads(self, load_db):
        """10 threads each inserting 10 tasks — all should succeed without errors."""
        N_THREADS = 10
        N_PER_THREAD = 10
        errors = []

        def insert_batch(thread_id: int):
            try:
                for i in range(N_PER_THREAD):
                    load_db.add_task(f"Thread {thread_id} task {i}")
            except Exception as e:
                errors.append(f"Thread {thread_id}: {e}")

        threads = [threading.Thread(target=insert_batch, args=(t,)) for t in range(N_THREADS)]
        start = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        elapsed = time.perf_counter() - start

        assert errors == [], f"Concurrent insert errors: {errors}"
        stats = load_db.get_statistics()
        assert stats["total_tasks"] == N_THREADS * N_PER_THREAD
        print(f"\n  Concurrent inserts: {N_THREADS}×{N_PER_THREAD} tasks in {elapsed:.3f}s")

    @skip_load
    def test_threadpool_inserts_50_concurrent(self, load_db):
        """50 concurrent inserts via ThreadPoolExecutor."""
        N = 50
        errors = []

        def insert(i):
            try:
                return load_db.add_task(f"Pool task {i} — " + "a" * 500)
            except Exception as e:
                errors.append(str(e))
                return None

        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(insert, i) for i in range(N)]
            ids = [f.result() for f in as_completed(futures)]
        elapsed = time.perf_counter() - start

        assert errors == [], f"ThreadPool errors: {errors}"
        valid_ids = [i for i in ids if i is not None]
        assert len(valid_ids) == N
        print(f"\n  ThreadPool inserts: {N} tasks in {elapsed:.3f}s ({N/elapsed:.0f} tasks/s)")


# ══════════════════════════════════════════════════════════════════════════════
# 7.3.2  Read throughput (dashboard polling simulation)
# ══════════════════════════════════════════════════════════════════════════════

class TestReadThroughput:

    @skip_load
    def test_statistics_query_performance_1000_tasks(self, load_db):
        """Statistics query should return in under 200ms with 1000 tasks."""
        # Insert 1000 tasks with mixed statuses
        statuses = ["queued", "running", "completed", "failed"]
        for i in range(1000):
            tid = load_db.add_task(f"Stat test task {i}")
            status = statuses[i % 4]
            if status != "queued":
                load_db.update_task_status(tid, "running")
                load_db.update_task_status(tid, status)

        start = time.perf_counter()
        for _ in range(10):  # 10 successive queries
            stats = load_db.get_statistics()
        elapsed = time.perf_counter() - start / 10

        assert stats["total_tasks"] == 1000
        assert elapsed < 0.2, f"Statistics query (avg over 10 runs) took {elapsed:.3f}s (limit: 0.2s)"
        print(f"\n  Statistics query (1000 tasks, 10 runs): {elapsed:.3f}s avg")

    @skip_load
    def test_get_all_tasks_pagination_performance(self, load_db):
        """Paginated task listing should complete in under 100ms per page."""
        N = 500
        for i in range(N):
            load_db.add_task(f"Page test task {i}")

        start = time.perf_counter()
        for page in range(5):
            tasks = load_db.get_all_tasks(limit=20, offset=page * 20)
            assert len(tasks) == 20
        elapsed = (time.perf_counter() - start) / 5  # avg per page

        assert elapsed < 0.1, f"Paginated query avg took {elapsed:.3f}s (limit: 0.1s)"
        print(f"\n  Paginated get_all_tasks (500 tasks, page_size=20): {elapsed*1000:.1f}ms avg")

    @skip_load
    def test_concurrent_readers_while_writing(self, load_db):
        """Multiple reader threads should not be blocked by a writer thread."""
        # Pre-populate
        for i in range(100):
            load_db.add_task(f"Pre-existing task {i}")

        read_times = []
        write_errors = []
        read_errors = []
        stop_flag = threading.Event()

        def reader():
            while not stop_flag.is_set():
                try:
                    t0 = time.perf_counter()
                    load_db.get_all_tasks(limit=20)
                    read_times.append(time.perf_counter() - t0)
                    time.sleep(0.005)
                except Exception as e:
                    read_errors.append(str(e))

        def writer():
            try:
                for i in range(50):
                    tid = load_db.add_task(f"Write-under-load task {i}")
                    load_db.update_task_status(tid, "running")
                    load_db.update_task_status(tid, "completed")
                    time.sleep(0.01)
            except Exception as e:
                write_errors.append(str(e))
            finally:
                stop_flag.set()

        readers = [threading.Thread(target=reader, daemon=True) for _ in range(3)]
        writer_t = threading.Thread(target=writer)
        for r in readers:
            r.start()
        writer_t.start()
        writer_t.join(timeout=15)
        stop_flag.set()
        for r in readers:
            r.join(timeout=3)

        assert write_errors == [], f"Writer errors: {write_errors}"
        assert read_errors == [], f"Reader errors: {read_errors}"
        assert len(read_times) > 0
        avg_read = sum(read_times) / len(read_times)
        max_read = max(read_times)
        assert avg_read < 0.05, f"Avg read time under write load: {avg_read*1000:.1f}ms (limit: 50ms)"
        print(f"\n  Concurrent R/W: {len(read_times)} reads, avg={avg_read*1000:.1f}ms, max={max_read*1000:.1f}ms")


# ══════════════════════════════════════════════════════════════════════════════
# 7.3.3  Task queue throughput (full cycle)
# ══════════════════════════════════════════════════════════════════════════════

class TestQueueThroughput:

    @skip_load
    def test_task_cycle_throughput_100_tasks(self, load_db):
        """
        Process 100 tasks through the full queue cycle (add → pick → complete)
        in a single thread — target: at least 50 tasks/second.
        """
        N = 100
        ids = [load_db.add_task(f"Cycle task {i}") for i in range(N)]

        start = time.perf_counter()
        processed = 0
        while True:
            task = load_db.get_next_task()
            if task is None:
                break
            load_db.update_task_status(task["id"], "running")
            load_db.update_task_status(task["id"], "completed")
            processed += 1

        elapsed = time.perf_counter() - start

        assert processed == N
        rate = N / elapsed
        assert rate >= 50, f"Task cycle throughput: {rate:.0f} tasks/s (minimum: 50)"
        print(f"\n  Queue throughput: {N} tasks in {elapsed:.3f}s ({rate:.0f} tasks/s)")

    @skip_load
    def test_get_next_task_speed_empty_queue(self, load_db):
        """get_next_task() on an empty queue should be very fast (< 5ms)."""
        times = []
        for _ in range(100):
            t0 = time.perf_counter()
            load_db.get_next_task()
            times.append(time.perf_counter() - t0)

        avg = sum(times) / len(times)
        assert avg < 0.005, f"get_next_task() on empty queue avg: {avg*1000:.2f}ms (limit: 5ms)"
        print(f"\n  get_next_task (empty, 100 calls): avg={avg*1000:.2f}ms")


# ══════════════════════════════════════════════════════════════════════════════
# 7.3.4  Result file I/O throughput
# ══════════════════════════════════════════════════════════════════════════════

class TestResultFileIO:

    @skip_load
    def test_write_50_result_files(self, load_db, tmp_path):
        """Write 50 result Markdown files — should complete in under 1 second."""
        results_dir = tmp_path / "results"
        results_dir.mkdir()

        import importlib
        import src.agent_worker as worker_mod
        with patch.dict(os.environ, {"RESULTS_DIR": str(results_dir)}):
            importlib.reload(worker_mod)

        N = 50
        response = "Claude says: " + "A detailed and thoughtful response. " * 50

        start = time.perf_counter()
        for i in range(N):
            task_id = load_db.add_task(f"File write task {i}")
            worker_mod._save_result(task_id, f"Prompt for task {i}", response)
        elapsed = time.perf_counter() - start

        files = list(results_dir.glob("task_*.md"))
        assert len(files) == N
        assert elapsed < 1.0, f"50 result file writes took {elapsed:.3f}s (limit: 1s)"
        print(f"\n  Result file writes: {N} files in {elapsed:.3f}s ({N/elapsed:.0f} files/s)")

    @skip_load
    def test_large_result_file_write(self, load_db, tmp_path):
        """Write a single large result file (50KB response) under 50ms."""
        results_dir = tmp_path / "results"
        results_dir.mkdir()

        import importlib
        import src.agent_worker as worker_mod
        with patch.dict(os.environ, {"RESULTS_DIR": str(results_dir)}):
            importlib.reload(worker_mod)

        task_id = load_db.add_task("Large response task")
        large_response = "Word. " * 8000  # ~48KB

        start = time.perf_counter()
        path = worker_mod._save_result(task_id, "Large response task", large_response)
        elapsed = time.perf_counter() - start

        assert Path(path).exists()
        size = Path(path).stat().st_size
        assert size > 40000  # at least 40KB written
        assert elapsed < 0.05, f"Large file write took {elapsed*1000:.1f}ms (limit: 50ms)"
        print(f"\n  Large file write ({size/1024:.0f}KB): {elapsed*1000:.1f}ms")


# ══════════════════════════════════════════════════════════════════════════════
# 7.3.5  Backoff delay math at scale
# ══════════════════════════════════════════════════════════════════════════════

class TestBackoffAtScale:

    @skip_load
    def test_backoff_sequence_never_exceeds_cap(self):
        """Backoff delay should never exceed 120 seconds regardless of retry count."""
        import src.agent_worker as worker_mod
        for retry in range(0, 50):
            delay = worker_mod._backoff_delay(retry)
            assert delay <= 120, f"Backoff exceeded cap at retry {retry}: {delay}s"

    @skip_load
    def test_backoff_doubles_until_cap(self):
        """Verify geometric growth before hitting the cap."""
        import src.agent_worker as worker_mod
        prev = None
        for retry in range(0, 10):
            delay = worker_mod._backoff_delay(retry)
            if prev is not None and delay < 120:
                assert delay >= prev  # monotonically increasing before cap
            prev = delay

    @skip_load
    def test_1000_backoff_calls_no_overflow(self):
        """Calling _backoff_delay 1000 times should not raise or produce infinity."""
        import src.agent_worker as worker_mod
        import math
        for i in range(1000):
            d = worker_mod._backoff_delay(i)
            assert not math.isinf(d)
            assert not math.isnan(d)
            assert d >= 0


# ══════════════════════════════════════════════════════════════════════════════
# 7.3.6  Memory stability (basic)
# ══════════════════════════════════════════════════════════════════════════════

class TestMemoryStability:

    @skip_load
    def test_no_obvious_memory_leak_over_1000_operations(self, load_db):
        """
        Perform 1000 DB operations and ensure memory growth is bounded.
        This is a rough check using tracemalloc — not a precise leak detector,
        but catches obvious unbounded accumulations.
        """
        import tracemalloc
        tracemalloc.start()

        snapshot_before = tracemalloc.take_snapshot()

        for i in range(1000):
            tid = load_db.add_task(f"Memory test task {i}")
            load_db.update_task_status(tid, "running")
            load_db.update_task_status(tid, "completed")

        snapshot_after = tracemalloc.take_snapshot()
        tracemalloc.stop()

        top_stats = snapshot_after.compare_to(snapshot_before, "lineno")
        total_growth_kb = sum(s.size_diff for s in top_stats if s.size_diff > 0) / 1024

        # Allow up to 5MB growth for 1000 task cycles
        assert total_growth_kb < 5120, (
            f"Memory grew by {total_growth_kb:.0f}KB over 1000 task cycles (limit: 5120KB)"
        )
        print(f"\n  Memory growth over 1000 task cycles: {total_growth_kb:.0f}KB")
