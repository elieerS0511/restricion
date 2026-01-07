from odoo import models, api, fields, _
from odoo.exceptions import AccessError, UserError
from odoo.osv import expression

class StockLocation(models.Model):
    _inherit = 'stock.location'

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None):
        """ Filtra las ubicaciones para que el usuario solo vea las permitidas + ancestros """
        user = self.env.user
        if not self.env.su and user.has_stock_restriction:
            allowed_ids = user.get_all_location_ids_with_access()
            if allowed_ids:
                domain = expression.AND([domain, [('id', 'in', allowed_ids)]])
        return super()._search(domain, offset=offset, limit=limit, order=order)

    def check_access_rule(self, operation):
        """
        Bypassear reglas de registro para lectura si estamos en la jerarquía permitida.
        Necesario para que Odoo pueda dibujar la ruta (Almacén -> Estante).
        """
        user = self.env.user
        if not self.env.su and user.has_stock_restriction and operation == 'read':
            allowed_ids = user.get_all_location_ids_with_access()
            if self.ids and all(loc_id in allowed_ids for loc_id in self.ids):
                return None
        return super().check_access_rule(operation)

class StockQuant(models.Model):
    _inherit = 'stock.quant'

    @api.model
    def check_access_rights(self, operation, raise_exception=True):
        """ 
        LA PUERTA PRINCIPAL:
        Permite el paso al modelo StockQuant.
        - Para 'read': Deja pasar para que las vistas se carguen (el filtro se hace después).
        - Para 'write/create': Deja pasar para permitir ajustes, la validación se hace a nivel de registro.
        """
        user = self.env.user
        if user.has_stock_restriction:
            # Permitimos el acceso general al modelo.
            # La seguridad granular se delega a _search (lectura) y write/create (escritura).
            return True
        
        return super().check_access_rights(operation, raise_exception=raise_exception)

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None):
        """ EL FILTRO VISUAL: Solo muestra quants en ubicaciones permitidas """
        user = self.env.user
        if not self.env.su and user.has_stock_restriction:
            allowed_ids = user.get_effective_location_ids()
            if allowed_ids:
                # Filtro estricto: solo lo que está fisicamente en mis ubicaciones
                domain = expression.AND([domain, [('location_id', 'in', allowed_ids)]])
            else:
                domain = expression.AND([domain, [('id', '=', 0)]])
        return super()._search(domain, offset=offset, limit=limit, order=order)

    @api.model_create_multi
    def create(self, vals_list):
        """ VALIDACIÓN AL CREAR: No permitir crear stock en ubicaciones ajenas """
        user = self.env.user
        if not self.env.su and user.has_stock_restriction:
            allowed_ids = user.get_effective_location_ids()
            for vals in vals_list:
                if vals.get('location_id') and vals['location_id'] not in allowed_ids:
                    # Buscamos el nombre para el error
                    loc_name = self.env['stock.location'].browse(vals['location_id']).name_get()[0][1]
                    raise AccessError(_('Restricción: No puedes crear inventario en la ubicación %s') % loc_name)
        return super().create(vals_list)

    def write(self, vals):
        """ VALIDACIÓN AL EDITAR: No permitir modificar stock ajeno """
        user = self.env.user
        if not self.env.su and user.has_stock_restriction:
            allowed_ids = user.get_effective_location_ids()
            # 1. Verificar si intentan mover el quant A una ubicación prohibida
            if 'location_id' in vals and vals['location_id'] not in allowed_ids:
                raise AccessError(_('Restricción: No puedes mover inventario hacia una ubicación no permitida.'))
            
            # 2. Verificar que los quants que están editando pertenezcan a ubicaciones permitidas
            for quant in self:
                if quant.location_id.id not in allowed_ids:
                    raise AccessError(_('Restricción: No tienes permiso para modificar inventario en %s') % quant.location_id.display_name)
                    
        return super().write(vals)

    def action_apply_inventory(self):
        """ Refuerzo para el botón de aplicar inventario """
        user = self.env.user
        if not self.env.su and user.has_stock_restriction:
            allowed_ids = user.get_effective_location_ids()
            for quant in self:
                if quant.location_id.id not in allowed_ids:
                    raise AccessError(_("No tienes permiso para ajustar el inventario en: %s") % quant.location_id.display_name)
        return super().action_apply_inventory()

class StockMove(models.Model):
    _inherit = 'stock.move'

    def check_access_rule(self, operation):
        """
        Permite MOVAR mercancía (Albaranes) si al menos una parte de la operación
        (Origen o Destino) está dentro de mi territorio.
        """
        try:
            super().check_access_rule(operation)
        except AccessError:
            user = self.env.user
            if not user.has_stock_restriction:
                raise

            allowed_ids = user.get_effective_location_ids()
            # Si es operación de escritura/creación/borrado
            for move in self:
                is_source_allowed = move.location_id.id in allowed_ids
                is_dest_allowed = move.location_dest_id.id in allowed_ids
                
                # Lógica de la "Puerta Giratoria":
                # Si saco de mi almacén (Source OK) -> Permitido (Venta/Salida)
                # Si meto a mi almacén (Dest OK) -> Permitido (Compra/Retorno)
                if not (is_source_allowed or is_dest_allowed):
                    raise AccessError(_(
                        'No tienes permiso para procesar movimientos que no involucren tus ubicaciones permitidas.\n'
                        'Movimiento: %s -> %s'
                    ) % (move.location_id.name, move.location_dest_id.name))
            # Si pasa el bucle, es válido
            return None