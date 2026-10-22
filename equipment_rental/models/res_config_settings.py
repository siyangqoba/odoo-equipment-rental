from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    rental_late_fee_percent = fields.Float(
        related="company_id.rental_late_fee_percent", readonly=False
    )
    rental_grace_hours = fields.Integer(
        related="company_id.rental_grace_hours", readonly=False
    )
