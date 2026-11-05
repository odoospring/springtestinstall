from odoo import _, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _name = 'account.move'
    _inherit = ['account.move', 'edi.push.mixin']

    edi_order_number = fields.Char('Order number', copy=False, index=True)
    edi_order_id = fields.Char('order ID', copy=False, index=True)

    def _edi_document_type(self):
        return 'invoice'

    def _edi_check_pushable(self):
        if self.move_type not in ('out_invoice', 'out_refund') or self.state != 'posted':
            raise UserError(_('Only posted customer invoices or credit notes can be pushed.'))

    def _edi_build_payload(self):
        self.ensure_one()
        order = self.line_ids.sale_line_ids.order_id[:1]
        return {
            'invoice_number': self.name,
            'move_type': self.move_type,
            'invoice_date': self.invoice_date and self.invoice_date.isoformat(),
            'due_date': self.invoice_date_due and self.invoice_date_due.isoformat(),
            'currency': self.currency_id.name,
            'customer': {'name': self.partner_id.name, 'ref': self.partner_id.ref},
            'edi_order_number': self.edi_order_number,
            'edi_order_id': self.edi_order_id,
            'ship_to': order._edi_ship_to_dict() if order else {},
            'amount_untaxed': self.amount_untaxed,
            'amount_tax': self.amount_tax,
            'amount_total': self.amount_total,
            'lines': [{
                'sku': l.product_id.default_code,
                'description': l.name,
                'quantity': l.quantity,
                'uom': l.product_uom_id.name,
                'unit_price': l.price_unit,
                'discount': l.discount,
                'subtotal': l.price_subtotal,
            } for l in self.invoice_line_ids.filtered(lambda l: l.display_type == 'product')],
        }
