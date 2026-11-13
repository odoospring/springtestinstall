from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


EDI_SHIP_TO_FIELDS = (
    'edi_ship_to_code',
    'edi_ship_to_name',
    'edi_ship_to_street',
    'edi_ship_to_street2',
    'edi_ship_to_city',
    'edi_ship_to_state',
    'edi_ship_to_zip',
    'edi_ship_to_country',
)
EDI_FIELDS = (
    'edi_order_number',
    'edi_ship_open_date',
    'edi_ship_close_date',
    'edi_mark_for_location',
    'edi_order_message',
    'edi_order_id',
    'edi_division',
    'edi_department',
    'edi_merchandise_type',
    'edi_currency_code',
    'edi_shipping_pay_method',
) + EDI_SHIP_TO_FIELDS


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # Tell the form whether there is anything to show (the tab is hidden otherwise)
    edi_has_data = fields.Boolean(compute='_compute_edi_flags')
    edi_has_ship_to = fields.Boolean(compute='_compute_edi_flags')

    @api.depends(*EDI_FIELDS)
    def _compute_edi_flags(self):
        for order in self:
            order.edi_has_data = any(getattr(order, f) for f in EDI_FIELDS)
            order.edi_has_ship_to = any(getattr(order, f) for f in EDI_SHIP_TO_FIELDS)

    # Fallback navigation to the order's transfers (see action_view_edi_transfers)
    edi_transfer_count = fields.Integer('Transfers', compute='_compute_edi_transfer_count')

    def _edi_unlinked_transfers_domain(self):
        """Transfers that belong to this order by procurement group or source
        document but are NOT linked through the standard Sales Order field (the
        link Odoo's own Delivery button relies on)."""
        self.ensure_one()
        parts = [('origin', '=', self.name)]
        if self.procurement_group_id:
            parts.append(('group_id', '=', self.procurement_group_id.id))
        return [('sale_id', '!=', self.id)] + ['|'] * (len(parts) - 1) + parts

    @api.depends('state', 'name', 'procurement_group_id', 'picking_ids')
    def _compute_edi_transfer_count(self):
        Picking = self.env['stock.picking'].sudo()
        for order in self:
            if not isinstance(order.id, int) or order.state in ('draft', 'sent', 'cancel'):
                order.edi_transfer_count = 0
            else:
                order.edi_transfer_count = Picking.search_count(
                    order._edi_unlinked_transfers_domain())

    def action_view_edi_transfers(self):
        self.ensure_one()
        pickings = self.env['stock.picking'].sudo().search(self._edi_unlinked_transfers_domain())
        if not pickings:
            raise UserError(_('No transfers were found for this order.'))
        return self._get_action_view_picking(pickings)

    # Header (next to Order Date)
    edi_order_number = fields.Char('Order number', copy=False, readonly=True, index=True)
    edi_ship_open_date = fields.Date('Ship Open Date', copy=False, readonly=True)
    edi_ship_close_date = fields.Date('Ship Close Date', copy=False, readonly=True)

    # "Spring Systems EDI info" tab - left column
    edi_mark_for_location = fields.Char('Mark For location', copy=False, readonly=True)
    edi_order_message = fields.Text('Order Message', copy=False, readonly=True)

    # "Spring Systems EDI info" tab - right column
    edi_order_id = fields.Char('order ID', copy=False, readonly=True, index=True)
    edi_division = fields.Char('Division', copy=False, readonly=True)
    edi_department = fields.Char('Department', copy=False, readonly=True)
    edi_merchandise_type = fields.Char('Merchandise Type', copy=False, readonly=True)
    edi_currency_code = fields.Char('Currency', copy=False, readonly=True,
                                    help='Currency code received on the EDI order, e.g. USD')
    edi_shipping_pay_method = fields.Char('Shipping Pay Method', copy=False, readonly=True)

    # Ship-To address exactly as received via EDI (snapshot, not linked to contacts)
    edi_ship_to_code = fields.Char('Ship-To Location Code', copy=False, readonly=True)
    edi_ship_to_name = fields.Char('Ship-To Name', copy=False, readonly=True)
    edi_ship_to_street = fields.Char('Ship-To Address 1', copy=False, readonly=True)
    edi_ship_to_street2 = fields.Char('Ship-To Address 2', copy=False, readonly=True)
    edi_ship_to_city = fields.Char('Ship-To City', copy=False, readonly=True)
    edi_ship_to_state = fields.Char('Ship-To State', copy=False, readonly=True)
    edi_ship_to_zip = fields.Char('Ship-To Zip', copy=False, readonly=True)
    edi_ship_to_country = fields.Char('Ship-To Country', copy=False, readonly=True)

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
