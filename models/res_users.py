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
        
        # IMPORTANTE: Usamos sudo() aquí para evitar:
        # 1. Recursión infinita (porque search llama a _search, que llama a este método).
        # 2. Bloqueo de seguridad al buscar hijos.
        all_child_locations = self.env['stock.location'].sudo().search([
            ('id', 'child_of', self.allowed_location_ids.ids),
            ('usage', 'in', ['internal', 'transit'])
        ])
        return all_child_locations.ids
    
    def get_all_location_ids_with_access(self):
        """ Calcula todas las ubicaciones visibles (Hijos + Ancestros + Ubicaciones Técnicas) """
        self.ensure_one()
        if not self.has_stock_restriction:
            return []
    
        # 1. Ubicaciones físicas permitidas y sus hijos
        effective_ids = self.get_effective_location_ids()
    
        # 2. Ancestros (Para que Odoo pueda renderizar la jerarquía: Almacén -> Pasillo -> Estante)
        ancestor_locations = self.env['stock.location'].sudo()
        seen = set()
        for loc in self.allowed_location_ids.sudo():
            current = loc
            while current and current.id not in seen:
                seen.add(current.id)
                ancestor_locations |= current
                current = current.location_id
    
        # 3. UBICACIONES TÉCNICAS/FICTICIAS (La solución a tu error)
        # Añadimos ubicaciones de tránsito, vista, proveedores, clientes y producción
        # Estas son necesarias para que los movimientos de stock no den error de acceso.
        technical_locations = self.env['stock.location'].sudo().search([
            '|', '|', '|',
            ('usage', '=', 'view'),          # Ubicaciones tipo Vista (Padres ficticios)
            ('usage', '=', 'transit'),       # Ubicaciones de tránsito
            ('usage', '=', 'inventory'),     # Ajustes de inventario
            ('usage', 'not in', ['internal']) # Proveedores, Clientes, Producción, etc.
        ])
    
        return list(set(effective_ids + ancestor_locations.ids + technical_locations.ids))


    def check_location_access(self, location_id, operation='read'):
        """ Verifica acceso según el tipo de operación """
        self.ensure_one()
        if not self.has_stock_restriction or self.env.su:
            return True
        
        if operation == 'read':
            return location_id in self.get_all_location_ids_with_access()
        
        return location_id in self.get_effective_location_ids()