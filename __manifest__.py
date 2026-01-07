{
    'name': 'Restricion de Almacenes y Ubicaciones',
    'version': '18.0.1.0.0',
    'category': 'Inventory/Security',
    'summary': 'Restricción de almacenes y ubicaciones vía Python (Sin Record Rules)',
    'author': '',
    'depends': ['base', 'stock', 'sale_management', 'purchase'],
    'data': [
        'views/res_users_views.xml',
        'views/sale_order_views.xml',
        'security/security.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}