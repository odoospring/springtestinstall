from odoo import api, fields, models
from odoo.exceptions import ValidationError


class EdiPushEndpoint(models.Model):
    _name = 'edi.push.endpoint'
    _description = 'External Push Endpoint'

    name = fields.Char(required=True)
    url = fields.Char('Endpoint URL', required=True,
                      help='Documents are sent here with HTTP POST as JSON.')
    api_key = fields.Char('API Key', help='Sent as "Authorization: Bearer <key>".')
    timeout = fields.Integer('Timeout (seconds)', default=30)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company,
                                 help='Leave empty to use for all companies.')
    active = fields.Boolean(default=True)

    @api.constrains('url')
    def _check_url(self):
        for rec in self:
            if not (rec.url or '').lower().startswith(('http://', 'https://')):
                raise ValidationError('The endpoint URL must start with http:// or https://')

    @api.model
    def _get_for_company(self, company):
        return (self.search([('company_id', '=', company.id)], limit=1)
                or self.search([('company_id', '=', False)], limit=1))
