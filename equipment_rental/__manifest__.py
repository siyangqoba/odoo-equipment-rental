{
    "name": "Equipment Rental",
    "version": "17.0.1.0.0",
    "summary": "Rent out equipment: bookings, availability checks, late fees and invoicing",
    "description": """
Equipment Rental Management

* Equipment catalogue with categories, daily rates and deposits
* Rental orders with a draft → confirmed → picked up → returned → invoiced workflow
* Double-booking protection across overlapping rental periods
* Automatic late fees based on a configurable company percentage
* One-click customer invoices
* Daily scheduled action that flags overdue rentals and schedules follow-ups
* Printable rental agreement (QWeb PDF)
""",
    "author": "Siyanda Ngqoba",
    "website": "https://github.com/siyangqoba/odoo-equipment-rental",
    "category": "Sales/Rental",
    "license": "LGPL-3",
    "depends": ["mail", "account"],
    "data": [
        "security/rental_security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "data/ir_cron_data.xml",
        "report/rental_order_report.xml",
        "wizard/rental_return_wizard_views.xml",
        "views/rental_equipment_views.xml",
        "views/rental_order_views.xml",
        "views/res_config_settings_views.xml",
        "views/rental_menus.xml",
    ],
    "demo": ["demo/rental_demo.xml"],
    "application": True,
    "installable": True,
}
