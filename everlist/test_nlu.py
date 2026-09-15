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
        self.assertTrue(c.startswith("╔═፨"))
        self.assertIn(" " + chatlib._mb("Rooftop Jazz Night"), c)
        self.assertNotIn("even-2", c)  # C9f: id removed, number is the handle
        self.assertIn("⌂ Berlin rooftop · ◷ " + chatlib._mb("Sat 2026-10-03") + " · $15", c)
        self.assertIn("♟ 18 left · ✪ escrow · refund window 72h", c)
        self.assertIn("✪ escrow · refund window 72h", c)
        self.assertIn("» Live jazz on the rooftop", c)
        self.assertIn("⇗ https://rooftopjazz.example", c)

    def test_card_variants(self):
        self.assertIn("♟ 0 left",
                      chatlib._fmt_listing(self._l(capacity=20, registered=20)))
        self.assertIn("$0", chatlib._fmt_listing(self._l(price=0)))
        self.assertIn("⇢ instant rail",
                      chatlib._fmt_listing(self._l(payment_terms={"rail": "instant"})))
        self.assertIn("verified buyers only",
                      chatlib._fmt_listing(self._l(require_verified_buyer=True)))

    def test_card_optional_fields(self):
        c = chatlib._fmt_listing(self._l(capacity=None))
        self.assertNotIn("♟", c)
        c = chatlib._fmt_listing(self._l(date=None))
        self.assertIn("⌂ Berlin rooftop", c)
        self.assertNotIn("· ·", c)
        self.assertNotIn("None", c)

    def test_card_bad_data(self):
        c = chatlib._fmt_listing({"id": "x", "price": "abc"})
        self.assertIn("$abc", c)  # C9f: id no longer shown; bad price must still render safely


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

    def test_translate_affirmed_offtopic_is_empty_string(self):
        # router ANSWERS that this is not a search -> sentinel '', not None
        content = json.dumps({"q": None})
        resp = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        with mock.patch.object(nlu, "_API_KEY", "k"), mock.patch(
                "urllib.request.urlopen", mock.mock_open(read_data=resp)):
            self.assertEqual(nlu.translate("capital of france?", sender="s3"), "")

    def test_translate_empty_content_fails_open(self):
        # reasoning model may return empty content -> indeterminate -> None (fail-open)
        resp = json.dumps({"choices": [{"message": {"content": ""}}]}).encode()
        with mock.patch.object(nlu, "_API_KEY", "k"), mock.patch(
                "urllib.request.urlopen", mock.mock_open(read_data=resp)):
            self.assertIsNone(nlu.translate("jazz", sender="s4"))


class SearchPagination(unittest.TestCase):
    """C9c: long result lists -> one-line index + preview; '3' / '2-6' / 'all' replay."""

    def _ls(self, n):
        return [{"id": f"even-{i}", "title": f"Event {i}", "category": "concert",
                 "date": "2026-10-03", "price": i, "vertical": "events", "description": f"Description {i}"}
                for i in range(1, n + 1)]

    def _run(self, n, sender="s9"):
        with mock.patch.object(chatlib, "_hub_get", return_value={"listings": self._ls(n)}):
            return chatlib.handle_text("http://hub", "search concert", sender=sender)

    def setUp(self):
        chatlib._LAST_RESULTS.clear()

    def test_short_list_full_cards(self):
        r = self._run(4)
        self.assertIn("4 found", r)
        self.assertIn(chatlib._mb("Event 1"), r)

    def test_long_list_index_and_preview(self):
        r = self._run(10)
        self.assertIn("10 found", r)
        self.assertIn("10  " + chatlib._mb("Event 10"), r)          # index covers ALL results
        self.assertIn(chatlib._mb("Event 1"), r)      # first preview card shown
        self.assertIn("» Description 1", r)          # first preview card shown
        self.assertNotIn("» Description 6", r)      # card 6 NOT auto-flooded
        self.assertIn("To book one", r)

    def test_number_replay(self):
        self._run(10)
        r = chatlib.handle_text("http://hub", "3", sender="s9")
        self.assertIn(chatlib._mb("Event 3"), r)
        self.assertIn("» Description 3", r)
        self.assertNotIn("» Description 4", r)

    def test_range_and_all_replay(self):
        self._run(10)
        r = chatlib.handle_text("http://hub", "2-4", sender="s9")
        self.assertIn(chatlib._mb("Event 2"), r)
        self.assertIn(chatlib._mb("Event 4"), r)
        self.assertNotIn("» Description 5", r)
        r = chatlib.handle_text("http://hub", "all", sender="s9")
        self.assertIn(chatlib._mb("Event 10"), r)

    def test_out_of_bounds(self):
        self._run(3)
        r = chatlib.handle_text("http://hub", "7", sender="s9")
        self.assertIn("No result 7", r)

    def test_per_sender_isolation(self):
        self._run(10, sender="a1")
        self.assertIsNone(chatlib._LAST_RESULTS.get("b2"))
        r = chatlib.handle_text("http://hub", "2", sender="b2")
        self.assertIn("search first", r)


class BookByNumber(unittest.TestCase):
    # C9i: 'book <n>' resolves the Nth result of the sender's last search;
    # honest errors when the stash is empty or n is out of range. Ids are
    # never pure digits (SPEC 14), so digits are always positional handles.
    # A message carrying a pvt-claim keeps the P2 fall-through.

    def _ls(self, n):
        return [{"id": "even-%d" % i, "title": "Event %d" % i, "price": 10,
                 "payment_terms": {"rail": "escrow", "refund_window_hours": 72}}
                for i in range(1, n + 1)]

    def test_book_by_number_resolves(self):
        with mock.patch.object(chatlib, "_hub_get", return_value={"listings": self._ls(4)}):
            chatlib.handle_text("http://hub", "search concert", sender="bn1")
        with mock.patch.object(chatlib, "_hub_get", return_value={"listings": self._ls(4)}):
            r = chatlib.handle_text("http://hub", "book 2 Alex", sender="bn1")
        self.assertIn("Event 2", r)  # stash position 2 -> real id even-2

    def test_book_by_number_out_of_range(self):
        with mock.patch.object(chatlib, "_hub_get", return_value={"listings": self._ls(3)}):
            chatlib.handle_text("http://hub", "search concert", sender="bn2")
        r = chatlib.handle_text("http://hub", "book 5 Alex", sender="bn2")
        self.assertIn("No result 5", r)
        self.assertIn("found 3", r)

    def test_book_by_number_no_search(self):
        chatlib._LAST_RESULTS.pop("bn3", None)
        r = chatlib.handle_text("http://hub", "book 2 Alex", sender="bn3")
        self.assertIn("no last search here", r)
        self.assertIn("search", r)

    def test_book_number_with_claim_keeps_p2_path(self):
        chatlib._LAST_RESULTS.pop("bn4", None)
        with mock.patch.object(chatlib, "_hub_get", side_effect=Exception("no hub")), mock.patch.object(chatlib, "_hub_get_claim", side_effect=Exception("no hub")):
            r = chatlib.handle_text("http://hub", "book 2 pvt-abcdef0123456789 Alex", sender="bn4")
        self.assertIn("Bookings need two things", r)  # generic P2 wall, not the number error


if __name__ == "__main__":
    unittest.main(verbosity=2)
