from odoo import models, api, fields, _
from odoo.exceptions import UserError, AccessError

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.model
    def default_get(self, fields_list):
        """ 
        Pre-seleccionar un almacén permitido por defecto al crear la venta.
        """
        defaults = super().default_get(fields_list)
        if 'warehouse_id' in fields_list:
            user = self.env.user
            if user.has_stock_restriction and user.allowed_warehouse_ids:
                defaults['warehouse_id'] = user.allowed_warehouse_ids[0].id
        return defaults

    @api.onchange('warehouse_id')
    def _onchange_warehouse_restriction(self):
        """ Advertencia visual en tiempo real """
        if self.warehouse_id:
            user = self.env.user
            if user.has_stock_restriction and user.allowed_warehouse_ids:
                if self.warehouse_id.id not in user.allowed_warehouse_ids.ids:
                    return {
                        'warning': {
                            'title': _("Almacén Restringido"),
                            'message': _("No tienes permiso para vender desde el almacén %s. Se restablecerá al permitido.") % self.warehouse_id.name
                        },
                        'value': {'warehouse_id': user.allowed_warehouse_ids[0].id}
                    }

    def action_confirm(self):
        """ Validación backend estricta antes de confirmar la venta. """
        user = self.env.user
        if not self.env.su and user.has_stock_restriction and user.allowed_warehouse_ids:
            if self.warehouse_id.id not in user.allowed_warehouse_ids.ids:
                raise AccessError(_(
                    "Error de Seguridad: Intentas confirmar una venta desde el almacén '%s', "
                    "pero solo tienes acceso a: %s."
                ) % (self.warehouse_id.name, ', '.join(user.allowed_warehouse_ids.mapped('name'))))
        
        return super().action_confirm()