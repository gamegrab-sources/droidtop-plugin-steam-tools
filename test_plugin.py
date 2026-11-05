"""Tests for Steam tools (python3 test_plugin.py, after build.sh). droidtop's Steam account API is faked
here: product facts and answers in the shape droidtop docs/plugin-api.md 3 G8 gives them, written for
the tests, not copied from Steam."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "common"), os.path.join(HERE, "src")]

import gamegrab as gg  # noqa: E402
import plugin  # noqa: E402
from testing import FakeHost, envelope, install  # noqa: E402


class SteamHost(FakeHost):
    """droidtop with a signed-in Steam account and droidtop's hourly limit of claims."""

    def __init__(self):
        super().__init__()
        self.current = 100
        self.changed_apps = []
        self.changed_packages = []
        self.app_facts = {}
        self.package_facts = {}
        self.limit = 30
        self.claimed = []
        self.keys = {}
        self.playing = None

    def __call__(self, api, op, args, version):
        if api != "steam":
            return super().__call__(api, op, args, version)
        self.calls.append((api, op, args))
        if op == "list_changes":
            return {"ok": True, "data": {"current": self.current, "apps": self.changed_apps, "packages": self.changed_packages, "fullUpdateNeeded": False}}
        if op == "get_product_info":
            apps = {str(a): self.app_facts.get(a, {}) for a in args.get("apps") or []}
            packages = {str(p): self.package_facts.get(p, {}) for p in args.get("packages") or []}
            return {"ok": True, "data": {"apps": apps, "packages": packages}}
        if op == "claim_free":
            results = []
            for kind, ids in (("app", args.get("apps") or []), ("package", args.get("packages") or [])):
                for i in ids:
                    if self.limit > 0:
                        self.limit -= 1
                        self.claimed.append((kind, i))
                        results.append({"kind": kind, "id": i, "claimed": True, "reason": None})
                    else:
                        results.append({"kind": kind, "id": i, "claimed": False, "reason": "later"})
            return {"ok": True, "data": {"results": results, "retryAfterMs": 0}}
        if op == "redeem_key":
            return {"ok": True, "data": self.keys.get(args["key"], {"result": "invalid", "items": []})}
        if op == "play":
            self.playing = args["apps"]
            return {"ok": True, "data": {"playing": True, "reason": None}}
        if op == "stop":
            self.playing = None
            return {"ok": True, "data": {"stopped": True}}
        return {"ok": False, "error": {"code": "UNSUPPORTED", "message": op}}


def call(point, op, args=None):
    return json.loads(plugin.handle(envelope(point, op, args)))


def job(point, op, args=None):
    return json.loads(plugin.start_job("j", json.loads(envelope(point, op, args)), lambda p, s: None))


class SteamTools(unittest.TestCase):
    def setUp(self):
        self.host = install(gg, SteamHost())
        plugin.time.sleep = lambda s: None

    def test_the_first_look_only_learns_where_steam_is(self):
        self.host.changed_apps = [1, 2, 3]
        self.assertEqual(plugin.look(now=0), "Watching Steam for free games")
        self.assertEqual(plugin.state()["since"], 100)
        self.assertEqual(self.host.claimed, [])

    def test_free_games_are_claimed_and_the_rest_left_alone(self):
        plugin.look(now=0)
        self.host.current = 120
        self.host.changed_apps = [10, 11, 12, 13]
        self.host.changed_packages = [500, 501, 502]
        self.host.app_facts = {
            10: {"common/type": "Game", "common/name": "Free Game", "extended/isfreeapp": "1"},
            11: {"common/type": "Game", "common/name": "Paid Game"},
            12: {"common/type": "Demo", "common/name": "A Demo", "extended/isfreeapp": "1"},
            13: {"common/type": "Game", "common/name": "Thing Playtest", "extended/isfreeapp": "1"},
        }
        self.host.package_facts = {
            500: {"billingtype": "12", "status": "0", "name": "Free Package"},
            501: {"billingtype": "12", "status": "0", "extended/expirytime": "1700000000", "name": "Free Weekend"},
            502: {"billingtype": "1", "status": "0", "name": "Bought"},
        }
        line = plugin.look(now=1000)
        self.assertEqual(sorted(self.host.claimed), [("app", 10), ("package", 500)])
        self.assertEqual(line, "Claimed 2 free games; 0 waiting")
        s = plugin.state()
        self.assertEqual(s["since"], 120)
        self.assertEqual({c["name"] for c in s["claimed"]}, {"Free Game", "Free Package"})

    def test_demos_and_playtests_only_when_wanted(self):
        opts = dict(plugin.DEFAULTS, demos=True)
        self.assertTrue(plugin.is_free_app({"common/type": "Demo", "extended/isfreeapp": "1"}, opts))
        self.assertFalse(plugin.is_free_app({"common/type": "Game", "common/name": "X Playtest", "extended/isfreeapp": "1"}, opts))
        self.assertTrue(plugin.is_free_package({"billingtype": "12", "status": "0", "extended/freeweekend": "1"}, dict(plugin.DEFAULTS, freeWeekends=True)))
        self.assertFalse(plugin.is_free_package({"billingtype": "12", "status": "2"}, plugin.DEFAULTS))

    def test_what_droidtop_holds_back_waits_for_the_next_look(self):
        plugin.look(now=0)
        self.host.current = 130
        self.host.limit = 1
        self.host.changed_packages = [600, 601, 602]
        self.host.package_facts = {p: {"billingtype": "12", "status": "0"} for p in (600, 601, 602)}
        self.assertEqual(plugin.look(now=1), "Claimed 1 free games; 2 waiting")
        self.host.limit = 30
        self.host.changed_packages = []
        self.assertEqual(plugin.look(now=2), "Claimed 2 free games; 0 waiting")
        self.assertEqual(len(self.host.claimed), 3)

    def test_keys_are_redeemed_one_by_one_and_listed(self):
        self.host.keys = {"AAAAA-BBBBB-CCCCC": {"result": "ok", "items": ["Portal 2"]}}
        reply = job("ui.facets", "redeem", {"values": {"keys": "aaaaa-bbbbb-ccccc\nDDDDD-EEEEE-FFFFF  AAAAA-BBBBB-CCCCC"}})
        self.assertEqual(reply["ok"], True)
        self.assertIn("Redeemed 1 of 2", json.dumps(reply))
        keys = plugin.state()["keys"]
        self.assertEqual([k["result"] for k in keys], ["ok", "invalid"])
        # Only the start of a key is kept.
        self.assertEqual(keys[0]["key"], "AAAAA-…")
        self.assertEqual(plugin.split_keys("no keys here"), [])

    def test_games_shown_as_played_follow_the_setting(self):
        call("ui.facets", "set", {"key": "playApps", "value": "440, 570 440"})
        self.assertIsNone(self.host.playing)
        call("ui.facets", "set", {"key": "play", "value": True})
        self.assertEqual(self.host.playing, [440, 570])
        call("ui.facets", "set", {"key": "play", "value": False})
        self.assertIsNone(self.host.playing)

    def test_the_page_lists_the_switches_the_form_and_what_was_claimed(self):
        facets = call("ui.facets", "get", {"target": "settings.page"})["data"]["facets"]
        ids = [f["id"] for f in facets]
        self.assertEqual(ids[:6], ["claim", "demos", "playtests", "freeWeekends", "play", "playApps"])
        form = next(f for f in facets if f["kind"] == "group")
        self.assertTrue(form["form"])
        self.assertEqual(form["submit"], {"op": "redeem", "kind": "job"})
        self.assertIn("Nothing claimed yet", json.dumps(facets))


if __name__ == "__main__":
    unittest.main()
