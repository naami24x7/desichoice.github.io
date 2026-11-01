import os, sys, unittest
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import scan_in as S


def card(asin="B0ABCDEFGH", title="Tata Tea Gold 1 kg", price="₹399", was="₹520", rating="4.4 out of 5 stars", reviews="12,345",
         img="https://m.media-amazon.com/images/I/71abc._AC_UY218_.jpg", text="Tata Tea\n-23%\nLimited time deal"):
    return {"asin": asin, "title": title, "price": price, "was": was, "rating": rating, "reviews": reviews, "img": img, "text": text}


class T(unittest.TestCase):
    def test_item(self):
        it = S.card_to_item(card(), now=1000)
        self.assertEqual(it["pct"], 23); self.assertEqual(it["now"], "399"); self.assertEqual(it["was"], "520")
        self.assertEqual(it["reviews"], 12345); self.assertEqual(it["rating"], 4.4); self.assertEqual(it["deal"], "Limited time deal")
        self.assertEqual(it["img"], "https://m.media-amazon.com/images/I/71abc.jpg")
        self.assertTrue(it["link"].startswith("https://www.amazon.in/dp/B0ABCDEFGH"))

    def test_sponsored_and_bad(self):
        self.assertIsNone(S.card_to_item(card(text="Sponsored\nTata")))
        self.assertIsNone(S.card_to_item(card(asin="")))
        self.assertIsNone(S.card_to_item(card(price="")))
        self.assertIsNone(S.card_to_item(card(was="", text="no discount here")))

    def test_pct_from_text_when_no_was(self):
        it = S.card_to_item(card(was="", text="Boat Airdopes (40% off)"))
        self.assertEqual(it["pct"], 40); self.assertGreater(float(it["was"]), float(it["now"]))

    def test_gates(self):
        ok = S.card_to_item(card())
        self.assertTrue(S.credible(ok, 10, 49))
        self.assertFalse(S.credible(ok, 30, 49))                       # discount too small
        self.assertFalse(S.credible(S.card_to_item(card(rating="3.5 out of 5 stars")), 10, 49))
        self.assertFalse(S.credible(S.card_to_item(card(reviews="12")), 10, 49))
        self.assertFalse(S.credible(S.card_to_item(card(price="₹100", was="₹900", reviews="100")), 10, 49))   # 89% / suspicious
        self.assertFalse(S.credible(ok, 10, 5000))                     # too cheap for this list

    def test_parsers(self):
        self.assertEqual(S.parse_reviews("1.2K"), 1200); self.assertEqual(S.parse_reviews("(3,456)"), 3456); self.assertEqual(S.parse_reviews(""), 0)
        self.assertEqual(S.parse_rating("4.3 out of 5 stars"), 4.3); self.assertEqual(S.parse_rating(""), 0.0)
        self.assertLessEqual(len(S.short_name("word " * 40)), S.NAME_MAX)
        self.assertEqual(S.tier(500), "budget"); self.assertEqual(S.tier(5000), "mid"); self.assertEqual(S.tier(50000), "premium")

    def test_themes(self):
        n = datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)
        keys = [k for k, *_ in S.active_themes(n)]
        self.assertIn("diwali", keys); self.assertIn("winter", keys); self.assertNotIn("summer", keys)
        self.assertEqual(keys[0], "diwali")                           # soonest-ending first
        jan = [k for k, *_ in S.active_themes(datetime(2027, 1, 10, tzinfo=timezone.utc))]
        self.assertIn("winter", jan); self.assertIn("republic", jan)  # winter wraps the new year

    def test_round_robin(self):
        mk = lambda a, p: {"asin": a, "pct": p}
        out = S.round_robin({"x": [mk("1", 50), mk("2", 40)], "y": [mk("3", 60)], "z": []}, 3)
        self.assertEqual([i["asin"] for i in out], ["3", "1", "2"])

    def test_clean_and_scopes(self):
        it = S.clean(S.card_to_item(card(text="x -23%")), cat="tea")
        self.assertNotIn("_price", it); self.assertEqual(it["cat"], "tea"); self.assertIn("tier", it)
        self.assertEqual(len(S.CATEGORIES), 17)
        self.assertEqual(set(S.SCOPES), {k for k, _, _ in S.CATEGORIES})
        self.assertEqual(set(S.FALLBACK_KW), set(S.SCOPES))

    def test_accessory_filter(self):
        self.assertTrue(S.ACCESSORY.search("Dog bowl stand"))
        self.assertFalse(S.ACCESSORY.search("Tata Tea Gold 1 kg"))


if __name__ == "__main__":
    unittest.main()
