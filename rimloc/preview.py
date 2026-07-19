"""Generación de la imagen de vista previa del Workshop.

Una carátula propia y consistente para todas las traducciones, en vez de
retocar el preview del mod original. Dos razones:

- El preview ajeno es obra de otra persona. Reutilizarlo obliga a heredar su
  composición, su resolución y sus derechos, y el resultado depende de lo bien
  o mal que encaje lo que le pegues encima.
- Una carátula común hace reconocible la colección: quien vea una traducción
  reconoce las demás en la lista del Workshop.

Este es el único módulo de rimloc con una dependencia externa (Pillow), y por
eso vive aparte y se importa en perezoso desde `cli`. El núcleo —validar,
comparar, sincronizar, instalar— sigue funcionando con la librería estándar
aunque Pillow no esté.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Proporción 16:9. Steam reescala, pero pide menos de 1 MB: a este tamaño un PNG
# de colores planos pesa unos 30 kB.
ANCHO, ALTO = 1200, 675

# Sin banderas, a propósito. Se tradujo a español neutro y se publican las dos
# variantes, castellana y latinoamericana; una bandera de España en la carátula
# diría lo contrario antes de que nadie lea una línea.

#: Fuentes de Windows, de más a menos condensada. Se usa la primera que exista;
#: si no hay ninguna, Pillow cae a su tipo por defecto y la imagen sigue saliendo.
FUENTES_TITULO = ("bahnschrift.ttf", "impact.ttf", "ariblk.ttf", "arialbd.ttf")
FUENTES_TEXTO = ("bahnschrift.ttf", "seguisb.ttf", "segoeuib.ttf", "arialbd.ttf")


@dataclass(frozen=True)
class Tema:
    """Paleta y rasgos de un estilo de carátula."""

    nombre: str
    fondo: tuple[int, int, int]
    fondo2: tuple[int, int, int]
    titulo: tuple[int, int, int]
    acento: tuple[int, int, int]
    apagado: tuple[int, int, int]
    subrayado: bool = True


TEMAS: dict[str, Tema] = {
    # Tierra y pergamino: la paleta del propio RimWorld.
    "rimworld": Tema(
        nombre="rimworld",
        fondo=(38, 33, 28), fondo2=(22, 19, 16),
        titulo=(240, 232, 216), acento=(217, 164, 65), apagado=(150, 138, 120),
    ),
    # Pizarra fría y dorado. Más neutro, funciona con cualquier mod.
    "pizarra": Tema(
        nombre="pizarra",
        fondo=(32, 38, 45), fondo2=(16, 20, 24),
        titulo=(238, 240, 243), acento=(226, 178, 74), apagado=(138, 150, 162),
    ),
    # Oliva y hueso. El que más se separa de la paleta habitual de los mods,
    # para cuando la carátula tiene que despegarse de lo que enmarca.
    "oliva": Tema(
        nombre="oliva",
        fondo=(34, 38, 29), fondo2=(18, 21, 15),
        titulo=(238, 238, 226), acento=(166, 190, 106), apagado=(140, 150, 122),
    ),
}


def _cargar_fuente(candidatas: tuple[str, ...], tamano: int):
    from PIL import ImageFont

    for nombre in candidatas:
        try:
            return ImageFont.truetype(nombre, tamano)
        except OSError:
            continue
    return ImageFont.load_default(tamano)


def _ancho(draw, texto: str, fuente) -> int:
    izq, _, der, _ = draw.textbbox((0, 0), texto, font=fuente)
    return der - izq


def _ajustar(draw, texto: str, candidatas: tuple[str, ...],
             max_ancho: int, tamano: int, minimo: int = 28):
    """Baja el cuerpo de letra hasta que el texto quepa a lo ancho.

    Los títulos de los mods van de «Vault» a «Vanilla Genetics Expanded», y una
    carátula que se sale por el borde con los largos no sirve como plantilla.
    """
    while tamano > minimo:
        fuente = _cargar_fuente(candidatas, tamano)
        if _ancho(draw, texto, fuente) <= max_ancho:
            return fuente
        tamano -= 2
    return _cargar_fuente(candidatas, minimo)


def _partir(draw, texto: str, fuente, max_ancho: int) -> list[str]:
    """Reparte el texto en líneas que quepan, sin cortar palabras."""
    lineas: list[str] = []
    actual = ""
    for palabra in texto.split():
        tentativa = f"{actual} {palabra}".strip()
        if _ancho(draw, tentativa, fuente) <= max_ancho or not actual:
            actual = tentativa
        else:
            lineas.append(actual)
            actual = palabra
    if actual:
        lineas.append(actual)
    return lineas


def _fondo(img, tema: Tema) -> None:
    """Degradado vertical y viñeta, para que el texto no flote sobre un plano."""
    from PIL import Image, ImageDraw, ImageFilter

    draw = ImageDraw.Draw(img)
    for y in range(ALTO):
        t = y / ALTO
        draw.line(
            [(0, y), (ANCHO, y)],
            fill=tuple(int(a + (b - a) * t) for a, b in zip(tema.fondo, tema.fondo2)),
        )

    # Viñeta: un rectángulo claro difuminado, restado por los bordes.
    mascara = Image.new("L", (ANCHO, ALTO), 0)
    ImageDraw.Draw(mascara).rectangle(
        (ANCHO * 0.10, ALTO * 0.10, ANCHO * 0.90, ALTO * 0.90), fill=70)
    mascara = mascara.filter(ImageFilter.GaussianBlur(ANCHO // 8))
    img.paste(Image.new("RGB", (ANCHO, ALTO), tema.acento), (0, 0),
              mascara.point(lambda v: v // 6))


def _trama(img, tema: Tema) -> None:
    """Diagonales muy tenues, para que el fondo no sea un plano muerto.

    Se dibujan en una capa aparte y se funden al 4 %: a tamaño de miniatura no
    se distinguen, pero le quitan al conjunto el aire de diapositiva vacía.
    """
    from PIL import Image, ImageDraw

    capa = Image.new("RGB", (ANCHO, ALTO), (0, 0, 0))
    d = ImageDraw.Draw(capa)
    for x in range(-ALTO, ANCHO, 44):
        d.line([(x, ALTO), (x + ALTO, 0)], fill=tema.titulo, width=2)
    img.paste(Image.blend(img, capa, 0.03), (0, 0))


def _sello(img, draw, tema: Tema, texto: str, cx: int, cy: int, radio: int) -> None:
    """Distintivo circular con el código de idioma.

    Es el elemento que hace reconocible la colección de un vistazo, incluso en
    la miniatura pequeña del Workshop donde el título ya no se lee.
    """
    from PIL import Image, ImageDraw

    # Anillo exterior a baja opacidad, sobre capa propia para poder fundirlo.
    capa = Image.new("RGBA", (ANCHO, ALTO), (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    d.ellipse((cx - radio, cy - radio, cx + radio, cy + radio),
              outline=tema.acento + (90,), width=6)
    d.ellipse((cx - radio + 16, cy - radio + 16, cx + radio - 16, cy + radio - 16),
              fill=tema.acento + (26,))
    img.paste(Image.alpha_composite(img.convert("RGBA"), capa).convert("RGB"), (0, 0))

    draw = ImageDraw.Draw(img)
    fuente = _cargar_fuente(FUENTES_TITULO, int(radio * 0.95))
    izq, arr, der, aba = draw.textbbox((0, 0), texto, font=fuente)
    draw.text((cx - (der - izq) / 2 - izq, cy - (aba - arr) / 2 - arr),
              texto, font=fuente, fill=tema.acento)


def _encajar(original, ancho: int, alto: int):
    """Escala la imagen para que quepa entera, sin deformarla ni recortarla."""
    from PIL import Image

    escala = min(ancho / original.width, alto / original.height)
    nuevo = (max(1, round(original.width * escala)), max(1, round(original.height * escala)))
    return original.resize(nuevo, Image.LANCZOS)


def _cubrir(original, ancho: int, alto: int):
    """Escala y recorta al centro para llenar el marco por completo."""
    from PIL import Image

    escala = max(ancho / original.width, alto / original.height)
    grande = original.resize(
        (max(1, round(original.width * escala)), max(1, round(original.height * escala))),
        Image.LANCZOS,
    )
    izq = (grande.width - ancho) // 2
    arr = (grande.height - alto) // 2
    return grande.crop((izq, arr, izq + ancho, arr + alto))


def generar_con_marco(
    destino: Path,
    titulo: str,
    original: Path,
    *,
    tema: str = "rimworld",
    autor: str = "",
    coleccion: str = "",
    subtitulo: str = "TRADUCCIÓN AL ESPAÑOL",
    codigo: str = "ES",
) -> Path:
    """Enmarca el preview del mod original con la identidad de la colección.

    Se reconoce de qué mod es —que es lo que busca quien navega el Workshop— y
    a la vez se distingue como traducción. El fondo del marco es el propio
    preview desenfocado y oscurecido, así que la carátula hereda la paleta de
    cada mod sin tener que elegirla a mano.
    """
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

    estilo = TEMAS.get(tema, TEMAS["rimworld"])
    franja = 132          # alto de la banda inferior
    borde = 30            # aire alrededor de la imagen original

    with Image.open(original) as src:
        src = src.convert("RGB")

        # Fondo: el propio preview, ampliado, desenfocado y apagado.
        fondo = _cubrir(src, ANCHO, ALTO).filter(ImageFilter.GaussianBlur(28))
        fondo = ImageEnhance.Brightness(fondo).enhance(0.38)
        fondo = ImageEnhance.Color(fondo).enhance(0.7)
        img = fondo

        # La imagen original, entera y nítida, sobre la zona superior.
        hueco_alto = ALTO - franja - borde * 2
        placa = _encajar(src, ANCHO - borde * 2, hueco_alto)

    x = (ANCHO - placa.width) // 2
    y = borde + (hueco_alto - placa.height) // 2
    img.paste(placa, (x, y))

    draw = ImageDraw.Draw(img)
    # Filo de acento alrededor de la imagen: la separa del fondo difuminado.
    draw.rectangle((x - 2, y - 2, x + placa.width + 1, y + placa.height + 1),
                   outline=estilo.acento, width=2)

    # --- Banda inferior ---
    banda_y = ALTO - franja
    capa = Image.new("RGBA", (ANCHO, ALTO), (0, 0, 0, 0))
    ImageDraw.Draw(capa).rectangle((0, banda_y, ANCHO, ALTO), fill=(0, 0, 0, 205))
    img = Image.alpha_composite(img.convert("RGBA"), capa).convert("RGB")
    draw = ImageDraw.Draw(img)

    # Filete de acento separando la banda de la imagen.
    draw.rectangle((0, banda_y - 4, ANCHO, banda_y - 1), fill=estilo.acento)

    margen = 34
    # Sello a la derecha; el título ocupa lo que quede.
    radio = (franja - 40) // 2
    cx = ANCHO - margen - radio
    cy = banda_y + franja // 2
    if codigo:
        draw.ellipse((cx - radio, cy - radio, cx + radio, cy + radio),
                     outline=estilo.acento, width=3)
        fuente_sello = _cargar_fuente(FUENTES_TITULO, int(radio * 1.05))
        izq, arr, der, aba = draw.textbbox((0, 0), codigo, font=fuente_sello)
        draw.text((cx - (der - izq) / 2 - izq, cy - (aba - arr) / 2 - arr),
                  codigo, font=fuente_sello, fill=estilo.acento)

    util = (cx - radio - 24) - margen if codigo else ANCHO - margen * 2
    limpio = titulo.replace("[ES]", "").replace("[es]", "").strip(" -–—")
    fuente_titulo = _ajustar(draw, limpio, FUENTES_TITULO, util, 50, minimo=26)
    draw.text((margen, banda_y + 26), limpio, font=fuente_titulo, fill=estilo.titulo)

    fuente_pie = _cargar_fuente(FUENTES_TEXTO, 23)
    firma = " · ".join(p for p in (subtitulo, coleccion, autor) if p)
    draw.text((margen, banda_y + 26 + fuente_titulo.size + 12), firma,
              font=fuente_pie, fill=estilo.apagado)

    destino.parent.mkdir(parents=True, exist_ok=True)
    img.save(destino, "PNG", optimize=True)
    return destino


def generar(
    destino: Path,
    titulo: str,
    *,
    tema: str = "rimworld",
    autor: str = "",
    coleccion: str = "",
    subtitulo: str = "TRADUCCIÓN AL ESPAÑOL",
    variantes: str = "",
    codigo: str = "ES",
) -> Path:
    """Compone la carátula y la escribe en `destino`.

    Requiere Pillow. Quien llame se encarga de avisar si no está instalado.
    """
    from PIL import Image, ImageDraw

    estilo = TEMAS.get(tema, TEMAS["rimworld"])
    img = Image.new("RGB", (ANCHO, ALTO), estilo.fondo)
    _fondo(img, estilo)
    _trama(img, estilo)
    draw = ImageDraw.Draw(img)

    margen = 90
    radio = 132
    cx, cy = ANCHO - margen - radio, ALTO // 2 - 30
    if codigo:
        _sello(img, draw, estilo, codigo, cx, cy, radio)
        draw = ImageDraw.Draw(img)

    # El título no puede invadir el sello.
    util = (cx - radio - 40) - margen if codigo else ANCHO - margen * 2

    # --- Título: el nombre del mod, sin el sufijo de idioma ---
    limpio = titulo.replace("[ES]", "").replace("[es]", "").strip(" -–—")
    fuente_titulo = _ajustar(draw, limpio, FUENTES_TITULO, util, 96, minimo=44)
    lineas = _partir(draw, limpio, fuente_titulo, util)
    if len(lineas) > 2:  # tres líneas descuadran la composición: encoge y reparte
        fuente_titulo = _ajustar(draw, limpio, FUENTES_TITULO, util * 2, 72, minimo=40)
        lineas = _partir(draw, limpio, fuente_titulo, util)[:3]

    alto_linea = fuente_titulo.size + 12
    bloque = alto_linea * len(lineas)
    y = ALTO // 2 - bloque // 2 - 40
    for linea in lineas:
        draw.text((margen, y), linea, font=fuente_titulo, fill=estilo.titulo)
        y += alto_linea

    # --- Regla de acento bajo el título ---
    if estilo.subrayado:
        y += 14
        draw.rectangle((margen, y, margen + 240, y + 12), fill=estilo.acento)
        y += 44

    # --- Subtítulo ---
    fuente_sub = _ajustar(draw, subtitulo, FUENTES_TEXTO, util, 44, minimo=24)
    draw.text((margen, y), subtitulo, font=fuente_sub, fill=estilo.acento)

    # --- Pie: variantes a la izquierda, autoría a la derecha ---
    fuente_pie = _cargar_fuente(FUENTES_TEXTO, 26)
    y_pie = ALTO - margen // 2 - 26
    draw.text((margen, y_pie), variantes, font=fuente_pie, fill=estilo.apagado)

    firma = " · ".join(p for p in (coleccion, autor) if p)
    if firma:
        draw.text((ANCHO - margen - _ancho(draw, firma, fuente_pie), y_pie),
                  firma, font=fuente_pie, fill=estilo.apagado)

    destino.parent.mkdir(parents=True, exist_ok=True)
    img.save(destino, "PNG", optimize=True)
    return destino
