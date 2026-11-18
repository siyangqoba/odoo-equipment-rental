from datetime import datetime, timedelta

from odoo.exceptions import UserError
from odoo.tests import Form, TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestReturnInspection(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        category = cls.env["rental.equipment.category"].create({"name": "Return Test"})
        cls.generator = cls.env["rental.equipment"].create({
            "name": "Generator 5kVA", "category_id": category.id, "daily_rate": 400, "deposit": 2000,
        })
        cls.saw = cls.env["rental.equipment"].create({
            "name": "Tile Saw", "category_id": category.id, "daily_rate": 150, "deposit": 500,
        })
        cls.customer = cls.env["res.partner"].create({"name": "Inspection Customer"})
        cls.start = datetime(2031, 6, 2, 8)

    def _picked_up_order(self):
        order = self.env["rental.order"].create({
            "partner_id": self.customer.id, "date_start": self.start,
            "date_end": self.start + timedelta(days=2),
            "line_ids": [(0, 0, {"equipment_id": e.id}) for e in (self.generator | self.saw)],
        })
        order.action_confirm()
        order.action_pickup()
        order.date_picked_up = self.start
        return order

    def _line(self, order, equipment):
        return order.line_ids.filtered(lambda l: l.equipment_id == equipment)

    def test_damage_covered_by_deposit(self):
        order = self._picked_up_order()
        order.action_return(self.start + timedelta(days=2), {
            self._line(order, self.generator).id: {"condition": "minor", "damage_charge": 600,
                                                    "damage_notes": "Cracked fuel cap"},
        })
        self.assertEqual(order.damage_total, 600)
        self.assertEqual(order.deposit_retained, 600)
        self.assertEqual(order.deposit_refund, 2500 - 600)
        self.assertEqual(order.damage_invoiced, 0)
        self.assertEqual(order.amount_total, order.amount_rental)
        self.assertEqual(self.generator.state, "maintenance")
        self.assertEqual(self.saw.state, "available")
        order.action_create_invoice()
        self.assertEqual(len(order.invoice_id.invoice_line_ids), 2)  # no damage line

    def test_damage_above_deposit_is_invoiced(self):
        order = self._picked_up_order()
        order.action_return(self.start + timedelta(days=2), {
            self._line(order, self.saw).id: {"condition": "major", "damage_charge": 3100},
        })
        self.assertEqual(order.deposit_retained, 2500)
        self.assertEqual(order.deposit_refund, 0)
        self.assertEqual(order.damage_invoiced, 600)
        self.assertEqual(order.amount_total, order.amount_rental + 600)
        order.action_create_invoice()
        damage_line = order.invoice_id.invoice_line_ids.filtered(lambda l: "Damage" in l.name)
        self.assertAlmostEqual(damage_line.price_subtotal, 600)
        self.assertAlmostEqual(order.invoice_id.amount_total, order.amount_rental + 600)

    def test_minor_damage_can_skip_maintenance(self):
        order = self._picked_up_order()
        order.action_return(self.start + timedelta(days=2), {
            self._line(order, self.saw).id: {"condition": "minor", "damage_charge": 50,
                                              "send_to_maintenance": False},
        })
        self.assertEqual(self.saw.state, "available")

    def test_negative_damage_rejected(self):
        order = self._picked_up_order()
        with self.assertRaises(UserError):
            order.action_return(self.start + timedelta(days=2), {
                self._line(order, self.saw).id: {"damage_charge": -10},
            })

    def test_wizard_flow(self):
        order = self._picked_up_order()
        action = order.action_open_return_wizard()
        wizard_form = Form(self.env[action["res_model"]].with_context(action["context"]))
        wizard_form.return_date = self.start + timedelta(days=3, hours=5)  # 1 day late
        with wizard_form.line_ids.edit(0) as line:
            self.assertEqual(line.condition, "good")
            line.condition = "major"
            self.assertTrue(line.send_to_maintenance)
            line.damage_charge = 2700
            line.damage_notes = "Dropped from bakkie"
        self.assertEqual(wizard_form.late_days, 2)
        self.assertEqual(wizard_form.damage_invoiced, 200)
        self.assertEqual(wizard_form.deposit_refund, 0)
        wizard_form.notes = "Returned dirty"
        wizard_form.save().action_confirm()
        self.assertEqual(order.state, "returned")
        self.assertEqual(order.late_days, 2)
        self.assertEqual(order.damage_invoiced, 200)
        self.assertEqual(order.return_notes, "Returned dirty")
        damaged = order.line_ids.filtered(lambda l: l.return_condition == "major")
        self.assertEqual(damaged.damage_notes, "Dropped from bakkie")
        self.assertEqual(damaged.equipment_id.state, "maintenance")

    def test_wizard_rejects_return_before_pickup(self):
        order = self._picked_up_order()
        action = order.action_open_return_wizard()
        wizard = self.env[action["res_model"]].with_context(action["context"]).create(
            {"return_date": self.start - timedelta(days=1)}
        )
        with self.assertRaises(UserError):
            wizard.action_confirm()

    def test_agreement_shows_settlement(self):
        order = self._picked_up_order()
        order.action_return(self.start + timedelta(days=2), {
            self._line(order, self.generator).id: {"condition": "minor", "damage_charge": 300,
                                                    "damage_notes": "Scratched housing"},
        })
        html, _fmt = self.env["ir.actions.report"]._render_qweb_html(
            "equipment_rental.action_report_rental_order", order.ids
        )
        self.assertIn(b"Deposit to refund", html)
        self.assertIn(b"Scratched housing", html)
