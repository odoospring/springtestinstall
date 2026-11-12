import base64
import logging
import re

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_KEY_RE = re.compile(r'document_key/([^/?#\s]+)')


def _optional(rec, name):
    """Value of a field that only exists when an optional module is installed
    (e.g. carrier / tracking come from the Delivery module)."""
    return getattr(rec, name) if name in rec._fields else None


def _iter_docs(obj):
    """Find every {document_type, response_data} block anywhere in the response."""
    if isinstance(obj, dict):
        if 'document_type' in obj and 'response_data' in obj:
            yield obj
        for value in obj.values():
            yield from _iter_docs(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _iter_docs(value)


def _extract_label_keys(data):
    """Document keys of the shipping labels in a push response, in order."""
    keys = []
    for doc in _iter_docs(data):
        if doc.get('document_type') != 'shipping_label':
            continue
        entries = doc.get('response_data') or []
        if isinstance(entries, str):
            entries = [entries]
        for entry in entries:
            if not isinstance(entry, str):
                continue
            match = _KEY_RE.search(entry)
            key = match.group(1) if match else entry.rstrip('/').rsplit('/', 1)[-1]
            key = key.replace(',', '')
            if key and key not in keys:
                keys.append(key)
    return keys


def _find_first(obj, key):
    """First non-empty value stored under `key` anywhere in a JSON structure."""
    if isinstance(obj, dict):
        if obj.get(key):
            return obj[key]
        children = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for child in children:
        found = _find_first(child, key)
        if found:
            return found
    return None


def _label_file_type(content, content_type):
    if content.startswith(b'%PDF'):
        return 'pdf', 'application/pdf'
    if content.startswith(b'\x89PNG'):
        return 'png', 'image/png'
    if content.startswith(b'GIF8'):
        return 'gif', 'image/gif'
    if content.startswith(b'\xff\xd8'):
        return 'jpg', 'image/jpeg'
    if content.lstrip().startswith(b'^XA'):
        return 'zpl', 'text/plain'
    return 'bin', content_type or 'application/octet-stream'


class StockPicking(models.Model):
    _name = 'stock.picking'
    _inherit = ['stock.picking', 'edi.push.mixin']

    # Read-only mirrors of the sales order values. Stored so they can be
    # searched, grouped and exported, and kept in sync if the order changes.
    edi_order_number = fields.Char('Order number', related='sale_id.edi_order_number',
                                   store=True, readonly=True, index=True)
    edi_order_id = fields.Char('order ID', related='sale_id.edi_order_id',
                               store=True, readonly=True, index=True)
    edi_tracking_number = fields.Char(
        'Tracking number', copy=False, readonly=True,
        help='Sent with the shipment. Filled from the response if left empty.')
    edi_carrier_code = fields.Char(
        'Carrier code', copy=False, readonly=True, help='Carrier code to send, e.g. UPS.')
    edi_label_keys = fields.Char('Label document keys', copy=False, readonly=True,
                                 help='Comma-separated keys returned by the external '
                                      'system when the shipment was pushed.')
    edi_label_count = fields.Integer('Labels', compute='_compute_edi_label_count')

    @api.depends('edi_label_keys')
    def _compute_edi_label_count(self):
        for rec in self:
            rec.edi_label_count = len([k for k in (rec.edi_label_keys or '').split(',') if k])

    def _edi_document_type(self):
        return 'delivery'

    def _edi_check_pushable(self):
        if self.state in ('draft', 'cancel'):
            raise UserError(_('Confirm the transfer before pushing it '
                              '(draft and cancelled transfers cannot be pushed).'))

    def _edi_package_payload(self):
        """Packages for the payload. Items put in a pack in Odoo keep their pack;
        anything not in a pack is placed together in one default carton."""
        packages, unpacked = {}, []
        lines = [l for l in self.move_line_ids if l.quantity]
        for ml in lines:
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
        if not lines:
            # Nothing reserved yet (e.g. the transfer is still Waiting): use the demand.
            for m in self.move_ids:
                if m.state != 'cancel' and m.product_uom_qty:
                    unpacked.append({
                        'sku': m.product_id.default_code,
                        'description': m.product_id.name,
                        'quantity': m.product_uom_qty,
                        'uom': m.product_uom.name,
                        'lot': None,
                    })
        result = list(packages.values())
        if unpacked:
            result.append({
                'package_number': f'{self.name}-{len(result) + 1}',
                'package_type': None,
                'weight': None,
                'items': unpacked,
            })
        for number, package in enumerate(result, 1):
            package['sequence'] = number
        return result

    def _edi_build_payload(self):
        self.ensure_one()
        order = self.sale_id
        packages = self._edi_package_payload()
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
            'carrier_code': self.edi_carrier_code or None,
            'tracking_number': (self.edi_tracking_number
                                or _optional(self, 'carrier_tracking_ref') or None),
            'lines': [{
                'sku': m.product_id.default_code,
                'description': m.product_id.name,
                'ordered_qty': m.product_uom_qty,
                'shipped_qty': m.quantity,
                'uom': m.product_uom.name,
            } for m in self.move_ids],
            'packages': packages,
        }

    def _edi_handle_response(self, data):
        vals = {}
        keys = _extract_label_keys(data)
        if keys:
            vals['edi_label_keys'] = ','.join(keys)
        tracking = _find_first(data, 'ship_info_tracking')
        if tracking and not self.edi_tracking_number:
            vals['edi_tracking_number'] = str(tracking)
        carrier = _find_first(data, 'ship_info_carrier_code')
        if carrier and not self.edi_carrier_code:
            vals['edi_carrier_code'] = str(carrier)
        if vals:
            self.sudo().write(vals)
        warnings = data.get('warnings') if isinstance(data, dict) else None
        if warnings:
            self.message_post(body=_('Warnings from the external system: %s',
                                     '; '.join(str(w) for w in warnings)))

    def _edi_fetch_label(self, endpoint, key, index=1, total=1):
        """Download one label and keep it as an attachment on the transfer."""
        Attachment = self.env['ir.attachment'].sudo()
        tag = f'EDI label {key}'
        existing = Attachment.search([
            ('res_model', '=', self._name), ('res_id', '=', self.id),
            ('description', '=', tag)], limit=1)
        if existing:
            return existing

        url = endpoint._label_url(key)
        headers, auth = ({}, None)
        if endpoint.label_send_auth:
            headers, auth = endpoint._auth_parts()
        try:
            resp = requests.get(url, headers=headers, auth=auth,
                                timeout=endpoint.timeout or 30)
        except requests.RequestException as e:
            raise UserError(_('Could not download the label: %s', e))
        if not resp.ok:
            raise UserError(_('The label download failed (HTTP %(code)s).\nURL: %(url)s',
                              code=resp.status_code, url=url))
        content = resp.content
        content_type = (resp.headers.get('Content-Type') or '').split(';')[0].strip().lower()
        if not content or content_type == 'text/html':
            raise UserError(_(
                'The label URL returned a web page instead of a document. It may require '
                'a login: enable "Send credentials when downloading labels" on the '
                'endpoint, or check the URL.\nURL: %s', url))
        ext, mimetype = _label_file_type(content, content_type)
        suffix = f'-{index}' if total > 1 else ''
        return Attachment.create({
            'name': f"Shipping label {(self.name or '').replace('/', '-')}{suffix}.{ext}",
            'datas': base64.b64encode(content),
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': mimetype,
            'description': tag,
        })

    def action_get_shipping_labels(self):
        self.ensure_one()
        keys = [k for k in (self.edi_label_keys or '').split(',') if k]
        if not keys:
            raise UserError(_('No shipping label was returned for this transfer.'))
        endpoint = self.env['edi.push.endpoint'].sudo()._get_for_company(
            self.company_id, self._edi_document_type())
        if not endpoint or not endpoint.label_url_base:
            raise UserError(_('Set the "Label download URL" on the delivery endpoint '
                              '(Sales > Configuration > External Push Endpoints).'))
        ids = [self._edi_fetch_label(endpoint, key, i, len(keys)).id
               for i, key in enumerate(keys, 1)]
        if len(ids) == 1:
            return {'type': 'ir.actions.act_url', 'target': 'new',
                    'url': f'/web/content/{ids[0]}?download=false'}
        return {
            'type': 'ir.actions.act_window', 'name': _('Shipping labels'),
            'res_model': 'ir.attachment', 'view_mode': 'tree,form',
            'domain': [('id', 'in', ids)],
        }
