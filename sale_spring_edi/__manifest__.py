{
    'name': 'Sale Order - Spring Systems EDI Info',
    'version': '17.0.1.11.0',
    'summary': 'Adds EDI header fields and a "Spring Systems EDI info" tab to sales orders',
    'category': 'Sales',
    'author': 'Your Company',
    'license': 'OPL-1',
    'depends': ['sale', 'sale_stock', 'stock_delivery', 'account'],
    'data': [
        'security/ir.model.access.csv',
        'views/sale_order_views.xml',
        'views/stock_account_views.xml',
        'views/edi_push_views.xml',
    ],
    'installable': True,
    'application': False,
}
