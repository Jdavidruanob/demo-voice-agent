"""Puente entre el agente de voz y el sistema de pedidos de Brasa & Pan.

Este paquete reemplaza lo que antes era `database/`: el agente ya no tiene
base de datos propia. El catalogo y los pedidos viven en el sistema de
pedidos (`demo-delivery-system`) y se consultan por HTTP.

Ver `docs/ARQUITECTURA.md` § El sistema de pedidos.
"""
