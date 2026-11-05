from odoo import _, fields, models
from odoo.exceptions import UserError


def _optional(rec, name):
    """Value of a field that only exists when an optional module is installed
    (e.g. carrier / tracking come from the Delivery module)."""
    return getattr(rec, name) if name in rec._fields else None


class StockPicking(models.Model):
    _name = 'stock.picking'
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

    def _edi_package_payload(self):
        """Group the done move lines by the package they were put in.
        Returns (packages, unpacked_items)."""
        packages, unpacked = {}, []
        for ml in [l for l in self.move_line_ids if l.quantity]:
            item = {
                'sku': ml.product_id.default_code,
                'description': ml.product_id.name,
                'quantity': ml.quantity,
                'uom': ml.product_uom_id.name,
                'lot': ml.lot_id.name or None,
            }
            pack = ml.result_package_id
            if not pack:
                unpacked.append(item)
                continue
            entry = packages.setdefault(pack.id, {
                'package_number': pack.name,
                'package_type': pack.package_type_id.name or None,
                'weight': _optional(pack, 'shipping_weight'),
                'items': [],
            })
            entry['items'].append(item)
        return list(packages.values()), unpacked

    def _edi_build_payload(self):
        self.ensure_one()
        order = self.sale_id
        packages, unpacked = self._edi_package_payload()
        carrier = _optional(self, 'carrier_id')
        return {
            'delivery_number': self.name,
            'origin': self.origin,
            'scheduled_date': self.scheduled_date and self.scheduled_date.isoformat(),
            'date_done': self.date_done and self.date_done.isoformat(),
            'customer': {'name': self.partner_id.name, 'ref': self.partner_id.ref},
            'edi_order_number': self.edi_order_number,
            'edi_order_id': self.edi_order_id,
            'ship_to': order._edi_ship_to_dict() if order else {},
            'carrier': carrier.name if carrier else None,
            'tracking_number': _optional(self, 'carrier_tracking_ref') or None,
            'lines': [{
                'sku': m.product_id.default_code,
                'description': m.product_id.name,
                'ordered_qty': m.product_uom_qty,
                'shipped_qty': m.quantity,
                'uom': m.product_uom.name,
            } for m in self.move_ids],
            'packages': packages,
            'unpacked_items': unpacked,
        }
