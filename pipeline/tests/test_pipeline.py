import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pipeline as p

class T(unittest.TestCase):
    def test_freshness_halves_toward_floor(self):
        self.assertAlmostEqual(p.freshness(0), 1.0)
        self.assertAlmostEqual(p.freshness(14), 0.7)
        self.assertTrue(p.freshness(365) > 0.4)

    def test_republished_copies_do_not_raise_confidence(self):
        one = [{"origin": "pr", "tier": "T1"}]
        self.assertEqual(p.confidence(one, []), 0.60)
        self.assertEqual(p.confidence(one + [{"origin": "a", "tier": "T2"}, {"origin": "b", "tier": "T3"}], []), 0.85)
        self.assertEqual(p.confidence(one, [{"sev": "open"}]), 0.53)

    def test_noise_does_not_trigger_action(self):
        a = {"signals": [{"kind": "secondary_sale", "fact": "x"}], "conflicts": [],
             "factors": {"trigger": 90, "fit": 90, "budget": 90, "reach": 90}}
        self.assertIn(p.score(a, [{"origin": "x", "tier": "T2"}], 1)["verdict"], ("Skip",))

    def test_low_fit_gate(self):
        a = {"signals": [{"kind": "funding_round", "fact": "x"}], "conflicts": [],
             "factors": {"trigger": 90, "fit": 30, "budget": 90, "reach": 90}}
        self.assertEqual(p.score(a, [{"origin": "x", "tier": "T1"}], 1)["verdict"], "Low fit")

    def test_diff_ignores_unchanged_and_reports_new(self):
        old = {"signals": [{"kind": "launch", "fact": "Launched app."}]}
        new = {"signals": [{"kind": "launch", "fact": "launched  app"}, {"kind": "funding_round", "fact": "raised Rs 5 crore"}]}
        d = p.diff(old, new)
        self.assertEqual([x["kind"] for x in d], ["funding_round"])

    def test_end_to_end_two_snapshots(self):
        here = os.path.join(os.path.dirname(__file__), "..")
        con = p.db_connect(os.path.join(tempfile.mkdtemp(), "t.db"))
        p.process(con, "Olyv", "u1", os.path.join(here, "fixtures/t0"))
        p.process(con, "Olyv", "u1", os.path.join(here, "fixtures/t1"))
        kinds = [r["kind"] for r in con.execute("SELECT kind FROM changes WHERE snapshot_id=2")]
        self.assertIn("leadership_c_suite", kinds)
        p.process(con, "Olyv", "u1", os.path.join(here, "fixtures/t1"))   # same content again
        self.assertEqual(con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 2)

if __name__ == "__main__":
    unittest.main()
