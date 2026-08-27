"""Progress and Live Ticker Utility Module (progress.py)."""
import sys
import time
import threading

class LiveTicker:
    def __init__(self, prefix="Processing"):
        self.prefix = prefix
        self.stop_event = threading.Event()
        self.thread = None
        self.t0 = time.time()

    def update_prefix(self, new_prefix):
        self.prefix = new_prefix

    def _run(self):
        spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        idx = 0
        while not self.stop_event.is_set():
            elapsed = time.time() - self.t0
            spin = spinner[idx % len(spinner)]
            print(f"\r  {spin} {self.prefix} [Elapsed: {elapsed:.0f}s]...", end="", flush=True)
            idx += 1
            time.sleep(0.4)

    def __enter__(self):
        self.t0 = time.time()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=0.6)
        elapsed = time.time() - self.t0
        if exc_type is None:
            print(f"\r  ✓ {self.prefix} - Done in {elapsed:.1f}s.                                     \n", flush=True)
        else:
            print(f"\r  ✗ {self.prefix} - Error after {elapsed:.1f}s.                                    \n", flush=True)
