"""Gateway Quant Protocol 1.0. A persistent, user-owned bridge for external tools.

Only bundled LEAN uses stdio. External bridges listen on loopback with a token.
The journal is transport recovery state; Gateway owns the canonical business DB.
"""
import argparse
import contextlib
import hashlib
import hmac
import importlib.metadata
import importlib.util
import importlib
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROVIDERS = {
    "lean": (None, ["validate", "backtest", "simulate"]),
    "backtrader": ("backtrader", ["validate", "backtest"]),
    "qlib": ("qlib", ["research"]),
    "vnpy": ("vnpy_ctastrategy", ["cta-backtest"]),
    "quantdinger": (None, ["backtest"]),
    "timesfm": ("timesfm", ["forecast"]),
    "czsc": ("czsc", ["analyze"]),
    "quantlib": ("QuantLib", ["option-price"]),
}
MAX_REQUEST = 32 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", value):
        raise ValueError("Invalid identifier")
    return value


class Bridge:
    def __init__(self, options):
        self.options = options
        self.root = Path(options.state_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.children = {}
        self.closed = False
        # A second instance cannot claim this journal while the first is serving.
        self.lock_file = open(self.root / "bridge.lock", "a+b")
        if os.name == "nt":
            import msvcrt
            self.lock_file.seek(0)
            self.lock_file.write(b"0")
            self.lock_file.flush()
            self.lock_file.seek(0)
            msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.db = sqlite3.connect(self.root / "journal.sqlite", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,generation TEXT NOT NULL,fingerprint TEXT NOT NULL,status TEXT NOT NULL,result TEXT);
          CREATE TABLE IF NOT EXISTS events(run_id TEXT NOT NULL,sequence INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(run_id,sequence));
          CREATE TABLE IF NOT EXISTS feeds(run_id TEXT NOT NULL,batch_id TEXT NOT NULL,hash TEXT NOT NULL,payload TEXT NOT NULL,delivered INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(run_id,batch_id));
        """)
        self.db.execute("UPDATE runs SET status='interrupted' WHERE status IN ('running','cancel_requested')")
        self.db.commit()

    def hello(self):
        provider = self.options.provider
        module, operations = PROVIDERS[provider]
        available, reason, version = True, None, "unknown"
        if module and importlib.util.find_spec(module) is None:
            available, reason = False, "Required Python package is not installed in this interpreter"
        elif module:
            package = {"qlib": "pyqlib", "QuantLib": "QuantLib", "vnpy_ctastrategy": "vnpy_ctastrategy"}.get(module, module)
            with contextlib.suppress(importlib.metadata.PackageNotFoundError):
                version = importlib.metadata.version(package)
            try:
                with contextlib.redirect_stdout(sys.stderr):
                    loaded=importlib.import_module(module)
                expected={"qlib":"init","backtrader":"Cerebro","timesfm":"TimesFM_2p5_200M_torch","czsc":"CZSC","QuantLib":"AnalyticEuropeanEngine","vnpy_ctastrategy":"CtaTemplate"}
                if not hasattr(loaded,expected[module]):
                    available,reason=False,"Installed package does not expose the adapted API"
            except Exception:
                available,reason=False,"Provider import failed; inspect the user's Python environment"
        if provider == "lean":
            root = Path(self.options.lean_root or ".")
            available = (root / "engine/QuantConnect.Lean.Launcher.dll").is_file() and (root / "runtime.json").is_file()
            if available:
                version = json.loads((root / "runtime.json").read_text())["sourceCommit"]
            else:
                reason = "Bundled LEAN files are missing"
        if provider == "timesfm" and (not self.options.model or not Path(self.options.model).exists()):
            available, reason = False, "Specify existing local TimesFM 2.5 weights with --model"
        if provider == "quantdinger":
            available = bool(self.options.upstream and self.options.upstream_token_file)
            reason = None if available else "Configure --upstream and --upstream-token-file for QuantDinger"
        return {"protocolVersion": "1.0", "providerId": provider, "version": version,
                "operations": operations if available else [], "diagnostics": [reason] if reason else [],
                "recovery": "query-only; interrupted jobs are never resubmitted automatically"}

    def event(self, run_id, kind, data, timestamp=None):
        with self.lock:
            row = self.db.execute("SELECT generation FROM runs WHERE id=?", (run_id,)).fetchone()
            seq = self.db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM events WHERE run_id=?", (run_id,)).fetchone()[0]
            payload = {"id": str(uuid.uuid4()), "sequence": seq, "generation": row[0], "type": kind, "timestamp": timestamp or now(), "data": data}
            self.db.execute("INSERT INTO events VALUES(?,?,?)", (run_id, seq, json.dumps(payload, allow_nan=False)))
            self.db.commit()

    def run(self, run_id, operation, data):
        folder = self.root / run_id
        folder.mkdir(mode=0o700)
        request = folder / "input.json"
        request.write_text(json.dumps({"provider": self.options.provider, "operation": operation, "input": data,
                                       "leanRoot": self.options.lean_root, "model": self.options.model,
                                       "upstream": self.options.upstream, "upstreamTokenFile": self.options.upstream_token_file,"parentPid":os.getpid()}))
        env = {key: value for key, value in os.environ.items() if key in ("PATH", "SystemRoot", "TEMP", "TMPDIR", "LANG", "DOTNET_ROOT", "PYTHONNET_PYDLL")}
        env.update(HOME=str(folder), USERPROFILE=str(folder), PYTHONUNBUFFERED="1", PYTHONNOUSERSITE="1")
        with self.lock:
            if self.closed:
                raise ValueError("Bridge is shutting down")
            with open(folder / "worker.log", "wb") as log:
                child = subprocess.Popen([sys.executable, str(Path(__file__).with_name("worker.py")), str(request)],
                                         cwd=folder, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=log, text=True)
            self.children[run_id] = child
        threading.Thread(target=self.collect, args=(run_id, child), daemon=True).start()

    def collect(self, run_id, child):
        result, error, cancel_ack = None, None, False
        try:
            for line in child.stdout:
                if len(line) > 8 * 1024 * 1024:
                    raise ValueError("Worker response too large")
                message = json.loads(line)
                if "event" in message:
                    self.event(run_id, message["event"], message.get("data", {}), message.get("timestamp"))
                    if message["event"] == "status" and message.get("data",{}).get("upstreamCancelled"):
                        cancel_ack = True
                if "result" in message:
                    result = message["result"]
            exit_code = child.wait()
            if exit_code:
                error = "Worker failed; inspect its local log"
        except Exception:
            error = "Invalid worker output; inspect its local log"
            child.terminate()
            child.wait(timeout=10)
        with self.lock:
            row = self.db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            status = "interrupted" if self.closed else "cancelled" if row[0] == "cancel_requested" else "failed" if error else "completed"
            if row[0] == "cancel_requested" and self.options.provider == "quantdinger" and not cancel_ack:
                status = "interrupted"
            result = {"error": error} if error else result
            self.event(run_id, "status", {"status": status})
            self.db.execute("UPDATE runs SET status=?,result=? WHERE id=?", (status, json.dumps(result, allow_nan=False), run_id))
            self.db.commit()
            self.children.pop(run_id, None)
            child.stdout.close()
            child.stdin.close()

    def call(self, method, params):
        if method == "hello":
            return self.hello()
        if method == "shutdown":
            if not self.options.stdio:
                raise ValueError("Only the bundled stdio owner may shut down this bridge")
            self.close()
            return {"stopped": True}
        if method == "execute":
            from providers import execute
            operation = params["operation"]
            if operation not in self.hello()["operations"] or operation not in ("validate", "option-price"):
                raise ValueError("Operation unavailable")
            # Serialized: QuantLib's evaluation date is process-global.
            with self.lock, contextlib.redirect_stdout(sys.stderr):
                return execute(self.options.provider, operation, params["input"])
        if method == "submit":
            run_id, generation = identifier(params["runId"]), identifier(params["generation"])
            with self.lock:
                old = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
                if old:
                    if old["generation"] != generation or old["fingerprint"] != params["fingerprint"]:
                        raise ValueError("Conflicting run identity")
                    return {"runId": run_id, "status": old["status"]}
                operation = params["operation"]
                if operation not in self.hello()["operations"] or operation in ("validate", "option-price"):
                    raise ValueError("Operation unavailable")
                if (params["kind"] == "session") != (operation == "simulate"):
                    raise ValueError("Invalid execution mode")
                self.db.execute("INSERT INTO runs VALUES(?,?,?,'running',NULL)", (run_id, generation, params["fingerprint"]))
                self.db.commit()
                self.event(run_id, "status", {"status": "running"})
                try:
                    self.run(run_id, operation, params["input"])
                except Exception:
                    self.db.execute("UPDATE runs SET status='failed' WHERE id=?", (run_id,))
                    self.db.commit()
                    raise
                return {"runId": run_id, "status": "running"}
        run_id = identifier(params["runId"])
        with self.lock:
            row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None and method == "cancel" and self.options.provider != "quantdinger":
                # The user explicitly cancels an uncertain submission. This
                # journal owner has no local worker; never start one to cancel it.
                return {"runId": run_id, "absent": True}
            if not row or row["generation"] != params["generation"]:
                raise ValueError("Run not found or generation mismatch")
            if method == "status":
                after = params.get("after", 0)
                events = self.db.execute("SELECT payload FROM events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT 501", (run_id, after)).fetchall()
                return {"runId": run_id, "generation": row["generation"], "status": row["status"],
                        "events": [json.loads(event[0]) for event in events[:500]], "hasMore": len(events) > 500,
                        "result": json.loads(row["result"]) if row["result"] else None}
            if method == "cancel":
                child = self.children.get(run_id)
                if child:
                    self.db.execute("UPDATE runs SET status='cancel_requested' WHERE id=?", (run_id,))
                    self.db.commit()
                    child.terminate()
                elif row["status"] == "interrupted" and self.options.provider != "quantdinger":
                    # This bridge has exclusive ownership of the journal and no child.
                    self.db.execute("UPDATE runs SET status='cancelled' WHERE id=?", (run_id,))
                    self.db.commit()
                    self.event(run_id, "status", {"status": "cancelled"})
                return {"runId": run_id}
            if method == "feed":
                child = self.children.get(run_id)
                if not child or child.poll() is not None:
                    raise ValueError("Simulation is not running")
                batch = params["batch"]
                batch_id = identifier(batch["id"])
                payload = json.dumps(batch,sort_keys=True,allow_nan=False)
                fingerprint = hashlib.sha256(payload.encode()).hexdigest()
                prior = self.db.execute("SELECT * FROM feeds WHERE run_id=? AND batch_id=?",(run_id,batch_id)).fetchone()
                if prior:
                    if prior["hash"] != fingerprint or not prior["delivered"]:
                        raise ValueError("Conflicting or uncertain feed batch; inspect session")
                    return {"accepted": True, "duplicate": True}
                request = json.loads((self.root / run_id / "input.json").read_text())
                if request["operation"] != "simulate":
                    raise ValueError("Feed requires a simulation")
                symbols = set(json.loads(request["input"]["dataset"]["symbols_json"]))
                previous = {}
                for saved in self.db.execute("SELECT payload FROM feeds WHERE run_id=?",(run_id,)):
                    for bar in json.loads(saved[0])["bars"]:
                        previous[bar["symbol"]] = max(previous.get(bar["symbol"],float('-inf')),datetime.fromisoformat(bar["time"].replace("Z","+00:00")).timestamp())
                if not 1 <= len(batch["bars"]) <= 1000:
                    raise ValueError("Invalid batch size")
                for bar in batch["bars"]:
                    at = datetime.fromisoformat(bar["time"].replace("Z","+00:00")).timestamp()
                    available = datetime.fromisoformat(bar["availableAt"].replace("Z","+00:00")).timestamp()
                    if bar["symbol"] not in symbols or at <= previous.get(bar["symbol"],float('-inf')) or abs(time.time()-available) > 300 or at > available or available > time.time()+5:
                        raise ValueError("Bars must be new, ordered and available within five minutes of current time")
                    if bar["low"] > min(bar["open"],bar["close"]) or bar["high"] < max(bar["open"],bar["close"]) or min(bar[key] for key in ("open","high","low","close")) <= 0 or bar["volume"] < 0:
                        raise ValueError("Invalid OHLCV")
                    previous[bar["symbol"]] = at
                self.db.execute("INSERT INTO feeds VALUES(?,?,?,?,0)",(run_id,batch_id,fingerprint,payload));self.db.commit()
                child.stdin.write(payload + "\n")
                child.stdin.flush()
                self.db.execute("UPDATE feeds SET delivered=1 WHERE run_id=? AND batch_id=?",(run_id,batch_id));self.db.commit()
                return {"accepted": True}
        raise ValueError("Unsupported method")

    def close(self):
        with self.lock:
            self.closed = True
            children = list(self.children.values())
        for child in children:
            child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
        # No automatic resume: journal status must be explicitly reconciled.


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=PROVIDERS, required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--token-file")
    parser.add_argument("--lean-root")
    parser.add_argument("--model")
    parser.add_argument("--upstream")
    parser.add_argument("--upstream-token-file")
    options = parser.parse_args()
    if options.stdio and options.provider != "lean":
        parser.error("External tools are user-owned HTTP services")
    if not options.stdio and not options.token_file:
        parser.error("--token-file is required")
    token = Path(options.token_file).read_text().strip() if options.token_file else None
    if token is not None and len(token) < 16:
        parser.error("Token must contain at least 16 characters")
    bridge = Bridge(options)

    def reply(raw):
        request = json.loads(raw)
        try:
            if request.get("jsonrpc") != "2.0" or not isinstance(request.get("params"), dict):
                raise ValueError("Invalid request")
            result = bridge.call(request["method"], request["params"])
            return json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": result}, allow_nan=False)
        except Exception as error:
            print(type(error).__name__ + ": " + str(error), file=sys.stderr)
            return json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32000, "message": "Operation failed; inspect bridge log"}})

    try:
        if options.stdio:
            for raw in sys.stdin:
                if len(raw) > MAX_REQUEST:
                    raise ValueError("Request too large")
                print(reply(raw), flush=True)
                if bridge.closed:
                    break
        else:
            class Handler(BaseHTTPRequestHandler):
                def do_POST(self):
                    if self.path != "/rpc" or not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                        self.send_error(403)
                        return
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 1 or length > MAX_REQUEST:
                        self.send_error(413)
                        return
                    try:
                        data = reply(self.rfile.read(length)).encode()
                    except (ValueError, KeyError):
                        self.send_error(400)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                def log_message(self, *_args):
                    pass
            with ThreadingHTTPServer(("127.0.0.1", options.port), Handler) as server:
                server.serve_forever()
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
