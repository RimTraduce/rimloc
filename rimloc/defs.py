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
    "labelNoun", "labelAdjective", "customLabel", "permanentLabel",
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
        return f"{self.def_type}/{self.name}"


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


def _walk(node: ET.Element, prefix: str, def_type: str, def_name: str,
          path: Path, out: list[SourceKey]) -> None:
    """Recorre un Def emitiendo las claves traducibles que encuentra.

    Las listas se indexan por posición (`stages.0.label`), que es como RimWorld
    referencia sus elementos.
    """
    li_index = 0
    for child in node:
        if not isinstance(child.tag, str):
            continue

        if child.tag == "li":
            segment = str(li_index)
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

    claves: list[SourceKey] = []
    for defs_root in candidatas:
        nodes = _collect_def_nodes(defs_root)
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

    # Dedup conservando el primero
    vistas: dict[str, SourceKey] = {}
    for clave in claves:
        vistas.setdefault(clave.id, clave)
    return list(vistas.values())
