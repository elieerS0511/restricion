from odoo import models, api, fields, _
from odoo.exceptions import UserError, AccessError

class SaleOrder(models.Model):
    """
    Hereda del modelo 'sale.order' para integrar las reglas de seguridad de inventario
    directamente en el flujo de trabajo de ventas.

    El objetivo es asegurar que un usuario con restricciones de inventario solo pueda
    crear y confirmar pedidos de venta desde los almacenes a los que tiene
    permiso explícito.
    """
    _inherit = 'sale.order'

    @api.model
    def default_get(self, fields_list):
        """
        Sobrescribe el método `default_get` para mejorar la experiencia de usuario.

        Cuando un usuario con restricciones crea un nuevo pedido de venta, este método
        pre-selecciona automáticamente el primer almacén de su lista de almacenes permitidos.
        Esto evita que el usuario tenga que seleccionarlo manualmente y reduce la
        posibilidad de errores.
        """
        # Obtenemos los valores por defecto estándar de Odoo.
        defaults = super().default_get(fields_list)

        # Solo nos interesa actuar si el campo 'warehouse_id' está siendo solicitado.
        if 'warehouse_id' in fields_list:
            user = self.env.user
            # Comprobamos si el usuario actual tiene la restricción activada
            # y si tiene al menos un almacén permitido asignado.
            if user.has_stock_restriction and user.allowed_warehouse_ids:
                # Si cumple las condiciones, asignamos el primer almacén de la lista como valor por defecto.
                defaults['warehouse_id'] = user.allowed_warehouse_ids[0].id
        return defaults

    @api.onchange('warehouse_id')
    def _onchange_warehouse_restriction(self):
        """
        Proporciona una validación visual e inmediata en la interfaz de usuario.

        Este método se dispara cada vez que el usuario cambia el almacén en el formulario
        de un pedido de venta. Si el usuario selecciona un almacén al que no tiene acceso,
        el sistema muestra una advertencia emergente y revierte el cambio al primer
        almacén permitido.

        Esto evita que el usuario avance con una selección incorrecta.
        """
        # Nos aseguramos de que haya un almacén seleccionado para evitar errores.
        if self.warehouse_id:
            user = self.env.user
            # Verificamos si el usuario tiene restricciones y almacenes permitidos.
            if user.has_stock_restriction and user.allowed_warehouse_ids:
                # La condición clave: ¿El almacén seleccionado NO está en la lista de permitidos?
                if self.warehouse_id.id not in user.allowed_warehouse_ids.ids:
                    # Si no está permitido, devolvemos un diccionario especial para Odoo.
                    # Esto genera una ventana de advertencia y, al mismo tiempo,
                    # fuerza un cambio en el valor del campo 'warehouse_id'.
                    return {
                        'warning': {
                            'title': _("Almacén Restringido"),
                            'message': _("No tienes permiso para vender desde el almacén %s. Se restablecerá al permitido.") % self.warehouse_id.name
                        },
                        # 'value' le dice a la interfaz que cambie el valor del campo 'warehouse_id'
                        # al primer almacén que el usuario sí tiene permitido.
                        'value': {'warehouse_id': user.allowed_warehouse_ids[0].id}
                    }

    def action_confirm(self):
        """
        Añade una capa de seguridad final y estricta en el lado del servidor.

        Este método se ejecuta cuando el usuario hace clic en el botón "Confirmar".
        Aunque las validaciones 'onchange' son útiles, un usuario avanzado podría
        intentar saltárselas. Esta validación en el backend es la barrera final.

        Si, por alguna razón, el almacén en el pedido de venta no está permitido
        para el usuario, la operación se bloquea por completo lanzando un `AccessError`.
        """
        user = self.env.user

        # La validación se aplica si el usuario no es el superusuario, tiene restricciones
        # y una lista de almacenes permitidos.
        if not self.env.su and user.has_stock_restriction and user.allowed_warehouse_ids:
            # Comprobamos si el almacén del pedido NO está en la lista de permitidos.
            if self.warehouse_id.id not in user.allowed_warehouse_ids.ids:
                # Si no lo está, detenemos la ejecución y mostramos un error claro.
                # `AccessError` es el tipo de excepción correcto para problemas de permisos.
                raise AccessError(_(
                    "Error de Seguridad: Intentas confirmar una venta desde el almacén '%s', "
                    "pero solo tienes acceso a: %s."
                ) % (self.warehouse_id.name, ', '.join(user.allowed_warehouse_ids.mapped('name'))))

        # Si todas las validaciones pasan, llamamos al método original `action_confirm`
        # para que el pedido continúe su flujo normal.
        return super().action_confirm()
