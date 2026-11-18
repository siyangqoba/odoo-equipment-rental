from odoo import api, fields, models


class RentalEquipmentService(models.Model):
    _name = "rental.equipment.service"
    _description = "Equipment Service Record"
    _order = "date desc, id desc"

    equipment_id = fields.Many2one("rental.equipment", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="equipment_id.company_id", store=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    date = fields.Date(required=True, default=fields.Date.context_today)
    service_type = fields.Selection(
        [("preventive", "Preventive Service"), ("repair", "Repair"), ("inspection", "Inspection")],
        required=True, default="preventive",
    )
    description = fields.Char(required=True)
    cost = fields.Monetary()
    technician_id = fields.Many2one("res.users", default=lambda self: self.env.user)
    notes = fields.Text()
    return_to_service = fields.Boolean(
        default=True,
        help="Mark the equipment available again once this record is saved.",
    )

    _sql_constraints = [("cost_positive", "CHECK(cost >= 0)", "Service cost cannot be negative.")]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._apply_to_equipment()
        return records

    def _apply_to_equipment(self):
        for record in self.sorted("date"):
            equipment = record.equipment_id
            vals = {}
            if not equipment.last_service_date or record.date >= equipment.last_service_date:
                vals["last_service_date"] = record.date
            if record.return_to_service and equipment.state == "maintenance":
                vals["state"] = "available"
            if vals:
                equipment.write(vals)
            equipment.message_post(body=f"{dict(self._fields['service_type'].selection)[record.service_type]} logged: {record.description}")
            equipment.activity_ids.filtered(
                lambda a: a.summary == "Service due"
            ).action_feedback(feedback=record.description)
