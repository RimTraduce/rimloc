"""Interfaz de línea de comandos de rimloc."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from difflib import SequenceMatcher
from pathlib import Path

from . import checks, defs
from .checks import Severity
from .model import SPANISH_VARIANTS, load_language_folder
# `preview` se puede importar siempre: no toca Pillow hasta que se llama a una
# de sus funciones. Aquí solo hacen falta los nombres de los temas, para --help.
from .preview import TEMAS as PREVIEW_THEMES

# Rutas habituales de instalación de RimWorld en Steam
RIMWORLD_PATHS = (
    r"C:\Program Files (x86)\Steam\steamapps\common\RimWorld",
    r"C:\Program Files\Steam\steamapps\common\RimWorld",
    r"D:\SteamLibrary\steamapps\common\RimWorld",
    r"~/Library/Application Support/Steam/steamapps/common/RimWorld",
    r"~/.steam/steam/steamapps/common/RimWorld",
)


#: Material de desarrollo que no debe viajar al juego ni al Workshop.
#: RimWorld ignora lo que no reconoce, pero un mod publicado con el glosario,
#: los workflows y el README dentro es ruido para quien lo descargue.
DEV_ONLY = (
    ".git", ".github", ".gitignore", ".gitattributes",
    "*.md", "*.json", "*.py", "*.toml", "*.yml", "*.yaml",
    "__pycache__", ".vscode", ".idea", ".DS_Store", "Thumbs.db",
)


def find_rimworld() -> Path | None:
    for raw in RIMWORLD_PATHS:
        path = Path(os.path.expanduser(raw))
        if (path / "Version.txt").is_file():
            return path
    return None


def _language_dirs(mod: Path, lang: str | None) -> list[Path]:
    base = mod / "Languages"
    if not base.is_dir():
        return []
    if lang:
        target = base / lang
        return [target] if target.is_dir() else []
    return [p for p in sorted(base.iterdir()) if p.is_dir()]


def _load_glossary(path: Path | None) -> tuple[dict[str, str], tuple[str, ...]]:
    """Carga el glosario.

    Devuelve el mapa {"término prohibido": "término canónico"} y la lista `keep`
    de nombres propios que se dejan en inglés a propósito (títulos de mods,
    marcas), dentro de los cuales no se avisa de nada.
    """
    if not path:
        return {}, ()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"aviso: no se pudo leer el glosario {path}: {exc}", file=sys.stderr)
        return {}, ()
    forbidden: dict[str, str] = {}
    for entry in data.get("terms", []):
        canonical = entry.get("canonical", "")
        for wrong in entry.get("forbidden", []):
            forbidden[wrong] = canonical
    return forbidden, tuple(data.get("keep", []))


# --- Comandos ----------------------------------------------------------------

def cmd_validate(args: argparse.Namespace) -> int:
    mod = Path(args.mod).resolve()
    originals: dict[str, str] = {}
    if args.source:
        originals = {
            k.id: k.english
            for k in defs.extract_keys(Path(args.source).resolve(), args.version)
        }

    forbidden, keep = _load_glossary(Path(args.glossary) if args.glossary else None)

    dirs = _language_dirs(mod, args.lang)
    if not dirs:
        print(f"No se encontró ninguna carpeta de idioma en {mod / 'Languages'}", file=sys.stderr)
        return 2

    total_errors = total_warnings = 0
    for lang_dir in dirs:
        folder = load_language_folder(lang_dir)
        findings = checks.run_all(folder, originals or None, forbidden or None, keep)

        errores = sum(1 for f in findings if f.severity is Severity.ERROR)
        avisos = sum(1 for f in findings if f.severity is Severity.WARNING)
        infos = len(findings) - errores - avisos
        total_errors += errores
        total_warnings += avisos

        print(f"\n=== {lang_dir.name} — {len(folder.keys)} claves ===")
        if not findings:
            print("  Sin incidencias.")
            continue

        for finding in findings:
            if finding.severity is Severity.INFO and not args.all:
                continue
            print(f"  {finding.format(mod)}")

        resumen = f"  → {errores} error(es), {avisos} aviso(s), {infos} sugerencia(s)"
        if infos and not args.all:
            resumen += "  (usa --all para ver las sugerencias)"
        print(resumen)

    print(f"\nTotal: {total_errors} error(es), {total_warnings} aviso(s).")
    if total_errors:
        return 1
    return 0


#: Por debajo de este parecido entre defNames no se sugiere un renombrado.
#: Sugerir de más es peor que no sugerir: invita a reciclar una traducción
#: en una clave que no le corresponde.
RENAME_THRESHOLD = 0.75


def _guess_rename(orphan, missing) -> tuple[str, float] | None:
    """Propone a qué clave nueva pudo renombrarse una que ya no existe.

    Exige mismo tipo de Def y mismo campo (`.label` con `.label`), y compara los
    defName por similitud. Así `WCE2_HarvestNeutroamine` → `WCE2_HarvestNeutroamineGrowth`
    se detecta, mientras que dos claves que solo comparten el sufijo `.description`
    no se emparejan.
    """
    def partes(name: str) -> tuple[str, str]:
        head, _, tail = name.partition(".")
        return head, tail

    huerfano_def, huerfano_campo = partes(orphan.name)
    mejor: tuple[str, float] | None = None

    for candidato in missing:
        if candidato.def_type != orphan.def_type:
            continue
        cand_def, cand_campo = partes(candidato.name)
        if cand_campo != huerfano_campo:
            continue
        parecido = SequenceMatcher(None, huerfano_def, cand_def).ratio()
        if parecido >= RENAME_THRESHOLD and (mejor is None or parecido > mejor[1]):
            mejor = (candidato.id, parecido)

    return mejor


def cmd_diff(args: argparse.Namespace) -> int:
    mod = Path(args.mod).resolve()
    source = Path(args.source).resolve()

    originales = {k.id: k for k in defs.extract_keys(source, args.version)}
    dirs = _language_dirs(mod, args.lang)
    if not dirs:
        print(f"No se encontró ninguna carpeta de idioma en {mod / 'Languages'}", file=sys.stderr)
        return 2

    for lang_dir in dirs:
        folder = load_language_folder(lang_dir)
        traducidas = folder.by_id

        faltan = [k for kid, k in originales.items() if kid not in traducidas]
        sobran = [k for kid, k in traducidas.items() if kid not in originales]
        cobertura = 100.0 * (len(originales) - len(faltan)) / len(originales) if originales else 0.0

        print(f"\n=== {lang_dir.name} ===")
        print(f"  Claves en el mod original : {len(originales)}")
        print(f"  Traducidas                : {len(traducidas)}")
        print(f"  Cobertura                 : {cobertura:.1f}%")

        if faltan:
            print(f"\n  SIN TRADUCIR ({len(faltan)}):")
            for key in sorted(faltan, key=lambda k: k.id):
                texto = key.english[:70] + ("..." if len(key.english) > 70 else "")
                print(f"    {key.id}")
                print(f"        EN: {texto}")

        if sobran:
            print(f"\n  YA NO EXISTEN EN EL ORIGINAL ({len(sobran)}):")
            for key in sorted(sobran, key=lambda k: k.id):
                print(f"    {key.id}")
                print(f"        ES: {key.value[:70]}")
                candidato = _guess_rename(key, faltan)
                if candidato:
                    nuevo, parecido = candidato
                    print(f"        ¿renombrada? → {nuevo}  ({parecido:.0%} de parecido)")

        if not faltan and not sobran:
            print("  La traducción está al día.")

    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """Replica una variante de idioma sobre otra.

    Pensado para el par Spanish / SpanishLatin cuando se traduce en neutro y las
    dos variantes comparten texto. Evita el copia-pega manual que garantiza que
    antes o después se desincronicen.
    """
    mod = Path(args.mod).resolve()
    origen = mod / "Languages" / args.source_lang
    destino = mod / "Languages" / args.target_lang

    if not origen.is_dir():
        print(f"No existe la carpeta de origen: {origen}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"[simulación] {origen.name} → {destino.name}")

    copiados = 0
    for src in sorted(origen.rglob("*.xml")):
        rel = src.relative_to(origen)
        dst = destino / rel
        distintos = not dst.exists() or dst.read_bytes() != src.read_bytes()
        if not distintos:
            continue
        copiados += 1
        print(f"  {'[simulado] ' if args.dry_run else ''}{rel}")
        if not args.dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    # Archivos que sobran en destino: quedaron de una versión anterior
    huerfanos = [
        dst for dst in sorted(destino.rglob("*.xml"))
        if not (origen / dst.relative_to(destino)).exists()
    ] if destino.is_dir() else []

    for huerfano in huerfanos:
        print(f"  sobra en {destino.name}: {huerfano.relative_to(destino)}")

    verbo = "se copiarían" if args.dry_run else "copiados"
    print(f"\n{copiados} archivo(s) {verbo}; {len(huerfanos)} huérfano(s) en destino.")
    return 0


def cmd_deploy(args: argparse.Namespace) -> int:
    """Instala el mod en la carpeta Mods de RimWorld para probarlo."""
    mod = Path(args.mod).resolve()
    rimworld = Path(args.rimworld).resolve() if args.rimworld else find_rimworld()

    if not rimworld:
        print("No se encontró RimWorld. Indícalo con --rimworld <ruta>.", file=sys.stderr)
        return 2

    destino = rimworld / "Mods" / (args.name or mod.name)
    if destino.exists() and not args.force:
        print(f"{destino} ya existe. Usa --force para sobrescribirlo.", file=sys.stderr)
        return 2

    if destino.exists():
        shutil.rmtree(destino)
    shutil.copytree(mod, destino, ignore=shutil.ignore_patterns(*DEV_ONLY))
    print(f"Instalado en {destino}")
    print("Actívalo en el menú de mods del juego (después del mod original).")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    mod = Path(args.mod).resolve()
    for lang_dir in _language_dirs(mod, args.lang):
        folder = load_language_folder(lang_dir)
        por_tipo: dict[str, int] = {}
        for key in folder.keys:
            por_tipo[key.def_type or "(Keyed/Strings)"] = por_tipo.get(key.def_type or "(Keyed/Strings)", 0) + 1
        print(f"\n=== {lang_dir.name} — {len(folder.keys)} claves ===")
        for tipo, n in sorted(por_tipo.items(), key=lambda kv: -kv[1]):
            print(f"  {tipo:<24} {n:>5}")
        palabras = sum(len(k.value.split()) for k in folder.keys)
        print(f"  {'':<24} {'':>5}\n  Palabras traducidas: {palabras}")
    return 0


def _nombre_del_mod(mod: Path) -> str:
    """Lee `<name>` de About.xml; si no se puede, usa el nombre de la carpeta."""
    about = mod / "About" / "About.xml"
    try:
        import xml.etree.ElementTree as ET
        nombre = (ET.parse(about).getroot().findtext("name") or "").strip()
        if nombre:
            return nombre
    except (OSError, ET.ParseError):
        pass
    return mod.name


def cmd_preview(args: argparse.Namespace) -> int:
    # Pillow se comprueba aquí, no al importar: es lo único de rimloc que lo
    # necesita, y el resto de la herramienta tiene que seguir funcionando sin él.
    # `preview` sí se puede importar siempre; son sus funciones las que lo usan.
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("El comando «preview» necesita Pillow, que no viene con rimloc:\n"
              "    pip install 'rimloc[preview]'\n"
              "El resto de comandos funciona sin él.", file=sys.stderr)
        return 2

    from . import preview

    mod = Path(args.mod).resolve()
    destino = Path(args.out) if args.out else mod / "About" / "Preview.png"
    titulo = args.title or _nombre_del_mod(mod)

    if destino.exists() and not args.force:
        print(f"Ya existe {destino}. Usa --force para sobrescribirlo.", file=sys.stderr)
        return 1

    # Si se da el mod original, se busca su preview para enmarcarlo. Es lo
    # preferible: la carátula se reconoce como ese mod y hereda su paleta.
    fuente: Path | None = None
    if args.source:
        candidato = Path(args.source).resolve() / "About" / "Preview.png"
        if candidato.is_file():
            fuente = candidato
        else:
            print(f"aviso: no hay Preview.png en {candidato.parent}; "
                  "se genera la carátula sin imagen de partida.", file=sys.stderr)

    comun = dict(tema=args.theme, autor=args.author,
                 coleccion=args.collection, codigo=args.badge)
    if fuente:
        preview.generar_con_marco(destino, titulo, fuente, **comun)
    else:
        preview.generar(destino, titulo, **comun)

    print(f"Carátula escrita en {destino}  ({destino.stat().st_size // 1024} kB)")
    print(f"  tema: {args.theme}   título: {titulo}")
    print(f"  base: {fuente if fuente else 'sin imagen de partida'}")
    return 0


# --- Parser ------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rimloc",
        description="Utilidades para mantener traducciones de mods de RimWorld.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="Busca errores en una traducción")
    p.add_argument("mod", help="Carpeta del mod de traducción")
    p.add_argument("--source", help="Carpeta del mod original, para cotejar placeholders")
    p.add_argument("--version", help="Versión del mod original a leer (p. ej. 1.6)")
    p.add_argument("--lang", help="Limitar a un idioma (p. ej. Spanish)")
    p.add_argument("--glossary", help="Glosario JSON del proyecto")
    p.add_argument("--all", action="store_true", help="Mostrar también las sugerencias")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("diff", help="Compara la traducción con el mod original")
    p.add_argument("mod", help="Carpeta del mod de traducción")
    p.add_argument("source", help="Carpeta del mod original")
    p.add_argument("--version", help="Versión del mod original a leer (p. ej. 1.6)")
    p.add_argument("--lang", help="Limitar a un idioma")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("sync", help="Replica una variante de idioma sobre otra")
    p.add_argument("mod", help="Carpeta del mod de traducción")
    p.add_argument("--from", dest="source_lang", default=SPANISH_VARIANTS[0])
    p.add_argument("--to", dest="target_lang", default=SPANISH_VARIANTS[1])
    p.add_argument("--dry-run", action="store_true", help="Solo mostrar qué haría")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("deploy", help="Instala el mod en RimWorld para probarlo")
    p.add_argument("mod", help="Carpeta del mod de traducción")
    p.add_argument("--rimworld", help="Ruta de RimWorld (se detecta sola por defecto)")
    p.add_argument("--name", help="Nombre de la carpeta destino")
    p.add_argument("--force", action="store_true", help="Sobrescribir si ya existe")
    p.set_defaults(func=cmd_deploy)

    p = sub.add_parser("stats", help="Recuento de claves y palabras")
    p.add_argument("mod", help="Carpeta del mod de traducción")
    p.add_argument("--lang", help="Limitar a un idioma")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("preview", help="Genera la carátula del Workshop (necesita Pillow)")
    p.add_argument("mod", help="Carpeta del mod de traducción")
    p.add_argument("--source", help="Carpeta del mod original, para enmarcar su Preview.png")
    p.add_argument("--title", help="Título a rotular (por defecto, el <name> de About.xml)")
    p.add_argument("--theme", default="rimworld", choices=sorted(PREVIEW_THEMES),
                   help="Estilo de la carátula")
    p.add_argument("--author", default="", help="Quien firma la traducción")
    p.add_argument("--collection", default="", help="Nombre de la colección")
    p.add_argument("--badge", default="ES", help="Texto del sello circular ('' para quitarlo)")
    p.add_argument("--out", help="Ruta de salida (por defecto About/Preview.png)")
    p.add_argument("--force", action="store_true", help="Sobrescribir si ya existe")
    p.set_defaults(func=cmd_preview)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
