"""Extracción de claves traducibles desde los Defs de un mod.

Esto es lo que hacía RimTrans cargando los ensamblados del mod por reflexión, y
la razón por la que se rompía con mods que usaban C# propio. Aquí se parsea XML
puro: más limitado en teoría, pero no depende de la versión del juego ni de que
el mod compile, así que no se pudre.

Aviso honesto sobre el alcance: la fuente de verdad definitiva es el propio
RimWorld («Clean up translation files» en el menú principal), porque resuelve
los Defs por reflexión real. Este módulo cubre los casos habituales para poder
hacer `diff` sin arrancar el juego; ante una discrepancia, manda el juego.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

# Campos que RimWorld inyecta por traducción. No es una lista arbitraria: son
# los marcados como traducibles en el código del juego. Ampliarla es seguro;
# recortarla hace que `diff` marque como obsoletas claves que sí se usan.
TRANSLATABLE_FIELDS = frozenset({
    # Etiquetas y descripciones
    "label", "labelShort", "labelPlural", "labelMale", "labelFemale",
    "labelNoun", "labelNounPretty", "labelAdjective", "customLabel", "permanentLabel",
    # Agrupa variantes de un mismo edificio en el menú de construcción. Se
    # escapó hasta que el informe del juego lo reclamó en Deep Storage.
    "groupingLabel",
    "description", "descriptionShort", "descriptionFuture", "baseDescription",
    # Trabajos y verbos
    "jobString", "verb", "gerund", "gerundLabel", "reportString", "skillLabel",
    "useLabel", "ingestCommandString", "ingestReportString", "ingestReportStringEat",
    # Mensajes
    "deathMessage", "successfullyRemovedHediffMessage", "recoveryMessage",
    "letterLabel", "letterText", "letterTitle", "beginLetter", "beginLetterLabel",
    "endMessage", "discoverLetterLabel", "discoverLetterText",
    # Lesiones y dolencias
    "labelTendedWell", "labelTendedWellInner", "labelSolidTendedWell",
    "labelTended", "labelTendedInner", "labelSolidTended",
    "destroyedLabel", "destroyedOutLabel",
    # Varios
    "pawnLabel", "pawnsPlural", "summary", "text", "helpText", "quotation",
    "headerTip", "tipString", "inspectLine", "stuffAdjective", "graphLabelY",
    "customSummary", "instantlyOldLabel", "oldLabel", "shortDescOverride",
    "fixedName", "onMapInstruction", "rejectInputMessage",
})


@dataclass(frozen=True)
class SourceKey:
    """Una clave traducible detectada en el mod original."""

    def_type: str
    name: str
    english: str
    source: Path

    @property
    def id(self) -> str:
        """Debe coincidir con `TranslationKey.id`: es la clave del cotejo.

        Las Keyed van sin tipo de Def, y ahí la identidad es el nombre a secas.
        Si aquí se emitiera `/NoItemsAreStoredHere` y allí
        `NoItemsAreStoredHere`, `diff` daría cada clave por ausente y sobrante
        a la vez.
        """
        return f"{self.def_type}/{self.name}" if self.def_type else self.name


def _collect_def_nodes(defs_root: Path) -> list[tuple[ET.Element, Path]]:
    """Todos los nodos Def de un árbol de Defs, con su archivo de origen."""
    nodes: list[tuple[ET.Element, Path]] = []
    for xml_path in sorted(defs_root.rglob("*.xml")):
        try:
            root = ET.parse(xml_path).getroot()
        except ET.ParseError:
            continue
        if root.tag != "Defs":
            continue
        for node in root:
            if isinstance(node.tag, str):
                nodes.append((node, xml_path))
    return nodes


def _resolve_inheritance(nodes: list[tuple[ET.Element, Path]]) -> None:
    """Aplica la herencia `ParentName` -> `Name` sobre los nodos, in situ.

    RimWorld permite Defs abstractos que sirven de plantilla. Un hijo que no
    declara `label` puede heredarlo del padre, y esa clave SÍ necesita
    traducción. Sin resolver la herencia, `diff` no las vería.
    """
    por_nombre = {
        node.get("Name"): node for node, _ in nodes if node.get("Name")
    }

    def campos_heredados(node: ET.Element, visitados: set[str]) -> list[ET.Element]:
        padre_id = node.get("ParentName")
        if not padre_id or padre_id in visitados or padre_id not in por_nombre:
            return []
        visitados.add(padre_id)
        padre = por_nombre[padre_id]
        return list(padre) + campos_heredados(padre, visitados)

    for node, _ in nodes:
        if not node.get("ParentName"):
            continue
        propios = {hijo.tag for hijo in node if isinstance(hijo.tag, str)}
        for heredado in campos_heredados(node, set()):
            if isinstance(heredado.tag, str) and heredado.tag not in propios:
                node.append(heredado)
                propios.add(heredado.tag)


def _comp_segment(child: ET.Element) -> str | None:
    """Nombre estable de un `<li Class="...">` dentro de una lista de comps.

    RimWorld admite referenciar un comp por su clase en vez de por su posición,
    y es lo que hay que usar: el índice numérico se rompe en cuanto otro mod
    reemplaza o reordena los comps. Combat Extended, por ejemplo, sustituye
    `HediffComp_TendDuration` por su propia versión, y a partir de ahí todos los
    índices del hediff se desplazan y las inyecciones fallan en silencio.

    La convención del juego es el nombre del comp, no el de sus propiedades:
    `HediffCompProperties_GetsPermanent` se referencia como `HediffComp_GetsPermanent`.
    """
    class_attr = child.get("Class")
    if not class_attr:
        return None
    nombre = class_attr.rsplit(".", 1)[-1]  # descarta el espacio de nombres
    return nombre.replace("CompProperties_", "Comp_")


def _walk(node: ET.Element, prefix: str, def_type: str, def_name: str,
          path: Path, out: list[SourceKey]) -> None:
    """Recorre un Def emitiendo las claves traducibles que encuentra.

    Las listas se indexan por posición (`stages.0.label`), salvo los comps, que
    se referencian por nombre de clase (ver `_comp_segment`).
    """
    li_index = 0
    dentro_de_comps = prefix.rsplit(".", 1)[-1] == "comps" if prefix else False

    # El nombre de clase solo sirve como identificador si es único en la lista.
    # Un precepto puede llevar varios PreceptComp_KnowsMemoryThought, y entonces
    # nombrarlos a todos igual haría colisionar las claves y se perderían
    # traducciones. En ese caso hay que seguir usando el índice.
    nombres_unicos: set[str] = set()
    if dentro_de_comps:
        vistos: dict[str, int] = {}
        for hermano in node:
            if isinstance(hermano.tag, str) and hermano.tag == "li":
                nombre = _comp_segment(hermano)
                if nombre:
                    vistos[nombre] = vistos.get(nombre, 0) + 1
        nombres_unicos = {n for n, veces in vistos.items() if veces == 1}

    for child in node:
        if not isinstance(child.tag, str):
            continue

        if child.tag == "li":
            comp = _comp_segment(child) if dentro_de_comps else None
            segment = comp if comp in nombres_unicos else str(li_index)
            li_index += 1
            # El nombre de campo relevante para un <li> es el de su lista padre
            field_name = prefix.rsplit(".", 1)[-1] if prefix else ""
        else:
            segment = child.tag
            field_name = child.tag

        child_path = f"{prefix}.{segment}" if prefix else segment
        tiene_hijos = any(isinstance(g.tag, str) for g in child)

        if tiene_hijos:
            _walk(child, child_path, def_type, def_name, path, out)
        elif field_name in TRANSLATABLE_FIELDS and (child.text or "").strip():
            out.append(SourceKey(
                def_type=def_type,
                name=f"{def_name}.{child_path}",
                english=child.text.strip(),
                source=path,
            ))


def _synthesize_recipe_makers(nodes: list[tuple[ET.Element, Path]]) -> list[SourceKey]:
    """Genera las claves `Make_<defName>` de las recetas automáticas.

    Todo ThingDef con `<recipeMaker>` provoca que RimWorld cree en tiempo de
    carga un RecipeDef llamado `Make_<defName>` con su propio label, description
    y jobString traducibles. Estas claves no existen en ningún XML del mod: un
    extractor ingenuo las da por obsoletas y te invita a borrar traducciones
    perfectamente válidas.
    """
    claves: list[SourceKey] = []
    for node, path in nodes:
        if node.find("recipeMaker") is None:
            continue
        def_name = node.findtext("defName", "").strip()
        label = node.findtext("label", "").strip()
        if not def_name:
            continue
        recipe = f"Make_{def_name}"
        claves.append(SourceKey("RecipeDef", f"{recipe}.label",
                                f"make {label}" if label else "", path))
        claves.append(SourceKey("RecipeDef", f"{recipe}.description",
                                f"Make {label}." if label else "", path))
        claves.append(SourceKey("RecipeDef", f"{recipe}.jobString",
                                f"Making {label}." if label else "", path))
    return claves


def _synthesize_architect_categories(nodes: list[tuple[ET.Element, Path]]) -> list[SourceKey]:
    """Genera las claves `Architect_<defName>` de las categorías del arquitecto.

    Por cada `DesignationCategoryDef`, RimWorld crea en tiempo de carga una
    `KeyBindingCategoryDef` para el atajo de teclado de esa pestaña. Como pasa
    con `Make_<defName>`, no existe en ningún XML: los idiomas del mod la
    traducen y un extractor que no la conozca la daría por sobrante.

    El texto inglés lo compone el juego a partir del label de la categoría.
    """
    claves: list[SourceKey] = []
    for node, path in nodes:
        if node.tag != "DesignationCategoryDef":
            continue
        def_name = node.findtext("defName", "").strip()
        if not def_name:
            continue
        label = node.findtext("label", "").strip()
        categoria = f"Architect_{def_name}"
        claves.append(SourceKey("KeyBindingCategoryDef", f"{categoria}.label",
                                f"{label} tab" if label else "", path))
        claves.append(SourceKey(
            "KeyBindingCategoryDef", f"{categoria}.description",
            f'Key bindings for the "{label[:1].upper() + label[1:]}" section of '
            f"the architect menu" if label else "", path))
    return claves


def _collect_patched_def_nodes(patches_root: Path) -> list[tuple[ET.Element, Path]]:
    """Defs que un mod añade mediante `Patches/`, no mediante `Defs/`.

    Es el mecanismo habitual para el contenido condicional: un
    `PatchOperationFindMod` comprueba que otro mod esté presente y un
    `PatchOperationAdd` inyecta los Defs nuevos. LWM's Deep Storage añade así su
    nevera profunda cuando detecta RimFridge.

    Esos Defs se traducen igual que cualquier otro, pero solo existen si el mod
    condicionante está instalado; si no lo está, RimWorld registra la inyección
    como error de carga. Por eso se extraen, pero conviene decidir a conciencia
    si se traducen.

    Se buscan los `<value>` de cualquier operación, a cualquier profundidad, sin
    interpretar el `xpath`: basta con que el nodo parezca un Def.
    """
    nodes: list[tuple[ET.Element, Path]] = []
    for xml_path in sorted(patches_root.rglob("*.xml")):
        try:
            root = ET.parse(xml_path).getroot()
        except ET.ParseError:
            continue
        for value in root.iter("value"):
            for node in value:
                # Un Def se reconoce por llevar defName; así se descartan los
                # <value> que solo contienen un fragmento de campo suelto.
                if isinstance(node.tag, str) and node.find("defName") is not None:
                    nodes.append((node, xml_path))
    return nodes


def extract_keyed(mod_path: Path, language: str = "English") -> list[SourceKey]:
    """Extrae las claves `Keyed/` que el mod define en su idioma de origen.

    Estas son las cadenas que el código C# pide con `.Translate()`. No se
    deducen de los Defs: la única lista fiable es la carpeta `Keyed` del propio
    mod, que por convención está en inglés.

    Van con `def_type` vacío porque `Keyed/` no se organiza por tipo de Def:
    la identidad de la clave es su nombre a secas, igual que en `model.py`.
    """
    claves: list[SourceKey] = []
    for languages_dir in sorted(mod_path.rglob("Languages")):
        keyed = languages_dir / language / "Keyed"
        if not keyed.is_dir():
            continue
        for xml_path in sorted(keyed.rglob("*.xml")):
            try:
                root = ET.parse(xml_path).getroot()
            except ET.ParseError:
                continue
            for node in root:
                if not isinstance(node.tag, str):
                    continue
                claves.append(SourceKey("", node.tag, (node.text or "").strip(), xml_path))
    return claves


def extract_keys(mod_path: Path, version: str | None = None) -> list[SourceKey]:
    """Extrae todas las claves traducibles de un mod.

    `version` elige la carpeta versionada (p. ej. "1.6"). Sin ella se usa la
    versión más alta disponible, o la raíz si el mod no está versionado.
    """
    candidatas: list[Path] = []
    if version and (mod_path / version / "Defs").is_dir():
        candidatas.append(mod_path / version / "Defs")
    elif version is None:
        versionadas = sorted(
            (p for p in mod_path.iterdir()
             if p.is_dir() and p.name[0].isdigit() and (p / "Defs").is_dir()),
            key=lambda p: [int(x) for x in p.name.split(".") if x.isdigit()],
        )
        if versionadas:
            candidatas.append(versionadas[-1] / "Defs")

    if (mod_path / "Defs").is_dir():
        candidatas.append(mod_path / "Defs")

    # Los Defs añadidos por Patches se tratan igual que los declarados: el
    # mismo recorrido, la misma herencia. Solo cambia de dónde salen.
    for patches_root in (mod_path / version / "Patches" if version else None,
                         mod_path / "Patches"):
        if patches_root and patches_root.is_dir():
            candidatas.append(patches_root)

    claves: list[SourceKey] = []
    for root_dir in candidatas:
        es_patch = root_dir.name == "Patches"
        nodes = (_collect_patched_def_nodes(root_dir) if es_patch
                 else _collect_def_nodes(root_dir))
        _resolve_inheritance(nodes)

        for node, path in nodes:
            # Los Defs abstractos son plantillas: no llegan al juego y no se traducen
            if node.get("Abstract", "").lower() == "true":
                continue
            def_name = node.findtext("defName", "").strip()
            if not def_name:
                continue
            _walk(node, "", node.tag, def_name, path, claves)

        claves.extend(_synthesize_recipe_makers(nodes))
        claves.extend(_synthesize_architect_categories(nodes))

    claves.extend(extract_keyed(mod_path))

    # Dedup conservando el primero
    vistas: dict[str, SourceKey] = {}
    for clave in claves:
        vistas.setdefault(clave.id, clave)
    return list(vistas.values())
