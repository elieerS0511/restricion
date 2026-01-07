from odoo import models, fields, api

class ResUsers(models.Model):
    _inherit = 'res.users'

    restrict_stock_access = fields.Boolean(
        string="Restringir Acceso a Inventario", 
        default=False
    )
    allowed_location_ids = fields.Many2many(
        'stock.location', 
        string="Ubicaciones Permitidas Manualmente"
    )
    allowed_warehouse_ids = fields.Many2many(
        'stock.warehouse', 
        string="Almacenes Permitidos"
    )

    has_stock_restriction = fields.Boolean(
        string="Tiene Restricción de Inventario",
        compute='_compute_has_stock_restriction', 
        store=False
    )

    @api.depends('restrict_stock_access', 'allowed_location_ids')
    def _compute_has_stock_restriction(self):
        for user in self:
            user.has_stock_restriction = user.restrict_stock_access and user.allowed_location_ids

    def get_effective_location_ids(self):
        """ Retorna IDs permitidos y sus hijos para OPERACIONES (Escritura/Conteo) """
        self.ensure_one()
        if not self.restrict_stock_access or not self.allowed_location_ids:
            return []
        
        all_child_locations = self.env['stock.location'].search([
            ('id', 'child_of', self.allowed_location_ids.ids),
            ('usage', 'in', ['internal', 'transit'])
        ])
        return all_child_locations.ids
    
    def get_all_location_ids_with_access(self):
        self.ensure_one()
        if not self.has_stock_restriction:
            return []
    
        effective_ids = self.get_effective_location_ids()
    
        # Ancestros (incluye permitidas y padres)
        ancestor_locations = self.env['stock.location']
        seen = set()
        for loc in self.allowed_location_ids:
            current = loc
            while current and current.id not in seen:
                seen.add(current.id)
                ancestor_locations |= current
                current = current.location_id
    
        # Vista y externas
        other_locations = self.env['stock.location'].search([
            '|', ('usage', '=', 'view'),
            ('usage', 'not in', ['internal', 'transit'])
        ])
    
        return list(set(effective_ids + ancestor_locations.ids + other_locations.ids))


    def check_location_access(self, location_id, operation='read'):
        """ Verifica acceso según el tipo de operación """
        self.ensure_one()
        if not self.has_stock_restriction or self.env.su:
            return True
        
        if operation == 'read':
            return location_id in self.get_all_location_ids_with_access()
        
        return location_id in self.get_effective_location_ids()