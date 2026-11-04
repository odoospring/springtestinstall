from odoo import api, fields, models
from odoo.exceptions import ValidationError


class EdiPushEndpoint(models.Model):
    _name = 'edi.push.endpoint'
    _description = 'External Push Endpoint'

    name = fields.Char(required=True)
    document_type = fields.Selection([
        ('all', 'All documents'),
        ('invoice', 'Invoices'),
        ('delivery', 'Deliveries / stock transfers'),
    ], required=True, default='all',
        help='Which documents are sent to this endpoint. A specific type '
             'takes priority over "All documents".')
    url = fields.Char('Endpoint URL', required=True,
                      help='Documents are sent here with HTTP POST as JSON.')
    auth_type = fields.Selection([
        ('none', 'None'),
        ('bearer', 'Bearer token (API key)'),
        ('basic', 'Basic (username and password)'),
    ], string='Authentication', required=True, default='bearer')
    api_key = fields.Char('API Key', help='Sent as "Authorization: Bearer <key>".')
    username = fields.Char('Username')
    password = fields.Char('Password')
    timeout = fields.Integer('Timeout (seconds)', default=30)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company,
                                 help='Leave empty to use for all companies.')
    active = fields.Boolean(default=True)

    @api.constrains('url')
    def _check_url(self):
        for rec in self:
            if not (rec.url or '').lower().startswith(('http://', 'https://')):
                raise ValidationError('The endpoint URL must start with http:// or https://')

    @api.constrains('auth_type', 'username')
    def _check_basic_auth(self):
        for rec in self:
            if rec.auth_type == 'basic' and not rec.username:
                raise ValidationError('Basic authentication requires a username.')

    @api.constrains('document_type', 'company_id', 'active')
    def _check_one_endpoint_per_type(self):
        for rec in self.filtered('active'):
            dup = self.search([
                ('id', '!=', rec.id),
                ('document_type', '=', rec.document_type),
                ('company_id', '=', rec.company_id.id or False),
            ], limit=1)
            if dup:
                raise ValidationError(
                    'Only one active endpoint is allowed per document type and company '
                    '(already used by "%s").' % dup.name)

    @api.model
    def _get_for_company(self, company, document_type):
        """Best match: a specific document type beats "All documents";
        within that, a company-specific endpoint beats a shared one."""
        candidates = self.search([
            ('document_type', 'in', [document_type, 'all']),
            ('company_id', 'in', [company.id, False]),
        ])
        ranked = candidates.sorted(key=lambda e: (
            0 if e.document_type == document_type else 1,
            0 if e.company_id else 1,
        ))
        return ranked[:1]
