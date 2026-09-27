"""El catalogo de Brasa & Pan, en memoria y por llamada.

Se pide una sola vez al arrancar la sesion y se guarda en `session.userdata`.
Antes esto eran consultas SQL con `pg_trgm` en cada busqueda; ahora el menu
completo va en el prompt del agente y la busqueda es un respaldo en memoria
para lo ambiguo, no el camino principal.

**Este modulo no calcula promociones.** El precio de hoy (`priceNow`) ya viene
resuelto del servidor. Lo unico que se hace aca es aplicar la regla del 2x1
—que no baja el precio unitario, baja cuantas unidades se cobran— para poder
decirle al cliente un subtotal parcial que coincida con el que va a quedar
guardado. El total definitivo siempre es el que devuelve `confirm_order`.
"""

import difflib
import math
from dataclasses import dataclass
from typing import Any


def _formatear_precio(valor: int) -> str:
    return f"${valor:,}".replace(",", ".")


@dataclass
class Opcion:
    id: int
    name: str
    price_delta: int


@dataclass
class GrupoOpciones:
    id: int
    name: str
    tipo: str
    obligatorio: bool
    opciones: list[Opcion]


@dataclass
class Producto:
    sku: str
    name: str
    description: str
    precio_carta: int
    precio_hoy: int
    promocion: str | None
    disponible: bool
    categoria: str
    grupos: list[GrupoOpciones]
    # `2x1` cobra la mitad de las unidades (redondeando hacia arriba); los otros
    # tipos ya vienen aplicados dentro de `precio_hoy`.
    promocion_kind: str | None = None

    @property
    def grupos_obligatorios(self) -> list[GrupoOpciones]:
        return [g for g in self.grupos if g.obligatorio and g.opciones]

    def opcion(self, opcion_id: int) -> Opcion | None:
        for grupo in self.grupos:
            for opcion in grupo.opciones:
                if opcion.id == opcion_id:
                    return opcion
        return None

    def grupo_de(self, opcion_id: int) -> GrupoOpciones | None:
        for grupo in self.grupos:
            if any(o.id == opcion_id for o in grupo.opciones):
                return grupo
        return None

    def faltantes_obligatorios(self, option_ids: list[int]) -> list[GrupoOpciones]:
        """TODOS los grupos obligatorios que el cliente todavia no eligio.

        Es lo que le dice al agente que tiene que preguntar el termino de la
        carne antes de mandar la comanda a la parrilla. El servidor lo vuelve a
        comprobar (y rechaza el pedido entero si falta), pero descubrirlo aca
        permite preguntarlo en el momento en vez de al final.

        Se devuelven **todos** y no solo el primero porque una hamburguesa pide
        dos cosas (termino y acompanamiento): si la tool solo nombrara el
        primero, el agente preguntaria uno, volveria a fallar, y el cliente
        oiria dos silencios en vez de dos preguntas seguidas.
        """
        elegidas = set(option_ids)
        return [
            grupo
            for grupo in self.grupos_obligatorios
            if not any(o.id in elegidas for o in grupo.opciones)
        ]

    def precio_unitario(self, option_ids: list[int]) -> int:
        """El precio de hoy mas lo que sumen las opciones.

        El descuento va sobre el precio base, no sobre las opciones: una hora
        feliz del 20% no tiene por que rebajar la tocineta extra. Es la misma
        regla del servidor (`priceLine`)."""
        extra = 0
        for opcion_id in option_ids:
            opcion = self.opcion(opcion_id)
            if opcion:
                extra += opcion.price_delta
        return self.precio_hoy + extra

    def total_linea(self, option_ids: list[int], cantidad: int) -> int:
        unitario = self.precio_unitario(option_ids)
        cobradas = math.ceil(cantidad / 2) if self.promocion_kind == "2x1" else cantidad
        return unitario * cobradas


class Catalogo:
    def __init__(self, datos: dict[str, Any]) -> None:
        self.negocio: dict[str, Any] = datos.get("business") or {}
        self.promociones: list[dict[str, Any]] = datos.get("promotions") or []
        self.productos: list[Producto] = []

        for categoria in datos.get("categories") or []:
            for p in categoria.get("products") or []:
                promo = p.get("promotion") or None
                self.productos.append(
                    Producto(
                        sku=p["sku"],
                        name=p["name"],
                        description=p.get("description") or "",
                        precio_carta=p.get("price", 0),
                        precio_hoy=p.get("priceNow", p.get("price", 0)),
                        promocion=promo.get("name") if promo else None,
                        promocion_kind=promo.get("kind") if promo else None,
                        disponible=bool(p.get("available", True)),
                        categoria=categoria.get("name") or "",
                        grupos=[
                            GrupoOpciones(
                                id=g["id"],
                                name=g["name"],
                                tipo=g.get("type", "single"),
                                obligatorio=bool(g.get("required")),
                                opciones=[
                                    Opcion(
                                        id=o["id"],
                                        name=o["name"],
                                        price_delta=o.get("priceDelta", 0),
                                    )
                                    for o in g.get("options") or []
                                ],
                            )
                            for g in p.get("optionGroups") or []
                        ],
                    )
                )

    @property
    def nombre_negocio(self) -> str:
        return self.negocio.get("name") or "el restaurante"

    @property
    def domicilio(self) -> int:
        return int(self.negocio.get("deliveryFee") or 0)

    @property
    def tiempo_entrega(self) -> str:
        return self.negocio.get("deliveryTime") or "30 a 45 minutos"

    @property
    def whatsapp(self) -> str:
        return self.negocio.get("whatsappNumber") or ""

    def buscar_sku(self, sku: str) -> Producto | None:
        sku = (sku or "").strip()
        for p in self.productos:
            if p.sku.lower() == sku.lower():
                return p
        return None

    def buscar(self, consulta: str, limite: int = 5) -> list[Producto]:
        """Busqueda tolerante a la transcripcion de voz.

        Primero coincidencia literal (lo que el cliente dijo aparece en el
        nombre, la descripcion o la categoria), y despues `difflib` para lo que
        el STT escribio distinto ("polo asado" -> "Pollo Asado").

        Lo que coincide en el **nombre** va antes que lo que solo coincide en la
        descripcion: quien pide "pollo" quiere el pollo, no la hamburguesa que
        lo menciona de pasada. Es la misma leccion que el buscador del menu.
        """
        palabras = [w for w in _normalizar(consulta).split() if w]
        if not palabras:
            return []

        con_puntaje: list[tuple[int, Producto]] = []
        for p in self.productos:
            nombre = _normalizar(p.name)
            resto = _normalizar(f"{p.description} {p.categoria}")
            if all(w in f"{nombre} {resto}" for w in palabras):
                en_nombre = sum(1 for w in palabras if w in nombre)
                con_puntaje.append((en_nombre, p))

        if con_puntaje:
            con_puntaje.sort(key=lambda x: -x[0])
            return [p for _, p in con_puntaje[:limite]]

        # Nada literal: se prueba por parecido, que es lo que salva los errores
        # de transcripcion.
        por_nombre = {_normalizar(p.name): p for p in self.productos}
        parecidos = difflib.get_close_matches(
            _normalizar(consulta), list(por_nombre), n=limite, cutoff=0.6
        )
        return [por_nombre[n] for n in parecidos]

    def prompt_block(self) -> str:
        """El menu como texto para las `instructions` del agente.

        Va completo en el prompt (25 productos entran sin problema) para que el
        agente ya "sepa" la carta desde el primer turno, sin una tool ni la
        latencia de un roundtrip en medio de la conversacion.

        Los ids de opcion van visibles porque son lo que el agente tiene que
        pasarle a `add_item_to_order`. Las opciones **obligatorias** se marcan
        para que sepa que esas si hay que preguntarlas.
        """
        if not self.productos:
            return (
                "El menú está vacío por el momento; informa al cliente que no hay "
                "productos disponibles y que escriba por WhatsApp."
            )

        por_categoria: dict[str, list[Producto]] = {}
        for p in self.productos:
            por_categoria.setdefault(p.categoria, []).append(p)

        lineas: list[str] = []
        for categoria, productos in por_categoria.items():
            lineas.append(f"{categoria.upper()}:")
            for p in productos:
                precio = _formatear_precio(p.precio_hoy)
                if p.precio_hoy != p.precio_carta:
                    precio = (
                        f"HOY {precio} en vez de {_formatear_precio(p.precio_carta)}"
                        f" — {p.promocion}"
                    )
                estado = "" if p.disponible else "  [AGOTADO HOY, no lo ofrezcas]"
                lineas.append(f"  - [{p.sku}] {p.name}: {p.description} ({precio}){estado}")

                for grupo in p.grupos:
                    if not grupo.opciones:
                        continue
                    marca = "PREGUNTA SIEMPRE" if grupo.obligatorio else "solo si lo pide"
                    opciones = ", ".join(
                        f"{o.name}={o.id}"
                        + (f" (+{_formatear_precio(o.price_delta)})" if o.price_delta else "")
                        for o in grupo.opciones
                    )
                    lineas.append(f"      {grupo.name} [{marca}]: {opciones}")

        if self.promociones:
            lineas.append("")
            lineas.append("PROMOCIONES VIGENTES AHORA:")
            for promo in self.promociones:
                detalle = " · ".join(
                    x for x in [promo.get("descuento"), promo.get("horario")] if x
                )
                lineas.append(f"  - {promo.get('name')}: {detalle}")

        lineas.append("")
        lineas.append(
            f"DOMICILIO: {_formatear_precio(self.domicilio)} "
            f"· entrega en {self.tiempo_entrega}"
        )
        return "\n".join(lineas)


def _normalizar(texto: str) -> str:
    """Minusculas y sin tildes, para que "limón" encuentre "LIMON"."""
    import unicodedata

    descompuesto = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")
