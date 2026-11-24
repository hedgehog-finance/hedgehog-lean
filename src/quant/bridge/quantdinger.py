"""QuantDinger Backtest Center asynchronous jobs. No trading API access."""
import json
from pathlib import Path
import signal
import time
from urllib.parse import urlsplit, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ValueError("Upstream redirects are not permitted")


def run(data, upstream, token_file, emit):
    url = urlsplit(upstream)
    if url.username or url.password or url.query or url.fragment or url.scheme not in ("http", "https"):
        raise ValueError("Invalid upstream URL")
    if url.scheme == "http" and url.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Remote upstream requires HTTPS")
    token = Path(token_file).read_text().strip()
    base = upstream.rstrip("/")
    client = build_opener(NoRedirect())
    def request(path, payload=None):
        req = Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with client.open(req, timeout=25) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("Upstream result too large")
        envelope = json.loads(raw)
        if envelope.get("code") != 1 or not isinstance(envelope.get("data"), dict):
            raise ValueError("Unsupported Backtest Center response or upstream error")
        return envelope["data"]
    allowed = {"code", "startDate", "endDate", "initialCapital", "commission", "slippage", "params"}
    if set(data) - allowed or not all(key in data for key in ("code", "startDate", "endDate", "initialCapital", "commission", "slippage")):
        raise ValueError("Explicit Backtest Center v2 code, dates, capital and costs are required")
    receipt = request("/jobs", {**data, "leverageEnabled": False, "persist": True})
    job_id = receipt["jobId"]
    Path("upstream-job.json").write_text(json.dumps({"jobId": job_id}))
    emit("metric", {"upstreamJobId": job_id})
    path = "/jobs/" + quote(job_id, safe="")
    def stop(*_args):
        request(path + "/cancel", {})
        for _ in range(10):
            if request(path)["status"] == "cancelled":
                emit("status",{"upstreamCancelled":True,"jobId":job_id})
                break
            time.sleep(1)
        else:
            raise ValueError("Upstream cancellation remains unconfirmed")
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    while True:
        state = request(path)
        status = state["status"]
        if status in ("completed", "succeeded"):
            return {"jobId": job_id, "result": state["result"]}
        if status in ("failed", "cancelled"):
            raise ValueError("Upstream job " + status)
        time.sleep(1)
