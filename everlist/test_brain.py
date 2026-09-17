"""Brain v2 tests: action protocol, screens, sanitizers, fail-open, executors.
NO live API calls — deterministic, patch-based (same law as the old test_nlu).
Run: python3 test_brain.py
"""
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import brain
import chatlib

os.environ["EVERLIST_BRAIN_DISABLED"] = "1"  # hermetic: no live LLM in this suite


class Screen(unittest.TestCase):
    """Deterministic walls: pure chit-chat/math/standalone-weather never reach
    the LLM; EverList-shaped text and identity always pass through."""

    def test_chitchat_walled(self):
        for m in ("tell me a joke", "capital of france", "how are you",
                  "what time is it", "write me a poem"):
            self.assertIsNotNone(brain._screen(m), m)

    def test_math_walled(self):
        self.assertIsNotNone(brain._screen("2+2"))
        self.assertIsNotNone(brain._screen("what is 5*7"))

    def test_standalone_weather_walled(self):
        self.assertIsNotNone(brain._screen("whats the weather tomorrow"))
        self.assertIsNotNone(brain._screen("will it rain"))

    def test_everlist_weather_passes(self):
        # weather AT a listing/event is in scope (owner call 2026-09-16)
        self.assertIsNone(brain._screen("weather at the jazz night tomorrow"))
        self.assertIsNone(brain._screen("will it rain at the rooftop concert"))

    def test_identity_passes(self):
        for m in ("who are you", "what is everlist", "what's everlist"):
            self.assertIsNone(brain._screen(m), m)

    def test_long_texts_pass(self):
        self.assertIsNone(brain._screen("jazz " * 40))


class Sanitize(unittest.TestCase):

    def test_urls_and_markdown_stripped(self):
        s = brain._sanitize_say("check https://evil.example/x **bold** [link](x)")
        self.assertNotIn("http", s)
        self.assertNotIn("*", s)
        self.assertNotIn("[", s)

    def test_blank_is_none(self):
        self.assertIsNone(brain._sanitize_say(""))
        self.assertIsNone(brain._sanitize_say("   "))

    def test_caps_length(self):
        self.assertEqual(len(brain._sanitize_say("x" * 900)), 400)


class ExtractJSON(unittest.TestCase):

    def test_fenced_bare_embedded(self):
        self.assertEqual(brain._extract_json('```json\n{"action":"nav"}\n```'), {"action": "nav"})
        self.assertEqual(brain._extract_json('{"action":"nav"}'), {"action": "nav"})
        self.assertEqual(brain._extract_json('junk {"a":1} junk'), {"a": 1})
        self.assertIsNone(brain._extract_json("no json here"))
        self.assertIsNone(brain._extract_json('[1,2,3]'))  # arrays are not actions


class Validators(unittest.TestCase):

    def test_valid_q(self):
        self.assertEqual(brain._valid_q("Jazz Night!!"), "jazz night")
        self.assertIsNone(brain._valid_q(""))
        self.assertIsNone(brain._valid_q("???"))
        self.assertIsNone(brain._valid_q("x" * 100))

    def test_valid_num(self):
        self.assertIsNone(brain._valid_num(True))
        self.assertEqual(brain._valid_num("20"), 20.0)  # numeric strings coerce safely
        self.assertIsNone(brain._valid_num(-5))
        self.assertIsNone(brain._valid_num(1_000_001))
        self.assertEqual(brain._valid_num(20), 20.0)

    def test_valid_date(self):
        self.assertEqual(brain._valid_date("2026-10-31"), "2026-10-31")
        self.assertIsNone(brain._valid_date("2026-13-40"))
        self.assertIsNone(brain._valid_date("tomorrow"))

    def test_filters_of(self):
        f = brain._filters_of({"filters": {"free": True, "max_price": 20,
                                            "from": "2026-10-31", "sort": "price"}})
        self.assertEqual(f, {"free": True, "max_price": 20.0,
                            "from": "2026-10-31", "sort": "price"})
        self.assertEqual(brain._filters_of({"filters": {"sort": "bogus"}}), {})


class RateLimit(unittest.TestCase):

    def test_cap(self):
        brain._RL.clear()
        self.assertTrue(brain._rate_ok("s1"))
        hits, win = brain._RL["s1"]
        brain._RL["s1"] = ([1.0] * brain._RL_CAP, win)  # tuples are immutable — replace whole
        self.assertFalse(brain._rate_ok("s1"))
        brain._RL.clear()


class FailOpen(unittest.TestCase):
    """Any LLM failure -> None -> chatlib keeps deterministic behavior."""

    def test_no_key(self):
        with mock.patch.object(brain, "_API_KEY", ""), mock.patch.object(brain, "_FB_KEY", ""):
            self.assertIsNone(brain.respond("http://hub", "jazz berlin", "s", chatlib))

    def test_api_error(self):
        with mock.patch.object(brain, "_DISABLED", False), mock.patch.object(
                brain, "_API_KEY", "k"), mock.patch(
                "urllib.request.urlopen", side_effect=OSError("boom")):
            self.assertIsNone(brain._call("jazz", []))

    def test_empty_content_fails_open(self):
        resp = json.dumps({"choices": [{"message": {"content": ""}}]}).encode()
        with mock.patch.object(brain, "_DISABLED", False), mock.patch.object(
                brain, "_API_KEY", "k"), mock.patch(
                "urllib.request.urlopen", mock.mock_open(read_data=resp)):
            self.assertIsNone(brain._call("jazz", []))

    def test_disabled_env(self):
        with mock.patch.object(brain, "_DISABLED", True):
            self.assertIsNone(brain.respond("http://hub", "anything at all here", "s", chatlib))


class RespondDispatch(unittest.TestCase):
    """respond() routes each action to the right executor with sanitized say."""

    def setUp(self):
        brain._RL.clear()
        brain._CTX.clear()

    def _act(self, action, **over):
        d = {"action": action}
        d.update(over)
        return d

    def test_off_topic_is_template(self):
        with mock.patch.object(brain, "_DISABLED", False), mock.patch.object(
                brain, "_call", return_value=self._act("off_topic", say="whatever")):
            r = brain.respond("http://hub", "capital of france?", "s1", chatlib)
            self.assertIn("only do EverList", r)  # template law: declines never vary

    def test_ack_uses_say(self):
        with mock.patch.object(brain, "_DISABLED", False), mock.patch.object(
                brain, "_call", return_value=self._act("ack", say="Anytime!")):
            r = brain.respond("http://hub", "thanks!", "s2", chatlib)
            self.assertEqual(r, "Anytime!")

    def test_greeting_and_identity_templates(self):
        self.assertIn("Commands:", brain.respond("http://hub", "hello", "s3", chatlib))
        self.assertIn("EverList assistant", brain.respond("http://hub", "who are you", "s4", chatlib))

    def test_nav_home_clears_state(self):
        chatlib._LAST_RESULTS["s5"] = [{"id": "x"}]
        chatlib._LAST_SEARCH["s5"] = {"q": "jazz", "filters": {}}
        with mock.patch.object(brain, "_DISABLED", False), mock.patch.object(
                brain, "_call", return_value=self._act("nav", target="home")):
            r = brain.respond("http://hub", "go back to main page", "s5", chatlib)
        self.assertIn("[[nav:home]]", r)
        self.assertNotIn("s5", chatlib._LAST_RESULTS)
        self.assertNotIn("s5", chatlib._LAST_SEARCH)

    def test_search_routes_to_executor(self):
        with mock.patch.object(brain, "_DISABLED", False), mock.patch.object(
                brain, "_call", return_value=self._act("search", q="jazz")), mock.patch.object(
                chatlib, "brain_search", return_value="SEARCH-OUT") as bs:
            r = brain.respond("http://hub", "jazz tonight", "s6", chatlib)
        self.assertEqual(r, "SEARCH-OUT")
        bs.assert_called_once()


class ResolveListing(unittest.TestCase):
    """The reference resolver: numbers, ordinals, titles, single-result."""

    def _stash(self, n=3):
        chatlib._LAST_RESULTS["r1"] = [
            {"id": "even-%d" % i, "title": t} for i, t in enumerate(
                ["Rooftop Jazz Night", "Margherita Pizza", "Morning Yoga"], 1)][:n]

    def setUp(self):
        chatlib._LAST_RESULTS.clear()

    def test_number(self):
        self._stash()
        i, l = chatlib._resolve_listing("r1", "2")
        self.assertEqual(l["title"], "Margherita Pizza")

    def test_ordinal(self):
        self._stash()
        i, l = chatlib._resolve_listing("r1", "the second one")
        self.assertEqual(l["title"], "Margherita Pizza")

    def test_title_overlap(self):
        self._stash()
        i, l = chatlib._resolve_listing("r1", "the jazz night")
        self.assertEqual(l["title"], "Rooftop Jazz Night")

    def test_single_result_any_text(self):
        self._stash(1)
        i, l = chatlib._resolve_listing("r1", "that one")
        self.assertEqual(l["title"], "Rooftop Jazz Night")

    def test_empty_stash_none(self):
        self.assertIsNone(chatlib._resolve_listing("rX", "the first one"))


class BrainSearch(unittest.TestCase):
    """brain_search builds canonical commands; refine merges onto last spec."""

    def setUp(self):
        chatlib._LAST_RESULTS.clear()
        chatlib._LAST_SEARCH.clear()

    def test_plain_search(self):
        with mock.patch.object(chatlib, "_smart_search", return_value="OUT") as ss:
            r = chatlib.brain_search("http://hub", "b1", {"action": "search", "q": "jazz"})
        self.assertEqual(r, "OUT")
        ss.assert_called_once_with("http://hub", "search jazz", "b1")

    def test_search_with_filters(self):
        with mock.patch.object(chatlib, "_smart_search", return_value="OUT") as ss:
            chatlib.brain_search("http://hub", "b2", {
                "action": "search", "q": "yoga",
                "filters": {"free": True, "from": "2026-09-19", "sort": "date"}})
        cmd = ss.call_args[0][1]
        self.assertIn("yoga", cmd)
        self.assertIn("free", cmd)
        self.assertIn("from 2026-09-19", cmd)
        self.assertIn("soonest", cmd)

    def test_refine_merges_last_spec(self):
        chatlib._LAST_SEARCH["b3"] = {"q": "yoga", "filters": {"from": "2026-09-19"}}
        with mock.patch.object(chatlib, "_smart_search", return_value="OUT") as ss:
            chatlib.brain_search("http://hub", "b3", {
                "action": "refine", "refine": True, "filters": {"free": True}})
        cmd = ss.call_args[0][1]
        self.assertIn("yoga", cmd)              # q inherited
        self.assertIn("from 2026-09-19", cmd)  # old filter kept
        self.assertIn("free", cmd)              # new filter applied

    def test_bare_cheaper_resorts(self):
        chatlib._LAST_SEARCH["b4"] = {"q": "jazz", "filters": {}}
        with mock.patch.object(chatlib, "_smart_search", return_value="OUT") as ss:
            chatlib.brain_search("http://hub", "b4", {"action": "refine", "refine": True})
        self.assertIn("cheapest", ss.call_args[0][1])  # honest re-sort, no invented budget


class BrainBookShow(unittest.TestCase):

    def setUp(self):
        chatlib._LAST_RESULTS.clear()
        chatlib._LAST_SEARCH.clear()

    def test_book_resolves_and_delegates(self):
        chatlib._LAST_RESULTS["bk1"] = [
            {"id": "even-1", "title": "Free Jazz", "price": 0,
             "payment_terms": {"rail": "escrow", "refund_window_hours": 72}}]
        with mock.patch.object(chatlib, "handle_text", return_value="BOOKED") as ht:
            chatlib.brain_book("http://hub", "bk1", "the first one", "Alex")
        ht.assert_called_once_with("http://hub", "book 1 Alex", sender="bk1")

    def test_book_no_stash_guides(self):
        r = chatlib.brain_book("http://hub", "bk2", "the first one", "")
        self.assertIn("Run a search first", r)

    def test_book_ambiguous_asks(self):
        chatlib._LAST_RESULTS["bk3"] = [{"id": "a", "title": "X"}, {"id": "b", "title": "Y"}]
        r = chatlib.brain_book("http://hub", "bk3", "hmm", "")
        self.assertIn("Which one", r)

    def test_show_resolves(self):
        chatlib._LAST_RESULTS["sh1"] = [{"id": "even-9", "title": "Yoga"}]
        with mock.patch.object(chatlib, "_show_listing", return_value="CARD") as sl:
            r = chatlib.brain_show("http://hub", "sh1", "the yoga one")
        self.assertEqual(r, "CARD")
        sl.assert_called_once_with("http://hub", "even-9")


class WeatherGrounding(unittest.TestCase):
    """Weather only for stashed listings with date+location; real forecast."""

    def setUp(self):
        chatlib._LAST_RESULTS.clear()

    def test_no_stash_none(self):
        self.assertIsNone(chatlib._weather_reply("http://hub", "w1", "weather at the event"))

    def test_past_date_none(self):
        chatlib._LAST_RESULTS["w2"] = [{"id": "e", "title": "Past",
                                        "date": "2020-01-01", "location": "Berlin"}]
        self.assertIsNone(chatlib._weather_reply("http://hub", "w2", "weather at Past"))

    def test_no_location_none(self):
        chatlib._LAST_RESULTS["w3"] = [{"id": "e", "title": "NoLoc",
                                        "date": "2026-12-01", "location": ""}]
        self.assertIsNone(chatlib._weather_reply("http://hub", "w3", "weather at NoLoc"))

    def test_grounded_forecast(self):
        from datetime import date
        soon = date(2026, 9, 20).isoformat()
        chatlib._LAST_RESULTS["w4"] = [
            {"id": "e1", "title": "Rooftop Jazz Night", "date": soon, "location": "Berlin"}]
        geo = {"results": [{"latitude": 52.52, "longitude": 13.4}]}
        fc = {"daily": {"temperature_2m_max": [21.4], "temperature_2m_min": [12.1],
                       "precipitation_probability_max": [30]}}
        with mock.patch.object(chatlib, "_http_json", side_effect=[geo, fc]):
            r = chatlib._weather_reply("http://hub", "w4", "weather at the jazz night")
        self.assertIn("Rooftop Jazz Night", r)
        self.assertIn("21", r)
        self.assertIn("30%", r)
        self.assertIn("book 1", r)

    def test_api_failure_honest(self):
        from datetime import date
        soon = date(2026, 9, 21).isoformat()
        chatlib._LAST_RESULTS["w5"] = [
            {"id": "e2", "title": "Jazz", "date": soon, "location": "Berlin"}]
        with mock.patch.object(chatlib, "_http_json", side_effect=OSError("down")):
            r = chatlib._weather_reply("http://hub", "w5", "weather at the jazz")
        self.assertIn("couldn't fetch", r)  # honest failure, no invented forecast


class MetaPolicies(unittest.TestCase):
    """Policy questions NEVER get LLM wording — deterministic templates only."""

    def test_escrow_template(self):
        r = chatlib.brain_meta("http://hub", "m1", "how does escrow work", "made up junk")
        self.assertIn("escrow", r.lower())
        self.assertNotIn("made up junk", r)

    def test_fee_template(self):
        with mock.patch.object(chatlib, "_hub_get", return_value={"payments": {"hub_fee": 0}}):
            r = chatlib.brain_meta("http://hub", "m2", "what are your fees", "junk")
        self.assertIn("declare their fee openly", r)


class NavTokenLaw(unittest.TestCase):
    """The nav token contract between chatlib, webchat and app.js."""

    def test_home_token_shape(self):
        r = chatlib.nav_reply("http://hub", "home", "n1")
        self.assertTrue(r.startswith("[[nav:home]]"))

    def test_dash_token(self):
        r = chatlib.nav_reply("http://hub", "dashboard", "n2")
        self.assertTrue(r.startswith("[[nav:dash]]"))
        self.assertIn("Bookings", r)

    def test_unknown_target_defaults_home(self):
        r = chatlib.nav_reply("http://hub", "bogus", "n3")
        self.assertTrue(r.startswith("[[nav:home]]"))


class SiteState(unittest.TestCase):

    def test_state_lists_results(self):
        chatlib._LAST_RESULTS["st1"] = [
            {"id": "a", "title": "Jazz Night", "date": "2026-10-03", "price": 15,
             "location": "Berlin"}]
        s = brain._site_state(chatlib, "st1")
        self.assertIn("Jazz Night", s)
        self.assertIn("2026-10-03", s)
        chatlib._LAST_RESULTS.clear()

    def test_no_results_empty(self):
        self.assertEqual(brain._site_state(chatlib, "st-none"), "")


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
        self.assertIn("$abc", c)  # bad price must still render safely


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


class NaturalLanguageRouting(unittest.TestCase):
    """Brain v2 routing laws: typed fast path vs conversational break-out."""

    def setUp(self):
        chatlib._LAST_RESULTS.clear()
        chatlib._LAST_SEARCH.clear()

    def test_typed_search_stays_deterministic(self):
        with mock.patch.object(chatlib, "_smart_search", return_value="OUT") as ss:
            chatlib.handle_text("http://hub", "search jazz berlin", sender="t1")
        ss.assert_called_once()

    def test_conversational_breaks_out_to_brain(self):
        # brain disabled -> fail-open deterministic path, NOT a crash/loop
        with mock.patch.object(brain, "_DISABLED", True):
            r = chatlib.handle_text("http://hub", "find me something fun this weekend", sender="t2")
        self.assertIsInstance(r, str) and self.assertTrue(r)  # fail-open survives

    def test_typed_book_is_deterministic(self):
        with mock.patch.object(chatlib, "handle_text", wraps=chatlib.handle_text) as ht:
            pass  # placeholder to keep symmetry; typed path covered in BookByNumber

    def test_natural_book_falls_through(self):
        # 'book the first one' must NOT match the typed book branch
        with mock.patch.object(brain, "_DISABLED", True), mock.patch.object(
                chatlib, "_smart_search", return_value="S") as ss:
            chatlib.handle_text("http://hub", "book the first one", sender="t3")
        # fell through to the brain tail -> disabled -> fail-open keyword search
        ss.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
