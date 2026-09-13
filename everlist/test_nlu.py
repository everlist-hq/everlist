"""C9/NLU tests: intent-router validation, fail-open behavior, and the new
listing card format. NO live API calls — deterministic, patch-based.
Run: python3 test_nlu.py
"""
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import chatlib
import nlu


class CardFormat(unittest.TestCase):
    """C9: the canonical listing card — fixed shape on every surface."""

    def _l(self, **over):
        base = {"id": "even-2", "title": "Rooftop Jazz Night", "category": "concert",
                "location": "Berlin rooftop", "date": "2026-10-03", "price": 15,
                "capacity": 30, "registered": 12, "vertical": "events",
                "description": "Live jazz on the rooftop - quartet, sunset, wine.",
                "url": "https://rooftopjazz.example",
                "payment_terms": {"rail": "escrow", "refund_window_hours": 72}}
        base.update(over)
        return base

    def test_card_fixed_order(self):
        c = chatlib._fmt_listing(self._l())
        self.assertTrue(c.startswith("🎫 Rooftop Jazz Night · even-2"))
        self.assertIn("concert · Berlin rooftop · Sat 2026-10-03", c)
        self.assertIn("💶 15.00 USD · 🎟 18 of 30 spots open", c)
        self.assertIn("🛡 escrow · refund window 72h", c)
        self.assertIn("📝 Live jazz on the rooftop", c)
        self.assertIn("🔗 https://rooftopjazz.example", c)

    def test_card_variants(self):
        self.assertIn("sold out (20 of 20 booked)",
                      chatlib._fmt_listing(self._l(capacity=20, registered=20)))
        self.assertIn("💶 free", chatlib._fmt_listing(self._l(price=0)))
        self.assertIn("⚡ instant rail",
                      chatlib._fmt_listing(self._l(payment_terms={"rail": "instant"})))
        self.assertIn("verified buyers only",
                      chatlib._fmt_listing(self._l(require_verified_buyer=True)))

    def test_card_optional_fields(self):
        c = chatlib._fmt_listing(self._l(capacity=None))
        self.assertNotIn("🎟", c)
        c = chatlib._fmt_listing(self._l(date=None))
        self.assertIn("concert · Berlin rooftop", c)
        self.assertNotIn("· ·", c)
        self.assertNotIn("None", c)

    def test_card_bad_data(self):
        c = chatlib._fmt_listing({"id": "x", "price": "abc"})
        self.assertIn("x", c)


class NLUValidation(unittest.TestCase):
    """build_cmd must only emit safe canonical search commands."""

    def test_full_command(self):
        cmd = nlu.build_cmd({"q": "jazz", "free": False, "max_price": 20,
                             "min_price": None, "from": None, "to": "2026-10-31",
                             "sort": "price"})
        self.assertEqual(cmd, "search jazz under 20 until 2026-10-31 cheapest")

    def test_free(self):
        cmd = nlu.build_cmd({"q": "yoga", "free": True, "max_price": None,
                             "min_price": None, "from": None, "to": None,
                             "sort": None})
        self.assertEqual(cmd, "search yoga free")

    def test_not_a_search(self):
        self.assertIsNone(nlu.build_cmd({}))
        self.assertIsNone(nlu.build_cmd({"q": None}))
        self.assertIsNone(nlu.build_cmd({"q": "   "}))

    def test_q_sanitized(self):
        # pipes/slashes/punct would be injection or markup vectors -> stripped
        self.assertEqual(nlu.build_cmd({"q": "a/b"}), "search a b")
        self.assertEqual(nlu.build_cmd({"q": "x" * 100}), "search " + "x" * 40)
        self.assertIsNone(nlu.build_cmd({"q": "??"}))

    def test_number_and_date_gates(self):
        self.assertIsNone(nlu._valid_num(True))
        self.assertIsNone(nlu._valid_num("20"))
        self.assertIsNone(nlu._valid_num(-5))
        self.assertEqual(nlu._valid_num(20), 20.0)
        self.assertIsNone(nlu._valid_date("2026-13-40"))
        self.assertIsNone(nlu._valid_date("tomorrow"))
        self.assertEqual(nlu._valid_date("2026-10-31"), "2026-10-31")
        # invalid max_price type is treated as absent; q still carries the search
        self.assertEqual(nlu.build_cmd({"q": "jazz", "max_price": "20"}), "search jazz")

    def test_rate_limit(self):
        nlu._RL.clear()
        self.assertTrue(nlu._rate_ok("s1"))
        nlu._RL["s1"][1] = nlu._RL_CAP
        self.assertFalse(nlu._rate_ok("s1"))
        nlu._RL.clear()

    def test_failopen_no_key(self):
        with mock.patch.object(nlu, "_API_KEY", ""):
            self.assertIsNone(nlu.translate("jazz tonight in berlin"))

    def test_failopen_api_error(self):
        with mock.patch.object(nlu, "_API_KEY", "k"), mock.patch(
                "urllib.request.urlopen", side_effect=OSError("boom")):
            self.assertIsNone(nlu.translate("jazz tonight in berlin", sender="s2"))

    def test_json_extraction(self):
        self.assertEqual(nlu._extract_json('```json\n{"q": "jazz"}\n```'), {"q": "jazz"})
        self.assertIsNone(nlu._extract_json("no json here"))

    def test_translate_end_to_end_mock(self):
        payload = json.dumps({"q": "jazz", "free": False, "max_price": None,
                              "min_price": None, "from": None, "to": None,
                              "sort": None})
        resp = json.dumps({"choices": [{"message": {"content": payload}}]}).encode()
        with mock.patch.object(nlu, "_API_KEY", "k"), mock.patch(
                "urllib.request.urlopen", mock.mock_open(read_data=resp)):
            self.assertEqual(nlu.translate("any jazz tonight"), "search jazz")


if __name__ == "__main__":
    unittest.main(verbosity=2)
