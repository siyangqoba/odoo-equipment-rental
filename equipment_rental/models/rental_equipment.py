from odoo import api, fields, models


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

    _sql_constraints = [
        ("serial_uniq", "unique(serial_number, company_id)", "Serial number must be unique."),
        ("rate_positive", "CHECK(daily_rate >= 0)", "Daily rate cannot be negative."),
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
