from odoo import models, fields, api

class ResUsers(models.Model):
    """
    Hereda del modelo 'res.users' para añadir campos y métodos relacionados con la seguridad
    y las restricciones de acceso al inventario (módulo 'stock').

    El objetivo principal es permitir que un administrador pueda configurar a qué almacenes
    y ubicaciones de inventario puede acceder un usuario específico.
    """
    _inherit = 'res.users'

    # --- Campos para la configuración de la restricción ---

    # Este campo es el "interruptor" principal. Si está activado,
    # el sistema empezará a aplicar las reglas de restricción para este usuario.
    restrict_stock_access = fields.Boolean(
        string="Restringir Acceso a Inventario",
        default=False,
        help="""
        Marca esta casilla para activar las restricciones de acceso al inventario para este usuario.
        Si no está marcada, el usuario tendrá el comportamiento estándar de Odoo.
        """
    )

    # Aquí es donde el administrador asigna explícitamente las ubicaciones de inventario
    # a las que el usuario tendrá permiso. Es un campo Many2many, lo que permite
    # seleccionar múltiples ubicaciones.
    allowed_location_ids = fields.Many2many(
        'stock.location',
        string="Ubicaciones Permitidas Manualmente",
        help="""
        Define las ubicaciones de inventario específicas a las que este usuario tendrá acceso.
        El sistema también le dará acceso a todas las ubicaciones hijas de las seleccionadas.
        """
    )

    # Este campo permite asignar almacenes completos. Al seleccionar un almacén,
    # Odoo podría (dependiendo de la lógica implementada) conceder acceso a todas las
    # ubicaciones dentro de ese almacén.
    allowed_warehouse_ids = fields.Many2many(
        'stock.warehouse',
        string="Almacenes Permitidos",
        help="Define los almacenes a los que el usuario tendrá acceso para operaciones de venta."
    )

    # --- Campo computado para verificar rápidamente si hay restricciones ---

    # Este es un campo "calculado". Su valor (True/False) no se guarda en la base de datos,
    # sino que se determina en tiempo real a través del método _compute_has_stock_restriction.
    # Es útil para comprobaciones rápidas en otras partes del código.
    has_stock_restriction = fields.Boolean(
        string="Tiene Restricción de Inventario",
        compute='_compute_has_stock_restriction',
        store=False, # 'store=False' significa que el valor no se almacena en la BD.
        help="Indica si el usuario tiene alguna restricción de inventario activa."
    )

    @api.depends('restrict_stock_access', 'allowed_location_ids')
    def _compute_has_stock_restriction(self):
        """
        Calcula el valor del campo 'has_stock_restriction'.

        Un usuario tiene una restricción activa si y solo si:
        1. El interruptor 'restrict_stock_access' está activado.
        2. Se le ha asignado al menos una ubicación en 'allowed_location_ids'.
        """
        for user in self:
            # La condición es clara: el booleano debe estar activo y la lista de ubicaciones no debe estar vacía.
            user.has_stock_restriction = user.restrict_stock_access and user.allowed_location_ids

    def get_effective_location_ids(self):
        """
        Calcula y devuelve la lista de IDs de ubicación a las que el usuario tiene
        acceso para realizar operaciones de escritura (como mover stock, contar inventario, etc.).

        Esta lista incluye:
        - Las ubicaciones asignadas directamente en 'allowed_location_ids'.
        - Todas las ubicaciones "hijas" de esas ubicaciones.

        Returns:
            list: Una lista de IDs de las ubicaciones de tipo 'internal' o 'transit' permitidas.
                  Retorna una lista vacía si el usuario no tiene restricciones.
        """
        self.ensure_one() # Asegura que el método se ejecuta para un solo usuario a la vez.

        # Si el usuario no tiene restricciones, no hay nada que calcular.
        # Devolvemos una lista vacía, que en las reglas de dominio significa "sin restricciones".
        if not self.restrict_stock_access or not self.allowed_location_ids:
            return []

        # --- IMPORTANTE: El uso de sudo() ---
        # Usamos `sudo()` para elevar los privilegios temporalmente durante esta búsqueda.
        # Esto es CRÍTICO para evitar dos problemas:
        # 1. Recursión infinita: El propio `search` sobre `stock.location` puede disparar
        #    nuestras reglas de seguridad, que a su vez llaman a este método, creando un bucle.
        # 2. Permisos insuficientes: El usuario podría no tener permiso para ver todas las
        #    ubicaciones hijas. Con `sudo()`, nos aseguramos de obtener la jerarquía completa.
        all_child_locations = self.env['stock.location'].sudo().search([
            ('id', 'child_of', self.allowed_location_ids.ids),
            # Nos interesan solo las ubicaciones físicas internas y las de tránsito.
            ('usage', 'in', ['internal', 'transit'])
        ])
        return all_child_locations.ids

    def get_all_location_ids_with_access(self):
        """
        Calcula TODAS las ubicaciones que el usuario necesita "ver" para que la interfaz
        de Odoo funcione correctamente. Esto es diferente a `get_effective_location_ids`,
        que es más restrictivo y solo para operaciones.

        Esta lista incluye:
        1. Las ubicaciones operativas (las de `get_effective_location_ids`).
        2. Los "ancestros" de esas ubicaciones (para que se vea el árbol completo: Almacén > Zona > Estante).
        3. Ubicaciones "técnicas" (proveedores, clientes, producción, etc.) que son necesarias
           para que las transferencias y otras operaciones no fallen por falta de permisos.

        Returns:
            list: Una lista de IDs de todas las ubicaciones visibles para el usuario.
                  Retorna una lista vacía si el usuario no tiene restricciones.
        """
        self.ensure_one()
        if not self.has_stock_restriction:
            return []

        # 1. Empezamos con la base: las ubicaciones donde el usuario puede operar.
        effective_ids = self.get_effective_location_ids()

        # 2. Calculamos los ancestros. Si a un usuario le das acceso a "Estante A",
        #    también necesita ver "Pasillo 1" y "Almacén Principal" para poder navegar.
        #    Este bucle recorre cada ubicación permitida y sube por el árbol (`location_id`)
        #    agregando cada padre a la lista hasta llegar a la cima.
        ancestor_locations = self.env['stock.location'].sudo()
        seen = set() # Usamos un 'set' para no añadir el mismo ancestro varias veces.
        for loc in self.allowed_location_ids.sudo():
            current = loc
            while current and current.id not in seen:
                seen.add(current.id)
                ancestor_locations |= current # El operador '|=' añade el recordset a la colección.
                current = current.location_id

        # 3. Ubicaciones Técnicas: Esta es la clave para evitar muchos errores de acceso.
        #    Cuando haces una transferencia de un proveedor a tu almacén, el usuario necesita
        #    poder "ver" la ubicación del proveedor, aunque no pueda operar en ella.
        #    Aquí añadimos todas esas ubicaciones no-internas para evitar problemas.
        technical_locations = self.env['stock.location'].sudo().search([
            '|', '|', '|',
            ('usage', '=', 'view'),          # Ubicaciones tipo 'Vista' (son como carpetas).
            ('usage', '=', 'transit'),       # Ubicaciones de tránsito entre almacenes.
            ('usage', '=', 'inventory'),     # Ubicaciones virtuales para ajustes de inventario.
            ('usage', 'not in', ['internal']) # El resto: Proveedores, Clientes, Producción, Chatarra...
        ])

        # Combinamos las tres listas y usamos `set()` para eliminar duplicados antes de devolver.
        return list(set(effective_ids + ancestor_locations.ids + technical_locations.ids))

    def check_location_access(self, location_id, operation='read'):
        """
        Método de ayuda para verificar si el usuario actual tiene acceso a una
        ubicación específica para una operación determinada ('read' o 'write').

        Args:
            location_id (int): El ID de la ubicación a verificar.
            operation (str): El tipo de operación, puede ser 'read' o 'write'.

        Returns:
            bool: True si el acceso está permitido, False en caso contrario.
        """
        self.ensure_one()
        # Si el usuario no tiene restricciones o es el superusuario, siempre tiene acceso.
        if not self.has_stock_restriction or self.env.su:
            return True

        # Para operaciones de 'lectura', usamos la lista más amplia de ubicaciones,
        # que incluye ancestros y ubicaciones técnicas.
        if operation == 'read':
            return location_id in self.get_all_location_ids_with_access()

        # Para operaciones de 'escritura' (o cualquier otra cosa), usamos la lista
        # más estricta, que solo incluye las ubicaciones operativas y sus hijas.
        return location_id in self.get_effective_location_ids()
