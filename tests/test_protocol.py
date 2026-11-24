"""Protocol regressions using temporary journals and no installed provider."""
from pathlib import Path
import os
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/quant/bridge"))
from bridge import Bridge, stop_worker
from worker import watch_parent


class ProtocolTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = Bridge(SimpleNamespace(provider="lean", state_dir=self.temp.name, lean_root=None,
                                             model=None, upstream=None, upstream_token_file=None, stdio=True))

    def tearDown(self):
        self.bridge.close()
        self.bridge.db.close()
        self.bridge.lock_file.close()
        self.temp.cleanup()

    def test_cancel_tombstone_prevents_delayed_submit_even_after_restart(self):
        identity = {"runId": "late", "generation": "one", "fingerprint": "original"}
        receipt = self.bridge.call("cancel", identity)
        self.assertTrue(receipt["cancellationRecorded"])
        self.bridge.db.close()
        self.bridge.lock_file.close()
        self.bridge = Bridge(self.bridge.options)
        with patch.object(self.bridge, "run") as launch:
            self.assertEqual(self.bridge.call("submit", identity)["status"], "cancelled")
            launch.assert_not_called()
        with self.assertRaises(ValueError):
            self.bridge.call("submit", {**identity, "fingerprint": "different"})

    def test_worker_preserves_external_home_and_enforces_utf8(self):
        for provider in ("lean", "vnpy"):
            self.bridge.options.provider = provider
            with patch("bridge.subprocess.Popen", return_value=MagicMock()) as spawn, patch("bridge.threading.Thread"):
                self.bridge.run(provider, "test", {})
                env = spawn.call_args.kwargs["env"]
                self.assertEqual(env["PYTHONUTF8"], "1")
                self.assertEqual(env.get("HOME"), str(Path(self.temp.name).resolve()/provider) if provider=="lean" else os.environ.get("HOME"))
            self.bridge.children.clear()

    def test_windows_cancellation_targets_the_owned_process_tree(self):
        child = MagicMock(pid=12345)
        child.poll.return_value = None
        with patch("bridge.sys.platform", "win32"), patch("bridge.subprocess.run", return_value=SimpleNamespace(returncode=0)) as run:
            stop_worker(child)
            self.assertEqual(run.call_args.args[0][1:], ["/PID", "12345", "/T", "/F"])
            child.terminate.assert_not_called()
        child.poll.return_value = 0
        with patch("bridge.subprocess.run") as run:
            stop_worker(child)
            run.assert_not_called()

    def test_windows_parent_exit_uses_a_handle_and_stops_only_its_own_tree(self):
        api = SimpleNamespace(SYNCHRONIZE=0x100000, INFINITE=0xFFFFFFFF,
                              OpenProcess=MagicMock(return_value=41), WaitForSingleObject=MagicMock(return_value=0), CloseHandle=MagicMock())
        for missing in (False, True):
            api.OpenProcess.side_effect = OSError("owner exited") if missing else None
            api.WaitForSingleObject.reset_mock()
            api.CloseHandle.reset_mock()
            with patch.dict(sys.modules, {"_winapi": api}), patch("worker.sys.platform", "win32"), patch("worker.os.getpid", return_value=789), patch("worker.os.getppid") as getppid, patch("worker.subprocess.run") as run, patch("worker.os._exit", side_effect=RuntimeError("worker-exit")):
                with self.assertRaisesRegex(RuntimeError, "worker-exit"):
                    watch_parent(123)
                api.OpenProcess.assert_called_with(api.SYNCHRONIZE, False, 123)
                if missing:
                    api.WaitForSingleObject.assert_not_called()
                else:
                    api.WaitForSingleObject.assert_called_once_with(41, api.INFINITE)
                    api.CloseHandle.assert_called_once_with(41)
                getppid.assert_not_called()
                self.assertEqual(run.call_args.args[0][1:], ["/PID", "789", "/T", "/F"])

    def test_posix_parent_exit_preserves_worker_cleanup_signal(self):
        with patch("worker.sys.platform", "darwin"), patch("worker.os.getppid", side_effect=[123, 1]), patch("worker.time.sleep"), patch("worker.os.kill") as kill:
            watch_parent(123)
            kill.assert_called_once_with(os.getpid(), __import__("signal").SIGTERM)


if __name__ == "__main__":
    unittest.main()
