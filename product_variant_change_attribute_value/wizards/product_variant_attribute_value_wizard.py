# Copyright 2021 Camptocamp SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

from collections import defaultdict

import psycopg2

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class VariantAttributeValueWizard(models.TransientModel):
    _name = "variant.attribute.value.wizard"
    _description = "Wizard to change attriubtes on product variants"

    product_ids = fields.Many2many(comodel_name="product.product")
    product_variant_count = fields.Integer(readonly=True)
    product_template_count = fields.Integer(readonly=True)
    attributes_action_ids = fields.Many2many(
        comodel_name="variant.attribute.value.action",
        relation="variant_attribute_wizard_attribute_action_rel",
    )
    attribute_value_ids = fields.Many2many(comodel_name="product.attribute.value")
    available_attribute_ids = fields.Many2many(comodel_name="product.attribute")
    filter_attribute_id = fields.Many2one(
        comodel_name="product.attribute",
        domain="[('id', 'in', available_attribute_ids)]",
    )

    @api.model
    def _get_actions_from_values(self, values, _filter=None):
        if _filter:
            values = values.filtered(lambda x: x.attribute_id == _filter)
        return self.env["variant.attribute.value.action"].create(
            [
                {
                    "product_attribute_value_id": x.id,
                    "attribute_id": x.attribute_id.id,
                    "attribute_action": "do_nothing",
                }
                for x in values._origin
            ]
        )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_model = self.env.context.get("active_model")
        if active_model != "product.product":
            return res
        active_ids = self.env.context.get("active_ids")
        variants = self.env[active_model].browse(active_ids)
        attribute_values = (
            variants.product_template_attribute_value_ids.product_attribute_value_id
        )
        available_attributes = attribute_values.mapped("attribute_id")
        actions = self._get_actions_from_values(attribute_values)
        res.update(
            {
                "product_ids": [(6, 0, variants.ids)],
                "attribute_value_ids": [(6, 0, attribute_values.ids)],
                "available_attribute_ids": [(6, 0, available_attributes.ids)],
                "attributes_action_ids": [(6, 0, actions.ids)],
                "product_variant_count": len(variants),
                "product_template_count": len(variants.mapped("product_tmpl_id")),
            }
        )
        return res

    @api.onchange("filter_attribute_id")
    def _compute_attributes_action_ids(self):
        """Update actions according to the attribute to filter on.
        """
        for rec in self:
            actions = self._get_actions_from_values(
                rec.attribute_value_ids, _filter=rec.filter_attribute_id
            )
            rec.attributes_action_ids = [(6, 0, actions.ids)]

    def action_apply(self):
        for product in self.product_ids:
            self._action_apply(product)

    def _is_attribute_value_being_used(self, variant_id, attribute_value):
        """Check if attribute value is still used by a variant."""
        existing_variants = self.env["product.product"].search(
            [
                ("id", "!=", variant_id.id),
                ("product_tmpl_id", "=", variant_id.product_tmpl_id.id),
            ],
        )
        existing_attributes = existing_variants.mapped(
            "product_template_attribute_value_ids.product_attribute_value_id"
        )
        return attribute_value in existing_attributes

    def _action_apply(self, product):
        """Update a variant with all the actions set by the user in the wizard."""
        pav_ids = product.product_template_attribute_value_ids.mapped(
            "product_attribute_value_id"
        )
        pavs_to_clean_by_attr = defaultdict(self.env["product.attribute.value"].browse)
        for value_action in self.attributes_action_ids:
            action = value_action.attribute_action
            if action == "do_nothing":
                continue
            pav = value_action.product_attribute_value_id
            if pav not in pav_ids:
                continue
            ptav_ids = product.product_template_attribute_value_ids.filtered(
                lambda r: r.product_attribute_value_id != pav
            )
            if action == "delete":
                # nothing to do because `_cleanup_attribute_value` will take care
                pass
            elif action == "replace":
                if not value_action.replaced_by_id:
                    continue
                tpl_attr_value = self._handle_replace(
                    product, value_action.replaced_by_id
                )
                ptav_ids |= tpl_attr_value

            # Update the values set on the product variant
            product.product_template_attribute_value_ids = ptav_ids
            # Remove the changed value from the template attribute line if needed
            if not self._is_attribute_value_being_used(product, pav):
                pavs_to_clean_by_attr[pav.attribute_id] |= pav
        if pavs_to_clean_by_attr:
            self._cleanup_attribute_values(product, pavs_to_clean_by_attr)

    def _handle_replace(self, product, pav_replacement):
        TplAttrLine = self.env["product.template.attribute.line"]
        TplAttrValue = self.env["product.template.attribute.value"]
        template = product.product_tmpl_id
        # Find corresponding attribute line on template or create it
        attr = pav_replacement.attribute_id
        tpl_attr_line = template.attribute_line_ids.filtered(
            lambda l: l.attribute_id == attr
        )
        if not tpl_attr_line:
            tpl_attr_line = TplAttrLine.create(
                {
                    "product_tmpl_id": template.id,
                    "attribute_id": attr.id,
                    "value_ids": [(6, False, [pav_replacement.id])],
                }
            )
        # Ensure the value exists in this attribute line.
        # The context key 'update_product_template_attribute_values' avoids
        # to create/unlink variants when values are updated on the template
        # attribute line.
        tpl_attr_line.with_context(
            update_product_template_attribute_values=False
        ).write({"value_ids": [(4, pav_replacement.id)]})
        # Get (or create if needed) the 'product.template.attribute.value'
        tpl_attr_value = TplAttrValue.search(
            [
                ("attribute_line_id", "=", tpl_attr_line.id),
                ("product_attribute_value_id", "=", pav_replacement.id),
            ]
        )
        if not tpl_attr_value:
            tpl_attr_value = TplAttrValue.create(
                {
                    "attribute_line_id": tpl_attr_line.id,
                    "product_attribute_value_id": pav_replacement.id,
                }
            )
        return tpl_attr_value

    def _handle_unique_violation(self, func, error_msg):
        try:
            with self.env.cr.savepoint():
                func()
        except psycopg2.IntegrityError as e:
            if e.pgcode == psycopg2.errorcodes.UNIQUE_VIOLATION:
                raise UserError(error_msg)
            else:
                raise

    def _cleanup_attribute_values(self, product, pavs_to_clean):
        TplAttrValue = self.env["product.template.attribute.value"]
        template = product.product_tmpl_id
        for attr, pavs in pavs_to_clean.items():
            tpl_attr_line = template.attribute_line_ids.filtered(
                lambda l: l.attribute_id == attr
            )
            error_msg = self._unique_err_msg(product, tpl_attr_line, pavs)
            if not (tpl_attr_line.value_ids - pavs):
                # no value left
                def _make_inactive():
                    tpl_attr_line.active = False

                self._handle_unique_violation(_make_inactive, error_msg)
            tpl_attr_line.with_context(
                update_product_template_attribute_values=False
            ).write({"value_ids": [(3, pav.id) for pav in pavs]})
            tpl_attr_values = TplAttrValue.search(
                [
                    ("attribute_line_id", "=", tpl_attr_line.id),
                    ("product_attribute_value_id", "in", pavs.ids),
                ]
            )
            if tpl_attr_values:
                self._handle_unique_violation(tpl_attr_values.unlink, error_msg)

    def _unique_err_msg(self, product, tpl_attr_line, pavs):
        msg = _(
            "Product '%s' uniqueness compromised.\n "
            "Impossible to remove value(s): %s"
        ) % (product.display_name, ", ".join(pavs.mapped("name")))
        return msg