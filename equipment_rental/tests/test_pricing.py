from datetime import datetime, timedelta

from odoo.tests import BaseCase, TransactionCase, tagged

from ..models.pricing import best_price


@tagged("post_install", "-at_install")
class TestBestPrice(BaseCase):
    def test_daily_only(self):
        q = best_price(3, 100)
        self.assertEqual((q.amount, q.breakdown), (300, "3 days"))

    def test_rounds_up_to_a_week_when_cheaper(self):
        # 6 days at R100 = R600, one week = R500
        q = best_price(6, 100, weekly=500)
        self.assertEqual((q.amount, q.breakdown), (500, "1 week"))

    def test_week_plus_days(self):
        q = best_price(10, 100, weekly=500)
        self.assertEqual((q.amount, q.breakdown), (800, "1 week + 3 days"))

    def test_month_week_and_day(self):
        q = best_price(45, 100, weekly=500, monthly=1800)
        self.assertEqual((q.amount, q.breakdown), (2900, "1 month + 2 weeks + 1 day"))

    def test_expensive_weekly_rate_is_ignored(self):
        # weekly is worse than 7 daily days, so it should never be chosen
        q = best_price(7, 100, weekly=800)
        self.assertEqual((q.amount, q.breakdown), (700, "7 days"))

    def test_zero_days(self):
        self.assertEqual(best_price(0, 100).amount, 0)


@tagged("post_install", "-at_install")
class TestTieredOrderPricing(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        category = cls.env["rental.equipment.category"].create({"name": "Tier Test"})
        cls.mixer = cls.env["rental.equipment"].create({
            "name": "Concrete Mixer", "category_id": category.id,
            "daily_rate": 250, "weekly_rate": 1200, "monthly_rate": 3500, "deposit": 800,
        })
        cls.customer = cls.env["res.partner"].create({"name": "Tier Customer"})

    def _order(self, days):
        start = datetime(2031, 5, 5, 8)
        return self.env["rental.order"].create({
            "partner_id": self.customer.id, "date_start": start,
            "date_end": start + timedelta(days=days),
            "line_ids": [(0, 0, {"equipment_id": self.mixer.id})],
        })

    def test_line_uses_cheapest_combination(self):
        order = self._order(9)
        line = order.line_ids
        self.assertEqual(line.weekly_rate, 1200)
        self.assertEqual(line.subtotal, 1200 + 2 * 250)
        self.assertEqual(line.pricing_note, "1 week + 2 days")
        self.assertEqual(order.amount_rental, 1700)

    def test_rate_override_on_line_reprices(self):
        order = self._order(9)
        order.line_ids.weekly_rate = 0  # e.g. special deal: daily only
        self.assertEqual(order.line_ids.subtotal, 9 * 250)

    def test_invoice_shows_breakdown(self):
        order = self._order(32)
        order.action_confirm()
        order.action_pickup()
        order.action_return(return_date=order.date_end)
        order.action_create_invoice()
        inv_line = order.invoice_id.invoice_line_ids
        self.assertEqual(len(inv_line), 1)
        self.assertIn("1 month + 2 days", inv_line.name)
        self.assertAlmostEqual(order.invoice_id.amount_total, 3500 + 500)
