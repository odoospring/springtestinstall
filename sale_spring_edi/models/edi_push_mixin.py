import json
import logging

import requests

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class EdiPushMixin(models.AbstractModel):
    _name = 'edi.push.mixin'
    _description = 'Push document to external system'

    edi_push_state = fields.Selection(
        [('not_sent', 'Not sent'), ('sent', 'Sent'), ('error', 'Error')],
        string='External push', default='not_sent', copy=False, readonly=True)
    edi_push_date = fields.Datetime('Last push', copy=False, readonly=True)
    edi_push_message = fields.Text('Last push response', copy=False, readonly=True)

    # --- to be provided by each model -------------------------------------
    def _edi_document_type(self):
        raise NotImplementedError

    def _edi_check_pushable(self):
        """Raise UserError if the document is not in a state to be pushed."""

    def _edi_build_payload(self):
        raise NotImplementedError

    def _edi_handle_response(self, data):
        """Hook: called with the parsed JSON response after a successful push."""

    # --- the button -------------------------------------------------------
    def action_push_to_external(self):
        self.ensure_one()
        if self.edi_push_state == 'sent':
            raise UserError(_('This document was already pushed on %s.',
                              self.edi_push_date))
        self._edi_check_pushable()

        # sudo: normal users may click the button without being able to read keys
        endpoint = self.env['edi.push.endpoint'].sudo()._get_for_company(
            self.company_id, self._edi_document_type())
        if not endpoint:
            raise UserError(_('No external push endpoint is configured for document type '
                              '"%s". Go to Sales > Configuration > External Push Endpoints.',
                              self._edi_document_type()))

        payload = {
            'document_type': self._edi_document_type(),
            'odoo_id': self.id,
            'data': endpoint._filter_payload(self._edi_build_payload()),
        }
        headers = {
            'Content-Type': 'application/json',
            # lets the receiving system reject a repeat of the same document
            'Idempotency-Key': f'{self._name}:{self.id}',
        }
        auth_headers, auth = endpoint._auth_parts()
        headers.update(auth_headers)

        try:
            resp = requests.post(endpoint.url, data=json.dumps(payload, default=str),
                                 headers=headers, auth=auth, timeout=endpoint.timeout or 30)
            ok = resp.ok
            detail = f'HTTP {resp.status_code}: {resp.text[:1000]}'
        except requests.RequestException as e:
            ok = False
            detail = f'Connection error: {str(e)[:1000]}'
            _logger.warning('External push failed for %s,%s: %s', self._name, self.id, e)

        parsed = None
        if ok:
            try:
                parsed = resp.json()
            except ValueError:
                parsed = None

        self.sudo().write({
            'edi_push_state': 'sent' if ok else 'error',
            'edi_push_date': fields.Datetime.now(),
            'edi_push_message': detail,
        })
        self.message_post(body=_('External push %(result)s. %(detail)s',
                                 result=_('succeeded') if ok else _('failed'),
                                 detail=detail))
        if parsed is not None:
            # Never let a problem reading the response undo a push that succeeded.
            try:
                with self.env.cr.savepoint():
                    self._edi_handle_response(parsed)
            except Exception:
                _logger.exception('Could not process push response for %s,%s',
                                  self._name, self.id)
                self.message_post(body=_('The push succeeded, but its response '
                                         'could not be processed.'))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('External push'),
                'message': _('Sent successfully.') if ok else detail,
                'type': 'success' if ok else 'danger',
                'sticky': not ok,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    def action_reset_push_state(self):
        """Allow a deliberate re-push (system administrators only, see view)."""
        self.sudo().write({'edi_push_state': 'not_sent', 'edi_push_message': False})
