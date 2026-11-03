from odoo import _, fields, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = ['stock.picking', 'edi.push.mixin']

    # Read-only mirrors of the sales order values. Stored so they can be
    # searched, grouped and exported, and kept in sync if the order changes.
    edi_order_number = fields.Char('Order number', related='sale_id.edi_order_number',
                                   store=True, readonly=True, index=True)
    edi_order_id = fields.Char('order ID', related='sale_id.edi_order_id',
                               store=True, readonly=True, index=True)

    def _edi_document_type(self):
        return 'delivery'

    def _edi_check_pushable(self):
        if self.state != 'done':
            raise UserError(_('Only validated (Done) transfers can be pushed.'))

    def _edi_build_payload(self):
        self.ensure_one()
        order = self.sale_id
        return {
            'delivery_number': self.name,
            'origin': self.origin,
            'scheduled_date': self.scheduled_date and self.scheduled_date.isoformat(),
            'date_done': self.date_done and self.date_done.isoformat(),
            'customer': {'name': self.partner_id.name, 'ref': self.partner_id.ref},
            'edi_order_number': self.edi_order_number,
            'edi_order_id': self.edi_order_id,
            'ship_to': order._edi_ship_to_dict() if order else {},
            'lines': [{
                'sku': m.product_id.default_code,
                'description': m.product_id.name,
                'ordered_qty': m.product_uom_qty,
                'shipped_qty': m.quantity,
                'uom': m.product_uom.name,
            } for m in self.move_ids],
        }
