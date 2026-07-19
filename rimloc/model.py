"""Modelo de datos de una traducción de RimWorld.

La pieza central es `TranslationKey`. Su identidad NO es el nombre de la clave
sino la pareja (tipo de Def, nombre de clave): RimWorld resuelve las inyecciones
por la carpeta que las contiene, así que un mismo `defName` puede existir como
`ThingDef` y como `HediffDef` con traducciones legítimamente distintas.
Ignorar esto produce falsos positivos de "clave duplicada".
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path


# Carpetas reconocidas dentro de Languages/<idioma>/
DEF_INJECTED = "DefInjected"
KEYED = "Keyed"
STRINGS = "Strings"

# Nombres cortos de las variantes de español. RimWorld acepta tanto estos como
# los largos ("Spanish (Español(Castellano))") gracias a su `legacyFolderName`.
SPANISH_VARIANTS = ("Spanish", "SpanishLatin")


@dataclass(frozen=True)
class TranslationKey:
    """Una clave traducida y su procedencia."""

    def_type: str          # "ThingDef", "HediffDef"... o "" para Keyed/Strings
    name: str              # "WCE2_GougeEye.label"
    value: str             # el texto en español
    source: Path           # archivo del que salió
    line: int = 0          # línea aproximada, para poder señalarla en los avisos
    #: Elementos, si la clave traduce una lista entera en vez de un texto suelto.
    #: RimWorld admite sustituir toda una lista (`rulesStrings` de un RulePack,
    #: por ejemplo) declarando la clave sin índice y metiendo dentro los `<li>`.
    #: Es el formato que recomienda el propio informe de traducción, y el único
    #: viable cuando la traducción no tiene el mismo número de elementos que el
    #: original.
    items: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        """Identidad real de la clave: lo que RimWorld considera única."""
        return f"{self.def_type}/{self.name}" if self.def_type else self.name

    @property
    def es_lista(self) -> bool:
        return bool(self.items)

    def __str__(self) -> str:
        return self.id


@dataclass
class LanguageFolder:
    """Una carpeta `Languages/<idioma>/` completa."""

    path: Path
    language: str
    keys: list[TranslationKey] = field(default_factory=list)
    parse_errors: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def by_id(self) -> dict[str, TranslationKey]:
        """Índice por identidad. Si hay duplicados gana el último, como RimWorld
        avisaría; usa `duplicates()` para detectarlos antes.

        Una clave que traduce una lista entera cubre además todas sus posiciones:
        `X.rulesStrings` sustituye a `X.rulesStrings.0`, `.1`, etc. El mod
        original las declara con índice, así que sin esto `diff` daría por
        ausentes las 450 que la lista ya traduce.
        """
        indice: dict[str, TranslationKey] = {}
        for k in self.keys:
            indice[k.id] = k
            for i in range(len(k.items)):
                indice.setdefault(f"{k.id}.{i}", k)
        return indice

    def duplicates(self) -> list[list[TranslationKey]]:
        """Grupos de claves que comparten identidad. Cada grupo es un conflicto
        real: RimWorld registrará `Duplicate def-linked translation key` y se
        quedará con una sola."""
        seen: dict[str, list[TranslationKey]] = {}
        for key in self.keys:
            seen.setdefault(key.id, []).append(key)
        return [group for group in seen.values() if len(group) > 1]


def _line_numbers(path: Path) -> dict[str, int]:
    """Mapa clave -> primera línea en la que aparece.

    ElementTree no expone números de línea, y como los avisos tienen que ser
    accionables ("archivo:línea") hacemos una pasada de texto. Buscamos la
    apertura `<clave>` al principio de la línea ignorando indentación; las
    líneas comentadas no cuentan porque el parser ya las descartó y aquí solo
    resolvemos claves que sabemos que existen.
    """
    numbers: dict[str, int] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return numbers
    for i, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped.startswith("<") or stripped.startswith("<!--"):
            continue
        end = stripped.find(">")
        if end > 1:
            tag = stripped[1:end]
            if tag not in numbers:
                numbers[tag] = i
    return numbers


def load_language_folder(path: Path) -> LanguageFolder:
    """Carga todas las claves de `Languages/<idioma>/`.

    Usa un parser XML de verdad, no expresiones regulares: los comentarios
    `<!-- ... -->` que estos archivos llevan por convención contienen texto que
    *parece* una clave, y una regex los tomaría por traducciones reales.
    """
    folder = LanguageFolder(path=path, language=path.name)

    for xml_path in sorted(path.rglob("*.xml")):
        # El tipo de Def es la carpeta contenedora, pero solo dentro de
        # DefInjected/. En Keyed/ y Strings/ los nombres de carpeta son libres.
        parts = xml_path.relative_to(path).parts
        def_type = parts[1] if len(parts) > 2 and parts[0] == DEF_INJECTED else ""

        try:
            root = ET.parse(xml_path).getroot()
        except ET.ParseError as exc:
            folder.parse_errors.append((xml_path, str(exc)))
            continue

        lines = _line_numbers(xml_path)
        for node in root:
            # ET representa los comentarios con una función como tag; los
            # descartamos igual que hace RimWorld.
            if not isinstance(node.tag, str):
                continue

            # Traducción de lista entera: la clave no lleva texto, lleva <li>.
            items = tuple(
                (li.text or "").strip() for li in node
                if isinstance(li.tag, str) and li.tag == "li"
            )
            # Para las reglas de texto —glosario, tildes, caracteres invisibles—
            # el contenido de la lista es tan revisable como cualquier otro.
            valor = " ".join(items) if items else (node.text or "").strip()

            folder.keys.append(
                TranslationKey(
                    def_type=def_type,
                    name=node.tag,
                    value=valor,
                    source=xml_path,
                    line=lines.get(node.tag, 0),
                    items=items,
                )
            )

    return folder


#: Carpetas que nunca contienen material del mod.
_IGNORADAS = {".git", ".github", "__pycache__", ".vscode", ".idea"}


def find_language_roots(mod_path: Path) -> list[Path]:
    """Todas las carpetas `Languages/` de un mod, no solo la de la raíz.

    Un mod puede tener varias por dos motivos:

    - La estructura versionada (`1.6/Languages/...`).
    - El contenido condicional: `LoadFolders.xml` permite cargar una carpeta
      solo si otro mod está activo (`<li IfModActive="rimfridge.kv.rw">RimFridge</li>`),
      y es la forma de que las inyecciones a Defs de otro mod no salgan como
      errores de carga para quien no lo tenga instalado.

    Si se mira solo la raíz, esas traducciones condicionales quedan sin validar
    y sin sincronizar: existen en el mod pero la herramienta no las ve.
    """
    encontradas: list[Path] = []
    for languages_dir in sorted(mod_path.rglob("Languages")):
        if not languages_dir.is_dir():
            continue
        if any(parte in _IGNORADAS for parte in languages_dir.relative_to(mod_path).parts):
            continue
        encontradas.append(languages_dir)
    return encontradas


def find_language_folders(mod_path: Path) -> list[Path]:
    """Las carpetas de idioma concretas (`Languages/Spanish`, ...) de un mod."""
    found: list[Path] = []
    for languages_dir in find_language_roots(mod_path):
        found.extend(child for child in sorted(languages_dir.iterdir()) if child.is_dir())
    return found


def load_language(paths: Sequence[Path]) -> LanguageFolder:
    """Carga varias carpetas del MISMO idioma como una sola.

    Un mod puede repartir un idioma entre la carpeta de la raíz y una o más
    condicionales de `LoadFolders.xml`. Para RimWorld eso sigue siendo un único
    idioma, y hay que tratarlo igual: si se contara cada carpeta por separado,
    cada una parecería una traducción incompleta, y una clave repetida entre
    dos de ellas —que RimWorld sí registra como duplicada— pasaría inadvertida.
    """
    if not paths:
        raise ValueError("hacen falta carpetas que cargar")

    fusionada = LanguageFolder(path=paths[0], language=paths[0].name)
    for path in paths:
        parcial = load_language_folder(path)
        fusionada.keys.extend(parcial.keys)
        fusionada.parse_errors.extend(parcial.parse_errors)
    return fusionada
