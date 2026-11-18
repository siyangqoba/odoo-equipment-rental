import math
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .pricing import best_price

ACTIVE_STATES = ("confirmed", "ongoing")


class RentalOrder(models.Model):
    _name = "rental.order"
    _description = "Rental Order"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_start desc, id desc"

    name = fields.Char(readonly=True, copy=False, default="New", index=True)
    partner_id = fields.Many2one("res.partner", string="Customer", required=True, tracking=True)
    user_id = fields.Many2one(
        "res.users", string="Responsible", default=lambda self: self.env.user, tracking=True
    )
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_start = fields.Datetime(
        string="Pickup", required=True, default=fields.Datetime.now, tracking=True
    )
    date_end = fields.Datetime(
        string="Planned Return",
        required=True,
        default=lambda self: fields.Datetime.now() + timedelta(days=1),
        tracking=True,
    )
    date_picked_up = fields.Datetime(readonly=True, copy=False)
    date_returned = fields.Datetime(string="Actual Return", readonly=True, copy=False)
    state = fields.Selection(
        [
            ("draft", "Quotation"),
            ("confirmed", "Confirmed"),
            ("ongoing", "Picked Up"),
            ("returned", "Returned"),
            ("invoiced", "Invoiced"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
        copy=False,
    )
    line_ids = fields.One2many("rental.order.line", "order_id", string="Equipment", copy=True)
    duration_days = fields.Integer(compute="_compute_duration", store=True)
    late_days = fields.Integer(readonly=True, copy=False)
    amount_rental = fields.Monetary(string="Rental Amount", compute="_compute_amounts", store=True)
    late_fee = fields.Monetary(readonly=True, copy=False, tracking=True)
    amount_total = fields.Monetary(string="Total", compute="_compute_amounts", store=True, tracking=True)
    deposit_total = fields.Monetary(compute="_compute_amounts", store=True)
    damage_total = fields.Monetary(
        string="Damage Charges", compute="_compute_amounts", store=True, tracking=True
    )
    deposit_retained = fields.Monetary(
        compute="_compute_amounts", store=True,
        help="Part of the deposit kept to cover damage charges.",
    )
    deposit_refund = fields.Monetary(
        string="Deposit to Refund", compute="_compute_amounts", store=True
    )
    damage_invoiced = fields.Monetary(
        string="Damage Not Covered by Deposit", compute="_compute_amounts", store=True,
        help="Damage charges above the deposit. These are added to the invoice.",
    )
    is_overdue = fields.Boolean(compute="_compute_is_overdue", search="_search_is_overdue")
    invoice_id = fields.Many2one("account.move", readonly=True, copy=False)
    return_notes = fields.Text()
    notes = fields.Html()

    _sql_constraints = [
        (
            "dates_check",
            "CHECK(date_end > date_start)",
            "The planned return must be after the pickup date.",
        ),
    ]

    # ------------------------------------------------------------------ compute
    @api.depends("date_start", "date_end")
    def _compute_duration(self):
        for order in self:
            if order.date_start and order.date_end and order.date_end > order.date_start:
                seconds = (order.date_end - order.date_start).total_seconds()
                order.duration_days = max(1, math.ceil(seconds / 86400))
            else:
                order.duration_days = 0

    @api.depends("line_ids.subtotal", "line_ids.deposit", "line_ids.damage_charge", "late_fee")
    def _compute_amounts(self):
        for order in self:
            order.amount_rental = sum(order.line_ids.mapped("subtotal"))
            order.deposit_total = sum(order.line_ids.mapped("deposit"))
            order.damage_total = sum(order.line_ids.mapped("damage_charge"))
            # Damage is settled from the deposit first; only the excess is invoiced.
            order.deposit_retained = min(order.damage_total, order.deposit_total)
            order.deposit_refund = order.deposit_total - order.deposit_retained
            order.damage_invoiced = order.damage_total - order.deposit_retained
            order.amount_total = order.amount_rental + order.late_fee + order.damage_invoiced

    @api.depends("state", "date_end")
    def _compute_is_overdue(self):
        now = fields.Datetime.now()
        for order in self:
            order.is_overdue = order.state == "ongoing" and order.date_end < now

    def _search_is_overdue(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise UserError(_("Unsupported search on overdue rentals."))
        domain = [("state", "=", "ongoing"), ("date_end", "<", fields.Datetime.now())]
        positive = (operator == "=") == value
        if positive:
            return domain
        return ["!", "&"] + domain

    # ------------------------------------------------------------------ crud
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("rental.order") or "New"
        return super().create(vals_list)

    def unlink(self):
        if any(order.state not in ("draft", "cancelled") for order in self):
            raise UserError(_("Only quotations or cancelled rentals can be deleted."))
        return super().unlink()

    # ------------------------------------------------------------------ availability
    @api.constrains("date_start", "date_end", "state", "line_ids")
    def _check_availability(self):
        for order in self.filtered(lambda o: o.state in ACTIVE_STATES):
            conflicts = order._get_conflicting_lines()
            if conflicts:
                details = ", ".join(
                    f"{line.equipment_id.display_name} ({line.order_id.name})"
                    for line in conflicts
                )
                raise ValidationError(
                    _("Equipment already booked for this period: %s", details)
                )
            dupes = order.line_ids.equipment_id.filtered(
                lambda e: len(order.line_ids.filtered(lambda l: l.equipment_id == e)) > 1
            )
            if dupes:
                raise ValidationError(
                    _("The same equipment appears twice on %s.", order.name)
                )

    def _get_conflicting_lines(self):
        """Lines of other active orders that overlap this order's period."""
        self.ensure_one()
        return self.env["rental.order.line"].search(
            [
                ("equipment_id", "in", self.line_ids.equipment_id.ids),
                ("order_id", "!=", self.id),
                ("order_id.state", "in", ACTIVE_STATES),
                ("order_id.date_start", "<", self.date_end),
                ("order_id.date_end", ">", self.date_start),
            ]
        )

    # ------------------------------------------------------------------ workflow
    def action_confirm(self):
        for order in self:
            if order.state != "draft":
                raise UserError(_("Only quotations can be confirmed."))
            if not order.line_ids:
                raise UserError(_("Add at least one piece of equipment to %s.", order.name))
            unusable = order.line_ids.equipment_id.filtered(
                lambda e: e.state in ("maintenance", "retired")
            )
            if unusable:
                raise UserError(
                    _("Not available for rent: %s", ", ".join(unusable.mapped("display_name")))
                )
            order.state = "confirmed"
        return True

    def action_pickup(self):
        for order in self:
            if order.state != "confirmed":
                raise UserError(_("Only confirmed rentals can be picked up."))
            busy = order.line_ids.equipment_id.filtered(lambda e: e.state != "available")
            if busy:
                raise UserError(
                    _("Still out or unavailable: %s", ", ".join(busy.mapped("display_name")))
                )
            order.line_ids.equipment_id.write({"state": "rented"})
            order.write({"state": "ongoing", "date_picked_up": fields.Datetime.now()})
        return True

    def action_open_return_wizard(self):
        self.ensure_one()
        if self.state != "ongoing":
            raise UserError(_("Only picked-up rentals can be returned."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Return %s", self.name),
            "res_model": "rental.return.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_order_id": self.id},
        }

    def action_return(self, return_date=None, inspections=None, notes=None):
        """Check equipment back in.

        ``inspections`` maps order-line ids to dicts with any of: condition,
        damage_charge, damage_notes, send_to_maintenance. Lines left out are
        treated as returned in good condition.
        """
        inspections = inspections or {}
        for order in self:
            if order.state != "ongoing":
                raise UserError(_("Only picked-up rentals can be returned."))
            returned_at = return_date or fields.Datetime.now()
            late_days = order._compute_late_days(returned_at)
            pct = order.company_id.rental_late_fee_percent / 100.0
            late_fee = sum(line.daily_rate * late_days * pct for line in order.line_ids)

            to_maintenance = self.env["rental.equipment"]
            for line in order.line_ids:
                result = inspections.get(line.id, {})
                condition = result.get("condition", "good")
                charge = result.get("damage_charge", 0.0)
                if charge < 0:
                    raise UserError(_("Damage charges cannot be negative."))
                line.write({
                    "return_condition": condition,
                    "damage_charge": charge,
                    "damage_notes": result.get("damage_notes"),
                })
                if result.get("send_to_maintenance", condition != "good"):
                    to_maintenance |= line.equipment_id
            (order.line_ids.equipment_id - to_maintenance).write({"state": "available"})
            to_maintenance.write({"state": "maintenance"})
            for equipment in to_maintenance:
                equipment.message_post(body=_(
                    "Sent to maintenance after return of %(order)s (%(condition)s).",
                    order=order.name,
                    condition=dict(self.env["rental.order.line"]._fields["return_condition"].selection)[
                        order.line_ids.filtered(lambda l: l.equipment_id == equipment).return_condition
                    ],
                ))

            vals = {
                "state": "returned",
                "date_returned": returned_at,
                "late_days": late_days,
                "late_fee": order.currency_id.round(late_fee),
            }
            if notes:
                vals["return_notes"] = notes
            order.write(vals)
            order.activity_ids.filtered(
                lambda a: a.activity_type_id == self.env.ref("mail.mail_activity_data_todo")
            ).action_feedback(feedback=_("Equipment returned."))
        return True

    def _compute_late_days(self, returned_at):
        self.ensure_one()
        grace = timedelta(hours=self.company_id.rental_grace_hours)
        overdue = returned_at - (self.date_end + grace)
        if overdue.total_seconds() <= 0:
            return 0
        return math.ceil((returned_at - self.date_end).total_seconds() / 86400)

    def action_create_invoice(self):
        for order in self:
            if order.state != "returned":
                raise UserError(_("Invoice the rental once the equipment is returned."))
            lines = [
                fields.Command.create(
                    {
                        "name": _(
                            "%(equipment)s – rental, %(days)s day(s) (%(pricing)s)",
                            equipment=line.equipment_id.display_name,
                            days=order.duration_days,
                            pricing=line.pricing_note,
                        ),
                        "quantity": 1,
                        "price_unit": line.subtotal,
                        "tax_ids": [fields.Command.clear()],
                    }
                )
                for line in order.line_ids
            ]
            if order.late_fee:
                lines.append(
                    fields.Command.create(
                        {
                            "name": _("Late return fee – %s day(s)", order.late_days),
                            "quantity": 1,
                            "price_unit": order.late_fee,
                            "tax_ids": [fields.Command.clear()],
                        }
                    )
                )
            if order.damage_invoiced:
                lines.append(
                    fields.Command.create(
                        {
                            "name": _(
                                "Damage charges not covered by deposit (total damage %(total)s, deposit applied %(deposit)s)",
                                total=order.currency_id.format(order.damage_total),
                                deposit=order.currency_id.format(order.deposit_retained),
                            ),
                            "quantity": 1,
                            "price_unit": order.damage_invoiced,
                            "tax_ids": [fields.Command.clear()],
                        }
                    )
                )
            invoice = self.env["account.move"].create(
                {
                    "move_type": "out_invoice",
                    "partner_id": order.partner_id.id,
                    "invoice_origin": order.name,
                    "company_id": order.company_id.id,
                    "invoice_line_ids": lines,
                }
            )
            order.write({"invoice_id": invoice.id, "state": "invoiced"})
        return self.action_view_invoice() if len(self) == 1 else True

    def action_view_invoice(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": self.invoice_id.id,
            "view_mode": "form",
        }

    def action_cancel(self):
        for order in self:
            if order.state not in ("draft", "confirmed"):
                raise UserError(_("Rentals already picked up cannot be cancelled."))
        self.write({"state": "cancelled"})
        return True

    def action_reset_draft(self):
        self.filtered(lambda o: o.state == "cancelled").write({"state": "draft"})
        return True

    # ------------------------------------------------------------------ cron
    @api.model
    def _cron_flag_overdue(self):
        todo = self.env.ref("mail.mail_activity_data_todo")
        overdue = self.search([("is_overdue", "=", True)])
        for order in overdue:
            if order.activity_ids.filtered(lambda a: a.activity_type_id == todo):
                continue
            order.activity_schedule(
                "mail.mail_activity_data_todo",
                user_id=order.user_id.id or self.env.user.id,
                summary=_("Overdue rental"),
                note=_(
                    "%(customer)s was due to return %(order)s on %(date)s.",
                    customer=order.partner_id.name,
                    order=order.name,
                    date=fields.Datetime.to_string(order.date_end),
                ),
            )
        return len(overdue)


class RentalOrderLine(models.Model):
    _name = "rental.order.line"
    _description = "Rental Order Line"

    order_id = fields.Many2one("rental.order", required=True, ondelete="cascade", index=True)
    equipment_id = fields.Many2one(
        "rental.equipment",
        required=True,
        domain="[('state', 'not in', ('maintenance', 'retired'))]",
    )
    currency_id = fields.Many2one(related="order_id.currency_id")
    daily_rate = fields.Monetary(
        compute="_compute_rates", store=True, readonly=False, precompute=True
    )
    weekly_rate = fields.Monetary(
        compute="_compute_rates", store=True, readonly=False, precompute=True
    )
    monthly_rate = fields.Monetary(
        compute="_compute_rates", store=True, readonly=False, precompute=True
    )
    deposit = fields.Monetary(
        compute="_compute_rates", store=True, readonly=False, precompute=True
    )
    days = fields.Integer(string="Days", related="order_id.duration_days")
    subtotal = fields.Monetary(compute="_compute_subtotal", store=True)
    return_condition = fields.Selection(
        [("good", "Good"), ("minor", "Minor Damage"), ("major", "Major Damage")],
        readonly=True, copy=False,
    )
    damage_charge = fields.Monetary(readonly=True, copy=False)
    damage_notes = fields.Char(readonly=True, copy=False)
    pricing_note = fields.Char(
        string="Priced As", compute="_compute_subtotal", store=True,
        help="The cheapest combination of monthly, weekly and daily rates for this period.",
    )

    @api.depends("equipment_id")
    def _compute_rates(self):
        for line in self:
            line.daily_rate = line.equipment_id.daily_rate
            line.weekly_rate = line.equipment_id.weekly_rate
            line.monthly_rate = line.equipment_id.monthly_rate
            line.deposit = line.equipment_id.deposit

    @api.depends("daily_rate", "weekly_rate", "monthly_rate", "order_id.duration_days")
    def _compute_subtotal(self):
        for line in self:
            quote = best_price(
                line.order_id.duration_days, line.daily_rate, line.weekly_rate, line.monthly_rate
            )
            line.subtotal = quote.amount
            line.pricing_note = quote.breakdown
