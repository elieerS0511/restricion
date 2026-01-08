from odoo import models, api, fields, _
from odoo.exceptions import AccessError, UserError
from odoo.osv import expression

# ==============================================================================
# Modelo: stock.location
# ==============================================================================
class StockLocation(models.Model):
    """
    Hereda de 'stock.location' para controlar la VISIBILIDAD de las ubicaciones.
    El objetivo principal es que en las listas, búsquedas y menús desplegables,
    el usuario solo pueda ver las ubicaciones a las que tiene acceso.
    """
    _inherit = 'stock.location'

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None):
        """
        Sobrescribe el método de búsqueda (`_search`) que Odoo usa internamente
        para todas las operaciones de lectura y listado de registros.

        Esta modificación es el "primer filtro" de seguridad. Inyecta un dominio
        adicional para asegurar que cualquier búsqueda de ubicaciones solo devuelva
        aquellas que el usuario tiene permitido ver.
        """
        user = self.env.user
        # La regla solo se aplica a usuarios normales (no al superusuario) que tengan
        # la restricción de inventario activada.
        if not self.env.su and user.has_stock_restriction:
            # Obtenemos la lista COMPLETA de ubicaciones visibles (operativas + ancestros + técnicas)
            # del método que definimos en `res.users`.
            allowed_ids = user.get_all_location_ids_with_access()
            if allowed_ids:
                # `expression.AND` es la forma correcta de añadir nuestra regla al dominio de búsqueda original.
                # Esto asegura que se respeten los filtros que el usuario pueda haber aplicado en la interfaz,
                # además de nuestra restricción de seguridad.
                # La regla es simple: 'el ID de la ubicación debe estar en mi lista de permitidos'.
                domain = expression.AND([domain, [('id', 'in', allowed_ids)]])

        # Continuamos con la búsqueda original, pero ahora con nuestro dominio de seguridad inyectado.
        return super()._search(domain, offset=offset, limit=limit, order=order)

    def check_access_rule(self, operation):
        """
        Refuerza las reglas de acceso a nivel de registro. Mientras `_search` filtra
        listas, este método se activa cuando se intenta acceder a un registro específico.
        """
        user = self.env.user
        # Solo aplicamos lógica extra para usuarios restringidos en operaciones de lectura.
        # Para 'write', 'create', 'unlink', dejamos que Odoo y los métodos de StockQuant se encarguen.
        if not self.env.su and user.has_stock_restriction and operation == 'read':
            allowed_ids = user.get_all_location_ids_with_access()
            # Si el usuario está intentando leer ubicaciones, verificamos que TODAS
            # las IDs solicitadas estén dentro de su lista de permitidas.
            if self.ids and all(loc_id in allowed_ids for loc_id in self.ids):
                # Si todo está en orden, no hacemos nada y dejamos que Odoo continúe.
                return None

        # Si no se cumple la condición o la operación es otra, usamos la lógica de acceso estándar de Odoo.
        return super().check_access_rule(operation)

# ==============================================================================
# Modelo: stock.quant
# ==============================================================================
class StockQuant(models.Model):
    """
    Hereda de 'stock.quant'. Este es el corazón de la seguridad del inventario,
    ya que los "quants" representan el stock físico de un producto en una ubicación.
    Aquí controlamos quién puede ver, crear, mover o modificar el stock.
    """
    _inherit = 'stock.quant'

    @api.model
    def check_access_rights(self, operation, raise_exception=True):
        """
        LA PUERTA DE ENTRADA AL MODELO.

        Este método se llama antes que cualquier otro para verificar si el usuario
        tiene siquiera permiso para interactuar con el modelo 'stock.quant'.

        La estrategia aquí es ser permisivos. Dejamos la puerta abierta para que el
        usuario pueda entrar, pero luego, en los métodos `_search`, `write` y `create`,
        implementamos la seguridad granular a nivel de registro.

        ¿Por qué? Si bloqueáramos el acceso aquí (ej., para 'read'), muchas vistas
        de Odoo que dependen indirectamente de los quants (como la disponibilidad
        en la ficha del producto) se romperían con errores de acceso.
        """
        user = self.env.user
        if user.has_stock_restriction:
            # Para usuarios restringidos, permitimos el acceso general al MODELO.
            # La seguridad real se aplicará más adelante, registro por registro.
            return True

        # Para usuarios no restringidos, usamos el comportamiento estándar de Odoo.
        return super().check_access_rights(operation, raise_exception=raise_exception)

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None):
        """
        EL FILTRO VISUAL: Solo muestra los quants de las ubicaciones permitidas.

        Similar a `StockLocation._search`, este método asegura que cuando un usuario
        vea una lista de quants (ej., en un informe de inventario), solo aparezcan
        aquellos que están FÍSICAMENTE en las ubicaciones donde puede operar.
        """
        user = self.env.user
        if not self.env.su and user.has_stock_restriction:
            # Aquí usamos `get_effective_location_ids`, la lista RESTRICTIVA,
            # porque solo queremos mostrar el stock que el usuario puede gestionar.
            allowed_ids = user.get_effective_location_ids()
            if allowed_ids:
                # Añadimos la regla: 'la ubicación del quant debe estar en mi lista de permitidos'.
                domain = expression.AND([domain, [('location_id', 'in', allowed_ids)]])
            else:
                # Si el usuario tiene restricciones pero no se le asignó ninguna ubicación,
                # no debe ver NINGÚN quant. Forzamos un dominio que nunca será verdadero.
                domain = expression.AND([domain, [('id', '=', 0)]])

        return super()._search(domain, offset=offset, limit=limit, order=order)

    @api.model_create_multi
    def create(self, vals_list):
        """
        VALIDACIÓN AL CREAR: Evita crear stock en ubicaciones prohibidas.

        Este método se activa al crear nuevos quants (ej., en un ajuste de inventario).
        Verifica que la ubicación del nuevo stock sea una a la que el usuario tiene acceso.
        """
        user = self.env.user
        if not self.env.su and user.has_stock_restriction:
            allowed_ids = user.get_effective_location_ids()
            for vals in vals_list:
                # Si están intentando asignar una 'location_id' y esa ID no está permitida...
                if vals.get('location_id') and vals['location_id'] not in allowed_ids:
                    # ...bloqueamos la operación con un error claro.
                    # Buscamos el nombre de la ubicación para que el mensaje sea más útil.
                    loc_name = self.env['stock.location'].browse(vals['location_id']).name_get()[0][1]
                    raise AccessError(_('Restricción: No puedes crear inventario en la ubicación %s') % loc_name)
        return super().create(vals_list)

    def write(self, vals):
        """
        VALIDACIÓN AL EDITAR/MOVER: Evita modificar o mover stock ajeno.

        Este método es crucial y tiene dos puntos de control:
        1. Al MOVER stock: ¿El destino es una ubicación permitida?
        2. Al MODIFICAR stock: ¿El stock que se está modificando ya está en una
           ubicación permitida?
        """
        user = self.env.user
        if not self.env.su and user.has_stock_restriction:
            allowed_ids = user.get_effective_location_ids()

            # Punto de control 1: Movimiento HACIA una nueva ubicación.
            if 'location_id' in vals and vals['location_id'] not in allowed_ids:
                raise AccessError(_('Restricción: No puedes mover inventario hacia una ubicación no permitida.'))

            # Punto de control 2: Modificación de quants existentes.
            # Recorremos cada quant que se intenta modificar.
            for quant in self:
                # Si la ubicación actual del quant no está en la lista de permitidos...
                if quant.location_id.id not in allowed_ids:
                    # ...lanzamos un error. Esto evita que alguien modifique el stock
                    # de un almacén vecino, por ejemplo.
                    raise AccessError(_('Restricción: No tienes permiso para modificar inventario en %s') % quant.location_id.display_name)

        return super().write(vals)

    def action_apply_inventory(self):
        """
        Refuerzo de seguridad para el botón "Aplicar Inventario".

        Esta acción es un atajo para `write`, por lo que necesita la misma
        protección. Nos aseguramos de que el usuario solo pueda aplicar ajustes
        de inventario en las ubicaciones que le corresponden.
        """
        user = self.env.user
        if not self.env.su and user.has_stock_restriction:
            allowed_ids = user.get_effective_location_ids()
            for quant in self:
                if quant.location_id.id not in allowed_ids:
                    raise AccessError(_("No tienes permiso para ajustar el inventario en: %s") % quant.location_id.display_name)
        return super().action_apply_inventory()

# ==============================================================================
# Modelo: stock.move
# ==============================================================================
class StockMove(models.Model):
    """
    Hereda de 'stock.move' para controlar los MOVIMIENTOS de inventario (albaranes).
    """
    _inherit = 'stock.move'

    def check_access_rule(self, operation):
        """
        Implementa la lógica de la "Puerta Giratoria".

        Un movimiento de stock tiene un origen y un destino. Un usuario restringido
        puede procesar un movimiento SIEMPRE Y CUANDO esté involucrado en al menos
        uno de los extremos de la operación.

        Ejemplos:
        - Venta: Origen (MI almacén) -> Destino (Cliente). Permitido, porque el origen me pertenece.
        - Compra: Origen (Proveedor) -> Destino (MI almacén). Permitido, porque el destino me pertenece.
        - Transferencia interna ajena: Origen (Almacén A) -> Destino (Almacén B). No permitido.
        """
        try:
            # Primero, intentamos la regla de acceso estándar de Odoo.
            super().check_access_rule(operation)
        except AccessError:
            # Si Odoo lanza un error de acceso, es nuestra oportunidad de aplicar nuestra lógica personalizada.
            user = self.env.user
            # Si el usuario no tiene restricciones, el error original era válido, así que lo relanzamos.
            if not user.has_stock_restriction:
                raise

            allowed_ids = user.get_effective_location_ids()

            for move in self:
                # Verificamos si el origen o el destino del movimiento están en las ubicaciones permitidas.
                is_source_allowed = move.location_id.id in allowed_ids
                is_dest_allowed = move.location_dest_id.id in allowed_ids

                # Si el usuario NO está ni en el origen NI en el destino, es un movimiento ajeno.
                if not (is_source_allowed or is_dest_allowed):
                    # Bloqueamos la operación con un error claro.
                    raise AccessError(_(
                        'No tienes permiso para procesar movimientos que no involucren tus ubicaciones permitidas.\n'
                        'Movimiento: %s -> %s'
                    ) % (move.location_id.name, move.location_dest_id.name))

            # Si el bucle termina sin lanzar un error, significa que todos los movimientos son válidos.
            # Devolvemos None para indicar que el acceso está permitido.
            return None
