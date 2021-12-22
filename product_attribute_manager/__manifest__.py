# Copyright 2021 Camptocamp SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

{
    "name": "Product Attribute Manager",
    "version": "14.0.1.0.0",
    "author": "Camptocamp SA, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/product-variant",
    "license": "AGPL-3",
    "category": "Product Variant",
    "depends": ["product"],
    "data": [
        "security/ir.model.access.csv",
        "wizards/product_attribute_manager_wizard.xml",
    ],
    "demo": ["demo/product_demo.xml"],
    "installable": True,
}
