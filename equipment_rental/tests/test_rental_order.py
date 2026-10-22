from datetime import datetime, timedelta

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRentalOrder(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.write({"rental_late_fee_percent": 150.0, "rental_grace_hours": 2})
        cls.customer = cls.env["res.partner"].create({"name": "Bongani Construction"})
        category = cls.env["rental.equipment.category"].create({"name": "Test Tools"})
        cls.drill = cls.env["rental.equipment"].create(
            {"name": "Test Drill", "category_id": category.id, "daily_rate": 200, "deposit": 500}
        )
        cls.saw = cls.env["rental.equipment"].create(
            {"name": "Test Saw", "category_id": category.id, "daily_rate": 100, "deposit": 300}
        )
        cls.start = datetime(2030, 3, 2, 8, 0)
        cls.end = datetime(2030, 3, 5, 8, 0)

    def _order(self, equipment, start=None, end=None):
        return self.env["rental.order"].create(
            {
                "partner_id": self.customer.id,
                "date_start": start or self.start,
                "date_end": end or self.end,
                "line_ids": [(0, 0, {"equipment_id": e.id}) for e in equipment],
            }
        )

    def test_sequences_and_amounts(self):
        order = self._order(self.drill | self.saw)
        self.assertTrue(order.name.startswith("RENT/"))
        self.assertTrue(self.drill.code.startswith("EQ-"))
        self.assertEqual(order.duration_days, 3)
        self.assertEqual(order.amount_rental, 900)  # (200 + 100) * 3
        self.assertEqual(order.deposit_total, 800)

    def test_partial_day_rounds_up(self):
        order = self._order(self.drill, end=self.start + timedelta(days=1, hours=1))
        self.assertEqual(order.duration_days, 2)

    def test_double_booking_blocked(self):
        self._order(self.drill).action_confirm()
        overlapping = self._order(
            self.drill, self.start + timedelta(days=1), self.end + timedelta(days=1)
        )
        with self.assertRaises(ValidationError):
            overlapping.action_confirm()

    def test_back_to_back_bookings_allowed(self):
        self._order(self.drill).action_confirm()
        nxt = self._order(self.drill, self.end, self.end + timedelta(days=2))
        nxt.action_confirm()
        self.assertEqual(nxt.state, "confirmed")

    def test_cancelled_orders_free_equipment(self):
        first = self._order(self.drill)
        first.action_confirm()
        first.action_cancel()
        second = self._order(self.drill)
        second.action_confirm()
        self.assertEqual(second.state, "confirmed")

    def test_maintenance_equipment_cannot_be_booked(self):
        self.drill.action_send_to_maintenance()
        with self.assertRaises(UserError):
            self._order(self.drill).action_confirm()

    def test_full_workflow_on_time(self):
        order = self._order(self.drill)
        order.action_confirm()
        order.action_pickup()
        self.assertEqual(self.drill.state, "rented")
        order.action_return(return_date=self.end + timedelta(hours=1))  # inside grace
        self.assertEqual(order.state, "returned")
        self.assertEqual(order.late_fee, 0)
        self.assertEqual(self.drill.state, "available")

        order.action_create_invoice()
        self.assertEqual(order.state, "invoiced")
        invoice = order.invoice_id
        self.assertEqual(invoice.move_type, "out_invoice")
        self.assertEqual(invoice.partner_id, self.customer)
        self.assertEqual(invoice.invoice_origin, order.name)
        self.assertAlmostEqual(invoice.amount_total, 600)

    def test_late_return_charges_fee(self):
        order = self._order(self.drill | self.saw)
        order.action_confirm()
        order.action_pickup()
        order.action_return(return_date=self.end + timedelta(days=1, hours=5))  # 2 late days
        self.assertEqual(order.late_days, 2)
        # (200 + 100) * 2 days * 150%
        self.assertEqual(order.late_fee, 900)
        self.assertEqual(order.amount_total, 900 + 900)
        order.action_create_invoice()
        self.assertAlmostEqual(order.invoice_id.amount_total, 1800)
        self.assertEqual(len(order.invoice_id.invoice_line_ids), 3)

    def test_cannot_pickup_equipment_already_out(self):
        first = self._order(self.drill, datetime(2030, 1, 1), datetime(2030, 1, 2))
        first.action_confirm()
        first.action_pickup()  # never returned
        second = self._order(self.drill)
        second.action_confirm()
        with self.assertRaises(UserError):
            second.action_pickup()

    def test_overdue_cron_schedules_activity_once(self):
        order = self._order(
            self.drill, datetime.now() - timedelta(days=3), datetime.now() - timedelta(days=1)
        )
        order.action_confirm()
        order.action_pickup()
        self.assertTrue(order.is_overdue)
        self.assertIn(order, self.env["rental.order"].search([("is_overdue", "=", True)]))
        self.env["rental.order"]._cron_flag_overdue()
        self.env["rental.order"]._cron_flag_overdue()
        self.assertEqual(len(order.activity_ids), 1)
        order.action_return()
        self.assertFalse(order.activity_ids)

    def test_invalid_transitions(self):
        order = self._order(self.drill)
        with self.assertRaises(UserError):
            order.action_pickup()
        with self.assertRaises(UserError):
            order.action_create_invoice()
        order.action_confirm()
        order.action_pickup()
        with self.assertRaises(UserError):
            order.action_cancel()
        with self.assertRaises(UserError):
            order.unlink()

    def test_report_renders(self):
        order = self._order(self.drill)
        order.action_confirm()
        html, _fmt = self.env["ir.actions.report"]._render_qweb_html(
            "equipment_rental.action_report_rental_order", order.ids
        )
        self.assertIn(order.name.encode(), html)
        self.assertIn(b"Rental Agreement", html)
