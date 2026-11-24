"""One isolated worker per finite task or persistent simulation session."""
import contextlib
import json
from pathlib import Path
import signal
import sys
import traceback
import threading
import time
import os

PROTOCOL_OUT = sys.stdout
CURRENCY = None
QUANTITY_UNIT = None


def emit(kind, data, timestamp=None):
    # Preserve a decimal text wire format. Backtrader computes in binary64;
    # string conversion does not claim greater source precision.
    decimal_fields = {"price", "quantity", "averagePrice", "fee", "cash", "equity"}
    if kind in ("order", "fill", "position", "asset"):
        data = {key: str(value) if key in decimal_fields else value for key,value in data.items()}
        data.setdefault("currency", CURRENCY)
        data.setdefault("quantityUnit", QUANTITY_UNIT)
    print(json.dumps({"event": kind, "data": data, **({"timestamp": timestamp} if timestamp else {})}, allow_nan=False), file=PROTOCOL_OUT, flush=True)


def main():
    global CURRENCY, QUANTITY_UNIT
    request = json.loads(Path(sys.argv[1]).read_text())
    if request.get("parentPid"):
        def watch_parent():
            while os.getppid() == request["parentPid"]:
                time.sleep(1)
            os.kill(os.getpid(),signal.SIGTERM)
        threading.Thread(target=watch_parent,daemon=True).start()
    provider, operation, data = request["provider"], request["operation"], request["input"]
    semantics=json.loads(data.get("dataset",{}).get("adjustment_json","{}"))
    CURRENCY=semantics.get("currency",data.get("currency"))
    QUANTITY_UNIT=semantics.get("volumeUnit","contracts" if provider=="vnpy" else None)
    with contextlib.redirect_stdout(sys.stderr):
        import providers
        if provider == "backtrader" and operation == "backtest":
            result = providers.backtrader(data, emit)
        elif provider == "lean" and operation in ("backtest", "simulate"):
            import lean
            result = lean.run(data, request["leanRoot"], operation == "simulate", emit)
        elif provider == "qlib" and operation == "research":
            result = providers.qlib(data, emit)
        elif provider == "vnpy" and operation == "cta-backtest":
            result = providers.vnpy(data, emit)
        elif provider == "timesfm" and operation == "forecast":
            result = providers.forecast(data, request["model"])
        elif provider == "czsc" and operation == "analyze":
            result = providers.czsc(data)
        elif provider == "quantdinger" and operation == "backtest":
            import quantdinger
            result = quantdinger.run(data, request["upstream"], request["upstreamTokenFile"], emit)
        else:
            raise ValueError("Unsupported provider operation")
    print(json.dumps({"result": result}, allow_nan=False), file=PROTOCOL_OUT, flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
