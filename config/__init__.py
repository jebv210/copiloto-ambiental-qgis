"""
Paquete de configuracion.

Contiene `settings.py`, el unico lugar del proyecto donde se resuelven rutas,
credenciales y parametros del servidor. Ningun otro modulo debe construir
rutas absolutas por su cuenta.

    from config import settings
    settings.ruta_gdb()
"""
