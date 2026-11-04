from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # Header (next to Order Date)
    edi_order_number = fields.Char('Order number', copy=False, index=True)
    edi_ship_open_date = fields.Date('Ship Open Date', copy=False)
    edi_ship_close_date = fields.Date('Ship Close Date', copy=False)

    # "Spring Systems EDI info" tab - left column
    edi_mark_for_location = fields.Char('Mark For location', copy=False)
    edi_order_message = fields.Text('Order Message', copy=False)

    # "Spring Systems EDI info" tab - right column
    edi_order_id = fields.Char('order ID', copy=False, index=True)
    edi_division = fields.Char('Division', copy=False)
    edi_department = fields.Char('Department', copy=False)
    edi_merchandise_type = fields.Char('Merchandise Type', copy=False)
    edi_currency_code = fields.Char('Currency', copy=False,
                                    help='Currency code received on the EDI order, e.g. USD')
    edi_shipping_pay_method = fields.Char('Shipping Pay Method', copy=False)

    # Ship-To address exactly as received via EDI (snapshot, not linked to contacts)
    edi_ship_to_code = fields.Char('Ship-To Location Code', copy=False)
    edi_ship_to_name = fields.Char('Ship-To Name', copy=False)
    edi_ship_to_street = fields.Char('Ship-To Address 1', copy=False)
    edi_ship_to_street2 = fields.Char('Ship-To Address 2', copy=False)
    edi_ship_to_city = fields.Char('Ship-To City', copy=False)
    edi_ship_to_state = fields.Char('Ship-To State', copy=False)
    edi_ship_to_zip = fields.Char('Ship-To Zip', copy=False)
    edi_ship_to_country = fields.Char('Ship-To Country', copy=False)

    # Database-level backstop (also protects against two requests arriving at once).
    # Empty values are stored as NULL, which PostgreSQL treats as distinct, so
    # orders without an EDI order ID are never blocked.
    _sql_constraints = [
        ('edi_order_id_uniq',
         'unique(company_id, partner_id, edi_order_id)',
         'Duplicate order: this order ID has already been pushed for this customer.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        # Friendly pre-check that names the existing order.
        seen = set()
        for vals in vals_list:
            edi_id = vals.get('edi_order_id')
            if not edi_id:
                continue
            company_id = vals.get('company_id') or self.env.company.id
            partner_id = vals.get('partner_id')
            key = (company_id, partner_id, edi_id)
            existing = self.sudo().search([
                ('edi_order_id', '=', edi_id),
                ('partner_id', '=', partner_id),
                ('company_id', '=', company_id),
            ], limit=1)
            if existing or key in seen:
                raise ValidationError(_(
                    'Duplicate order: order ID %(oid)s has already been pushed '
                    'for this customer (existing order: %(name)s).',
                    oid=edi_id, name=existing.name or _('same batch')))
            seen.add(key)
        return super().create(vals_list)

    def _prepare_invoice(self):
        """Carry the EDI references onto invoices created from this order."""
        vals = super()._prepare_invoice()
        vals.update({
            'edi_order_number': self.edi_order_number,
            'edi_order_id': self.edi_order_id,
        })
        return vals

    def _edi_ship_to_dict(self):
        self.ensure_one()
        return {
            'code': self.edi_ship_to_code,
            'name': self.edi_ship_to_name,
            'address1': self.edi_ship_to_street,
            'address2': self.edi_ship_to_street2,
            'city': self.edi_ship_to_city,
            'state': self.edi_ship_to_state,
            'zip': self.edi_ship_to_zip,
            'country': self.edi_ship_to_country,
            'mark_for_location': self.edi_mark_for_location,
        }
