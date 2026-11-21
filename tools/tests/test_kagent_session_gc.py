#!/usr/bin/env python3
"""Unit tests for kagent Session GC (a2a_client + e2e watchdog). Stdlib only.

Run: python3 -m unittest discover -s tools/tests -p 'test_kagent_session_gc.py'
"""
import importlib.util
import os
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


a2a = _load("a2a_client", "kubernetes/apps/ai/local-cron/app/resources/a2a_client.py")
wd = _load("kagent_e2e_watchdog",
           "kubernetes/apps/ai/kagent-e2e-watchdog/app/resources/kagent_e2e_watchdog.py")


def invoke_reply(state="TASK_STATE_COMPLETED", sid="sess-1", text="DONE"):
    return {"result": {"structuredContent": {"state": state, "session_id": sid, "text": text},
                       "content": [{"type": "text", "text": text}]}}


class A2AClientGC(unittest.TestCase):
    def setUp(self):
        self.calls = []
        os.environ.pop("A2A_KEEP_FAILED_SESSIONS", None)

    def fake_post(self, replies):
        def _p(url, payload, timeout):
            self.calls.append(payload["params"]["name"] if payload.get("method") == "tools/call" else "?")
            self.last = payload
            r = replies.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        return _p

    def test_completed_session_deleted_result_unchanged(self):
        with mock.patch.object(a2a, "_post", self.fake_post([invoke_reply(), {"result": {}}])):
            text, state = a2a.call_agent("http://x/mcp", "ml-operator", "hi")
        self.assertEqual((text, state), ("DONE", "completed"))
        self.assertEqual(self.calls, ["invoke_agent", "delete_session"])
        self.assertEqual(self.last["params"]["arguments"], {"session_id": "sess-1"})

    def test_failed_session_kept_by_default(self):
        with mock.patch.object(a2a, "_post", self.fake_post([invoke_reply("TASK_STATE_FAILED")])):
            text, state = a2a.call_agent("http://x/mcp", "ml-operator", "hi")
        self.assertEqual(state, "failed")
        self.assertEqual(self.calls, ["invoke_agent"])

    def test_failed_session_deleted_when_opted_in(self):
        os.environ["A2A_KEEP_FAILED_SESSIONS"] = "0"
        with mock.patch.object(a2a, "_post", self.fake_post([invoke_reply("TASK_STATE_FAILED"), {"result": {}}])):
            a2a.call_agent("http://x/mcp", "ml-operator", "hi")
        self.assertEqual(self.calls, ["invoke_agent", "delete_session"])

    def test_delete_error_never_fails_the_run(self):
        replies = [invoke_reply()] + [RuntimeError("boom")] * 3
        with mock.patch.object(a2a, "_post", self.fake_post(replies)), \
                mock.patch.object(a2a.time, "sleep"):
            text, state = a2a.call_agent("http://x/mcp", "ml-operator", "hi")
        self.assertEqual((text, state), ("DONE", "completed"))
        self.assertEqual(self.calls.count("delete_session"), 3)

    def test_delete_isError_retries_then_succeeds(self):
        replies = [invoke_reply(), {"result": {"isError": True, "content": [{"type": "text", "text": "conflict"}]}},
                   {"result": {}}]
        with mock.patch.object(a2a, "_post", self.fake_post(replies)), mock.patch.object(a2a.time, "sleep"):
            a2a.call_agent("http://x/mcp", "ml-operator", "hi")
        self.assertEqual(self.calls.count("delete_session"), 2)

    def test_no_session_id_no_delete(self):
        r = invoke_reply()
        r["result"]["structuredContent"].pop("session_id")
        with mock.patch.object(a2a, "_post", self.fake_post([r])):
            a2a.call_agent("http://x/mcp", "ml-operator", "hi")
        self.assertEqual(self.calls, ["invoke_agent"])

    def test_transport_error_no_delete(self):
        with mock.patch.object(a2a, "_post", self.fake_post([OSError("down")])):
            text, state = a2a.call_agent("http://x/mcp", "ml-operator", "hi")
        self.assertEqual(text, "")
        self.assertTrue(state.startswith("transport-error"))
        self.assertEqual(self.calls, ["invoke_agent"])


class _Resp:
    def __init__(self, body):
        self.body = body
    def read(self):
        return self.body.encode()
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class WatchdogGC(unittest.TestCase):
    TASK = {"result": {"task": {"contextId": "ctx-9", "status": {"state": "TASK_STATE_COMPLETED"},
                                 "artifacts": [{"parts": [{"metadata": {"kagent.dev/a2a/part-type": "function_call"}}]}]}}}

    def test_deletes_context_id(self):
        with mock.patch.object(wd.urllib.request, "urlopen", return_value=_Resp('{"result":{}}')) as u:
            self.assertTrue(wd._delete_session(self.TASK))
        self.assertIn(b'"session_id": "ctx-9"', u.call_args[0][0].data)

    def test_error_reply_and_exception_never_raise(self):
        with mock.patch.object(wd.urllib.request, "urlopen", return_value=_Resp('{"result":{"isError":true}}')):
            self.assertFalse(wd._delete_session(self.TASK))
        with mock.patch.object(wd.urllib.request, "urlopen", side_effect=OSError("x")):
            self.assertFalse(wd._delete_session(self.TASK))

    def test_probe_verdict_unaffected_by_delete_failure(self):
        with mock.patch.object(wd, "_invoke", return_value=self.TASK), \
                mock.patch.object(wd.urllib.request, "urlopen", side_effect=OSError("x")):
            ok, label, _ = wd.run_cycle()
        self.assertTrue(ok)
        self.assertEqual(label, "TASK_STATE_COMPLETED")


if __name__ == "__main__":
    unittest.main()
