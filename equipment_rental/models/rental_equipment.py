from odoo import _, api, fields, models


class RentalEquipmentCategory(models.Model):
    _name = "rental.equipment.category"
    _description = "Equipment Category"
    _order = "name"

    name = fields.Char(required=True, translate=True)
    equipment_ids = fields.One2many("rental.equipment", "category_id")
    equipment_count = fields.Integer(compute="_compute_equipment_count")

    _sql_constraints = [
        ("name_uniq", "unique(name)", "A category with this name already exists."),
    ]

    @api.depends("equipment_ids")
    def _compute_equipment_count(self):
        for category in self:
            category.equipment_count = len(category.equipment_ids)


class RentalEquipment(models.Model):
    _name = "rental.equipment"
    _description = "Rental Equipment"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(
        string="Reference", readonly=True, copy=False, default="New", index=True
    )
    category_id = fields.Many2one("rental.equipment.category", required=True, tracking=True)
    serial_number = fields.Char(copy=False)
    image_1920 = fields.Image(max_width=1920, max_height=1920)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    daily_rate = fields.Monetary(required=True, tracking=True)
    weekly_rate = fields.Monetary(
        tracking=True, help="Price for 7 days. Leave at 0 if you don't offer weekly hire."
    )
    monthly_rate = fields.Monetary(
        tracking=True, help="Price for 30 days. Leave at 0 if you don't offer monthly hire."
    )
    deposit = fields.Monetary(help="Refundable security deposit charged per rental.")
    state = fields.Selection(
        [
            ("available", "Available"),
            ("rented", "Rented Out"),
            ("maintenance", "In Maintenance"),
            ("retired", "Retired"),
        ],
        default="available",
        required=True,
        tracking=True,
        group_expand="_expand_states",
    )
    active = fields.Boolean(default=True)
    notes = fields.Html()
    order_line_ids = fields.One2many("rental.order.line", "equipment_id")
    rental_count = fields.Integer(compute="_compute_rental_stats")
    total_revenue = fields.Monetary(compute="_compute_rental_stats")
    utilisation_days = fields.Integer(
        compute="_compute_rental_stats", string="Days Rented"
    )

    # Preventive maintenance
    service_interval_days = fields.Integer(
        string="Service Every (rental days)",
        help="Number of rental days between preventive services. 0 means no schedule.",
    )
    last_service_date = fields.Date(readonly=True, copy=False, tracking=True)
    rental_days_since_service = fields.Integer(
        compute="_compute_service_status", store=True, string="Rental Days Since Service"
    )
    service_due = fields.Boolean(compute="_compute_service_status", store=True)
    service_ids = fields.One2many("rental.equipment.service", "equipment_id", string="Service Log")
    service_count = fields.Integer(compute="_compute_service_count")
    maintenance_cost = fields.Monetary(compute="_compute_service_count", string="Maintenance Cost")

    _sql_constraints = [
        ("serial_uniq", "unique(serial_number, company_id)", "Serial number must be unique."),
        ("rate_positive", "CHECK(daily_rate >= 0)", "Daily rate cannot be negative."),
        ("interval_positive", "CHECK(service_interval_days >= 0)", "Service interval cannot be negative."),
        (
            "tier_rates_positive",
            "CHECK(weekly_rate >= 0 AND monthly_rate >= 0)",
            "Weekly and monthly rates cannot be negative.",
        ),
    ]

    @api.model
    def _expand_states(self, states, domain, order):
        return [key for key, _label in self._fields["state"].selection]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("code", "New") == "New":
                vals["code"] = self.env["ir.sequence"].next_by_code("rental.equipment") or "New"
        return super().create(vals_list)

    @api.depends("order_line_ids.order_id.state", "order_line_ids.subtotal")
    def _compute_rental_stats(self):
        done_states = ("ongoing", "returned", "invoiced")
        for equipment in self:
            lines = equipment.order_line_ids.filtered(
                lambda l: l.order_id.state in done_states
            )
            equipment.rental_count = len(lines.order_id)
            equipment.total_revenue = sum(lines.mapped("subtotal"))
            equipment.utilisation_days = sum(lines.mapped("order_id.duration_days"))

    @api.depends(
        "service_interval_days",
        "last_service_date",
        "order_line_ids.order_id.state",
        "order_line_ids.order_id.date_returned",
        "order_line_ids.order_id.duration_days",
    )
    def _compute_service_status(self):
        for equipment in self:
            orders = equipment.order_line_ids.order_id.filtered(
                lambda o: o.state in ("returned", "invoiced")
                and o.date_returned
                and (not equipment.last_service_date or o.date_returned.date() >= equipment.last_service_date)
            )
            days = sum(orders.mapped("duration_days"))
            equipment.rental_days_since_service = days
            equipment.service_due = bool(
                equipment.service_interval_days and days >= equipment.service_interval_days
            )

    @api.depends("service_ids.cost")
    def _compute_service_count(self):
        for equipment in self:
            equipment.service_count = len(equipment.service_ids)
            equipment.maintenance_cost = sum(equipment.service_ids.mapped("cost"))

    def _flag_if_service_due(self, responsible=None):
        """Called after a return: equipment that has reached its interval goes to maintenance."""
        due = self.filtered(lambda e: e.service_due and e.state == "available")
        for equipment in due:
            equipment.state = "maintenance"
            equipment.message_post(body=_(
                "Preventive service due: %(days)s rental days since last service (interval %(interval)s).",
                days=equipment.rental_days_since_service,
                interval=equipment.service_interval_days,
            ))
            equipment.activity_schedule(
                "mail.mail_activity_data_todo",
                summary="Service due",
                user_id=(responsible or self.env.user).id,
            )
        return due

    def action_log_service(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Log Service"),
            "res_model": "rental.equipment.service",
            "view_mode": "form",
            "target": "new",
            "context": {"default_equipment_id": self.id},
        }

    def action_view_services(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Service Log: %s", self.name),
            "res_model": "rental.equipment.service",
            "view_mode": "tree,form",
            "domain": [("equipment_id", "=", self.id)],
            "context": {"default_equipment_id": self.id},
        }

    def _compute_display_name(self):
        for equipment in self:
            equipment.display_name = (
                f"[{equipment.code}] {equipment.name}" if equipment.code else equipment.name
            )

    def action_send_to_maintenance(self):
        self.write({"state": "maintenance"})

    def action_mark_available(self):
        self.write({"state": "available"})

    def action_retire(self):
        self.write({"state": "retired", "active": False})

    def action_view_rentals(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Rentals of {self.name}",
            "res_model": "rental.order",
            "view_mode": "tree,form,calendar",
            "domain": [("line_ids.equipment_id", "=", self.id)],
        }
