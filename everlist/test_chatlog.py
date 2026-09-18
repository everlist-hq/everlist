"""Chat turn log tests (owner call 2026-09-18). Hermetic: no live LLM, temp files.
Run: python3 test_chatlog.py
"""
import importlib
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["EVERLIST_BRAIN_DISABLED"] = "1"  # hermetic: no live mercury

import chatlog
import chatlib


class Redaction(unittest.TestCase):
    """Secrets must NEVER reach the log file (repo law: no secrets on disk)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="chatlog-test-")
        self.path = os.path.join(self.dir, "chatlog.jsonl")
        self._orig = chatlog._LOG_PATH
        chatlog._LOG_PATH = self.path

    def tearDown(self):
        chatlog._LOG_PATH = self._orig

    def _last(self):
        with open(self.path, encoding="utf-8") as f:
            return json.loads(f.read().strip().split("\n")[-1])

    def test_seed_redacted(self):
        seed = "ab" * 32
        chatlog.log_turn("web-x", "login-seed " + seed, "logged in", 5.0)
        rec = self._last()
        self.assertNotIn(seed, rec["text"])
        self.assertIn("[seed-redacted]", rec["text"])

    def test_seed_redacted_in_reply(self):
        seed = "cd" * 32
        chatlog.log_turn("web-x", "signup", "🔑 SEED: " + seed, 5.0)
        rec = self._last()
        self.assertNotIn(seed, rec["reply"])
        self.assertIn("[seed-redacted]", rec["reply"])

    def test_acct_code_redacted(self):
        chatlog.log_turn("web-x", "login acct-0123abcd", "welcome", 5.0)
        rec = self._last()
        self.assertNotIn("acct-0123abcd", rec["text"])
        self.assertIn("[acct-redacted]", rec["text"])

    def test_pvt_claim_redacted(self):
        claim = "pvt-" + "0123456789abcdef"
        chatlog.log_turn("web-x", "book even-2 " + claim, "ok", 5.0)
        rec = self._last()
        self.assertNotIn(claim, rec["text"])
        self.assertIn("[claim-redacted]", rec["text"])

    def test_sender_never_raw(self):
        chatlog.log_turn("web-secret-session-id-777", "hi", "hello", 5.0)
        raw = open(self.path, encoding="utf-8").read()
        self.assertNotIn("secret-session-id-777", raw)
        self.assertEqual(len(self._last()["sender"]), 12)


class Mechanics(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="chatlog-test-")
        self.path = os.path.join(self.dir, "chatlog.jsonl")
        self._orig = chatlog._LOG_PATH
        chatlog._LOG_PATH = self.path

    def tearDown(self):
        chatlog._LOG_PATH = self._orig

    def test_valid_jsonl_shape(self):
        chatlog.log_turn("web-abc", "search jazz", "3 found", 42.5)
        rec = json.loads(open(self.path, encoding="utf-8").read().strip())
        self.assertEqual(rec["surface"], "web")
        self.assertEqual(rec["text"], "search jazz")
        self.assertEqual(rec["reply"], "3 found")
        self.assertEqual(rec["ms"], 42.5)
        self.assertTrue(rec["ok"])
        self.assertIn("ts", rec)

    def test_surface_classification(self):
        self.assertEqual(chatlog.surface("web-123"), "web")
        self.assertEqual(chatlog.surface("agent1qxyz"), "agent")
        self.assertEqual(chatlog.surface("cli"), "other")

    def test_error_turn(self):
        chatlog.log_turn("web-abc", "boom", "", 1.0, ok=False, error=ValueError("x"))
        rec = json.loads(open(self.path, encoding="utf-8").read().strip())
        self.assertFalse(rec["ok"])
        self.assertIn("ValueError", rec["error"])

    def test_off_switch(self):
        os.environ["EVERLIST_CHATLOG"] = "off"
        try:
            chatlog.log_turn("web-abc", "hi", "ho", 1.0)
            self.assertFalse(os.path.exists(self.path))
        finally:
            del os.environ["EVERLIST_CHATLOG"]

    def test_brain_info_attached(self):
        with mock.patch.object(chatlog, "_brain_info",
                               return_value={"provider": "fallback", "action": "search", "ms": 900.0}):
            chatlog.log_turn("web-abc", "jazz", "found", 1000.0)
        rec = json.loads(open(self.path, encoding="utf-8").read().strip())
        self.assertEqual(rec["brain"]["provider"], "fallback")


class DepthGuard(unittest.TestCase):
    """Internal re-entries must log exactly ONE line per top-level turn."""

    def setUp(self):
        chatlib._LAST_RESULTS.clear()
        chatlib._LAST_SEARCH.clear()
        self.dir = tempfile.mkdtemp(prefix="chatlog-test-")
        self.path = os.path.join(self.dir, "chatlog.jsonl")
        self._orig = chatlog._LOG_PATH
        chatlog._LOG_PATH = self.path
        chatlog._tls = chatlog.threading.local()  # fresh nesting state

    def tearDown(self):
        chatlog._LOG_PATH = self._orig

    def _lines(self):
        if not os.path.exists(self.path):
            return []
        return [json.loads(l) for l in open(self.path, encoding="utf-8").read().strip().split("\n") if l]

    def test_simple_turn_one_line(self):
        with mock.patch.object(chatlib, "_hub_get",
                               return_value={"listings": [{"id": "even-1", "title": "Jazz",
                                                            "price": 10, "vertical": "events"}]}):
            chatlib.handle_text("http://hub", "search jazz", sender="web-t1")
        lines = self._lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["text"], "search jazz")

    def test_failopen_reentry_one_line(self):
        # conversational text, brain disabled -> fail-open path; still one line
        r = chatlib.handle_text("http://hub", "find me something fun this weekend", sender="web-t2")
        lines = self._lines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0]["reply"])

    def test_brain_book_reentry_one_line(self):
        # natural-language book re-enters handle_text via brain_book -> one line
        chatlib._LAST_RESULTS["web-t3"] = [
            {"id": "even-1", "title": "Free Jazz", "price": 0,
             "payment_terms": {"rail": "escrow", "refund_window_hours": 72}}]
        with mock.patch.object(chatlib, "_hub_get", side_effect=Exception("no hub")):
            chatlib.handle_text("http://hub", "book the first one for Alex", sender="web-t3")
        lines = self._lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["text"], "book the first one for Alex")


if __name__ == "__main__":
    unittest.main(verbosity=2)
