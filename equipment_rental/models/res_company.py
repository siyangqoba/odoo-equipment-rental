from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    rental_late_fee_percent = fields.Float(
        string="Late Fee (% of daily rate)",
        default=150.0,
        help="Charged per late day, per item, as a percentage of the item's daily rate.",
    )
    rental_grace_hours = fields.Integer(
        string="Grace Period (hours)",
        default=2,
        help="Returns within this many hours after the planned end are not charged a late fee.",
    )
