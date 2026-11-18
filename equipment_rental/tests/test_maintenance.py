from datetime import date, datetime, timedelta

from odoo.tests import Form, TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPreventiveMaintenance(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        category = cls.env["rental.equipment.category"].create({"name": "Maintenance Test"})
        cls.compactor = cls.env["rental.equipment"].create({
            "name": "Plate Compactor", "category_id": category.id,
            "daily_rate": 300, "service_interval_days": 10,
        })
        cls.customer = cls.env["res.partner"].create({"name": "Maintenance Customer"})

    def _rent(self, start, days, equipment=None):
        equipment = equipment or self.compactor
        order = self.env["rental.order"].create({
            "partner_id": self.customer.id, "date_start": start,
            "date_end": start + timedelta(days=days),
            "line_ids": [(0, 0, {"equipment_id": equipment.id})],
        })
        order.action_confirm()
        order.action_pickup()
        order.action_return(return_date=order.date_end)
        return order

    def test_counts_rental_days_and_flags_when_due(self):
        self._rent(datetime(2031, 1, 5), 4)
        self.assertEqual(self.compactor.rental_days_since_service, 4)
        self.assertFalse(self.compactor.service_due)
        self.assertEqual(self.compactor.state, "available")

        order = self._rent(datetime(2031, 1, 12), 6)
        self.assertEqual(self.compactor.rental_days_since_service, 10)
        self.assertTrue(self.compactor.service_due)
        # reached the interval on return: pulled out of the rental pool
        self.assertEqual(self.compactor.state, "maintenance")
        activity = self.compactor.activity_ids.filtered(lambda a: a.summary == "Service due")
        self.assertEqual(activity.user_id, order.user_id)

    def test_logging_service_resets_counter_and_returns_to_pool(self):
        self._rent(datetime(2031, 1, 5), 12)
        self.assertEqual(self.compactor.state, "maintenance")
        self.env["rental.equipment.service"].create({
            "equipment_id": self.compactor.id, "date": date(2031, 1, 20),
            "description": "Engine oil and filter changed", "cost": 650,
        })
        self.assertEqual(self.compactor.last_service_date, date(2031, 1, 20))
        self.assertEqual(self.compactor.rental_days_since_service, 0)
        self.assertFalse(self.compactor.service_due)
        self.assertEqual(self.compactor.state, "available")
        self.assertFalse(self.compactor.activity_ids.filtered(lambda a: a.summary == "Service due"))
        self.assertEqual(self.compactor.service_count, 1)
        self.assertEqual(self.compactor.maintenance_cost, 650)

        # only rentals returned after the service count toward the next one
        self._rent(datetime(2031, 2, 1), 3)
        self.assertEqual(self.compactor.rental_days_since_service, 3)

    def test_service_can_keep_equipment_in_maintenance(self):
        self.compactor.action_send_to_maintenance()
        self.env["rental.equipment.service"].create({
            "equipment_id": self.compactor.id, "service_type": "inspection",
            "description": "Waiting on spare part", "return_to_service": False,
        })
        self.assertEqual(self.compactor.state, "maintenance")

    def test_older_service_record_does_not_move_date_back(self):
        Service = self.env["rental.equipment.service"]
        Service.create({"equipment_id": self.compactor.id, "date": date(2031, 3, 1), "description": "Newer"})
        Service.create({"equipment_id": self.compactor.id, "date": date(2031, 1, 1), "description": "Backfilled"})
        self.assertEqual(self.compactor.last_service_date, date(2031, 3, 1))

    def test_no_interval_means_never_due(self):
        self.compactor.service_interval_days = 0
        self._rent(datetime(2031, 1, 5), 40)
        self.assertFalse(self.compactor.service_due)
        self.assertEqual(self.compactor.state, "available")

    def test_damaged_and_due_equipment_only_flagged_once(self):
        order = self.env["rental.order"].create({
            "partner_id": self.customer.id, "date_start": datetime(2031, 1, 5),
            "date_end": datetime(2031, 1, 17),
            "line_ids": [(0, 0, {"equipment_id": self.compactor.id})],
        })
        order.action_confirm()
        order.action_pickup()
        order.action_return(order.date_end, {order.line_ids.id: {"condition": "major", "damage_charge": 100}})
        self.assertEqual(self.compactor.state, "maintenance")
        # already in maintenance because of damage, so no duplicate service activity
        self.assertFalse(self.compactor.activity_ids.filtered(lambda a: a.summary == "Service due"))

    def test_log_service_button_opens_prefilled_form(self):
        action = self.compactor.action_log_service()
        form = Form(self.env[action["res_model"]].with_context(action["context"]))
        self.assertEqual(form.equipment_id, self.compactor)
        form.description = "Belt tensioned"
        record = form.save()
        self.assertEqual(record.service_type, "preventive")
