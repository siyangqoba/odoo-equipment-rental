from odoo import _, api, fields, models
from odoo.exceptions import UserError


class RentalReturnWizard(models.TransientModel):
    _name = "rental.return.wizard"
    _description = "Return Inspection"

    order_id = fields.Many2one("rental.order", required=True)
    currency_id = fields.Many2one(related="order_id.currency_id")
    return_date = fields.Datetime(required=True, default=fields.Datetime.now)
    line_ids = fields.One2many("rental.return.wizard.line", "wizard_id", string="Inspection")
    notes = fields.Text(string="Inspection Notes")

    late_days = fields.Integer(compute="_compute_preview")
    late_fee = fields.Monetary(compute="_compute_preview")
    damage_total = fields.Monetary(compute="_compute_preview")
    deposit_total = fields.Monetary(related="order_id.deposit_total")
    deposit_refund = fields.Monetary(string="Deposit to Refund", compute="_compute_preview")
    damage_invoiced = fields.Monetary(string="Damage to Invoice", compute="_compute_preview")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        order = self.env["rental.order"].browse(res.get("order_id") or self.env.context.get("default_order_id"))
        if order and "line_ids" in fields_list:
            res["line_ids"] = [
                fields.Command.create({"order_line_id": line.id, "condition": "good"})
                for line in order.line_ids
            ]
        return res

    @api.depends("return_date", "line_ids.damage_charge", "order_id")
    def _compute_preview(self):
        for wiz in self:
            order = wiz.order_id
            if not order or not wiz.return_date:
                wiz.late_days = 0
                wiz.late_fee = wiz.damage_total = wiz.deposit_refund = wiz.damage_invoiced = 0
                continue
            wiz.late_days = order._compute_late_days(wiz.return_date)
            pct = order.company_id.rental_late_fee_percent / 100.0
            wiz.late_fee = sum(l.daily_rate * wiz.late_days * pct for l in order.line_ids)
            wiz.damage_total = sum(wiz.line_ids.mapped("damage_charge"))
            retained = min(wiz.damage_total, order.deposit_total)
            wiz.deposit_refund = order.deposit_total - retained
            wiz.damage_invoiced = wiz.damage_total - retained

    def action_confirm(self):
        self.ensure_one()
        if self.return_date < self.order_id.date_picked_up:
            raise UserError(_("The return date can't be before the pickup."))
        inspections = {
            line.order_line_id.id: {
                "condition": line.condition,
                "damage_charge": line.damage_charge,
                "damage_notes": line.damage_notes,
                "send_to_maintenance": line.send_to_maintenance,
            }
            for line in self.line_ids
        }
        self.order_id.action_return(self.return_date, inspections, self.notes)
        return {"type": "ir.actions.act_window_close"}


class RentalReturnWizardLine(models.TransientModel):
    _name = "rental.return.wizard.line"
    _description = "Return Inspection Line"

    wizard_id = fields.Many2one("rental.return.wizard", required=True, ondelete="cascade")
    order_line_id = fields.Many2one("rental.order.line", required=True)
    equipment_id = fields.Many2one(related="order_line_id.equipment_id")
    deposit = fields.Monetary(related="order_line_id.deposit")
    currency_id = fields.Many2one(related="order_line_id.currency_id")
    condition = fields.Selection(
        [("good", "Good"), ("minor", "Minor Damage"), ("major", "Major Damage")],
        required=True, default="good",
    )
    damage_charge = fields.Monetary()
    damage_notes = fields.Char()
    send_to_maintenance = fields.Boolean(
        compute="_compute_send_to_maintenance", store=True, readonly=False,
    )

    @api.depends("condition")
    def _compute_send_to_maintenance(self):
        for line in self:
            line.send_to_maintenance = line.condition != "good"

    @api.constrains("damage_charge")
    def _check_damage_charge(self):
        if any(line.damage_charge < 0 for line in self):
            raise UserError(_("Damage charges cannot be negative."))
