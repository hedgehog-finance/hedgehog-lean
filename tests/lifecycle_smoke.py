"""Exercise the packaged bridge and real paper engine with no broker or feed."""
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time

runtime = Path(sys.argv[1]).resolve()
manifest = json.loads((runtime / "runtime.json").read_text())
python = str(runtime / manifest["python"])
code = '''from AlgorithmImports import *
from pathlib import Path
import json
import os
class GatewayStrategy(QCAlgorithm):
    def initialize(self):
        self.add_equity("SPY", Resolution.DAILY)
        Path(__file__).with_name("pids.json").write_text(json.dumps([os.getpid(), os.getppid()]))
    def on_data(self, data):
        pass
'''
input_data = {"code": code, "bars": [{"symbol": "SPY", "time": "2024-01-02T21:00:00Z", "availableAt": "2024-01-02T21:00:00Z", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 100000}],
              "dataset": {"market": "us", "frequency": "daily", "symbols_json": '["SPY"]', "adjustment_json": json.dumps({"prices": "raw", "volumeUnit": "shares", "currency": "USD"})},
              "initialCash": 100000, "commission": 0, "slippage": 0}


class ProcessProbe:
    def __init__(self, pid):
        self.pid = pid
        self.handle = None
        if sys.platform == "win32":
            import _winapi
            self.handle = _winapi.OpenProcess(_winapi.SYNCHRONIZE, False, pid)

    def exited(self):
        if self.handle is not None:
            import _winapi
            return _winapi.WaitForSingleObject(self.handle, 0) == _winapi.WAIT_OBJECT_0
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(self.pid)], capture_output=True, text=True, check=False).stdout.strip()
        return not state or state.startswith("Z")

    def close(self):
        if self.handle is not None:
            import _winapi
            _winapi.CloseHandle(self.handle)


def exercise(crash):
    with tempfile.TemporaryDirectory(prefix="lean-lifecycle-") as directory:
        root = Path(directory)
        probes = []
        with (root / "bridge.log").open("wb") as log:
            env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1")
            env.pop("PYTHONHOME", None)
            env.pop("PYTHONPATH", None)
            child = subprocess.Popen([python, str(runtime / "bridge/bridge.py"), "--provider", "lean", "--stdio", "--state-dir", str(root), "--lean-root", str(runtime)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf-8", env=env)
            replies = queue.Queue()

            def read_replies():
                for line in child.stdout:
                    replies.put(json.loads(line))
                replies.put(None)

            reader = threading.Thread(target=read_replies, daemon=True)
            reader.start()
            sequence = 0

            def call(method, params):
                nonlocal sequence
                sequence += 1
                child.stdin.write(json.dumps({"jsonrpc": "2.0", "id": sequence, "method": method, "params": params}) + "\n")
                child.stdin.flush()
                response = replies.get(timeout=45)
                assert response and response.get("id") == sequence and "error" not in response, response
                return response["result"]

            try:
                assert "simulate" in call("hello", {})["operations"]
                tombstone = {"runId": "late", "generation": "one", "fingerprint": "fixture-late"}
                assert call("cancel", tombstone)["cancellationRecorded"]
                assert call("submit", tombstone)["status"] == "cancelled"
                assert not (root / "late").exists()
                identity = {"runId": "paper", "generation": "one", "fingerprint": "fixture-paper"}
                call("submit", dict(identity, operation="simulate", kind="session", input=input_data))
                for _ in range(900):
                    path = root / "paper/lean.log"
                    if path.exists() and "AlgorithmManager.Run(): Begin DataStream" in path.read_text(errors="replace"):
                        break
                    state = call("status", dict(identity, after=0))
                    assert state["status"] == "running", state
                    time.sleep(0.1)
                else:
                    raise AssertionError("Paper engine did not initialize")
                pids = json.loads((root / "paper/pids.json").read_text())
                assert len(set(pids)) == 2 and child.pid not in pids and all(pid > 0 for pid in pids)
                probes = [ProcessProbe(pid) for pid in pids]
                assert not any(probe.exited() for probe in probes)
                if crash:
                    child.kill()
                    child.wait(timeout=10)
                else:
                    call("cancel", identity)
                    for _ in range(200):
                        if call("status", dict(identity, after=0))["status"] == "cancelled":
                            break
                        time.sleep(0.1)
                    else:
                        raise AssertionError("Cancellation was not confirmed")
                for _ in range(200):
                    if all(probe.exited() for probe in probes):
                        break
                    time.sleep(0.1)
                else:
                    raise AssertionError("Owned worker or LEAN child survived its owner")
            except Exception:
                for path in (root / "bridge.log", root / "paper/worker.log", root / "paper/lean.log"):
                    if path.exists():
                        print(path.name, path.read_text(errors="replace")[-6000:], file=sys.stderr)
                raise
            finally:
                if child.poll() is None:
                    child.stdin.close()
                    try:
                        child.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=10)
                for probe in probes:
                    if not probe.exited():
                        if sys.platform == "win32":
                            subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"), "/PID", str(probe.pid), "/T", "/F"], capture_output=True, timeout=10)
                        else:
                            os.kill(probe.pid, signal.SIGKILL)
                    probe.close()
                if not child.stdin.closed:
                    child.stdin.close()
                reader.join(timeout=5)
                child.stdout.close()


exercise(crash=False)
exercise(crash=True)
print(f"PASS {manifest['target']}: durable cancellation, real paper cancellation, owner-exit cleanup")
