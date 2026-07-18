"""Reglas de validación de una traducción de RimWorld.

Cada regla nace de un fallo real encontrado en una traducción publicada. La idea
es que ningún error que ya hayamos visto una vez pueda volver a colarse.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .model import LanguageFolder, TranslationKey


class Severity(str, Enum):
    ERROR = "ERROR"      # se ve roto en el juego
    WARNING = "AVISO"    # incorrecto pero no rompe nada
    INFO = "INFO"        # cosmético o sugerencia


@dataclass
class Finding:
    severity: Severity
    rule: str
    message: str
    source: Path
    line: int = 0
    key: str = ""

    def format(self, root: Path | None = None) -> str:
        where = self.source
        if root:
            try:
                where = self.source.relative_to(root)
            except ValueError:
                pass
        location = f"{where}:{self.line}" if self.line else str(where)
        head = f"{self.severity.value:6} [{self.rule}] {location}"
        return f"{head}\n       {self.message}"


# --- Placeholders ------------------------------------------------------------

# Argumentos posicionales: {0}, {1}...
POSITIONAL = re.compile(r"\{(\d+)\}")
# Argumentos con nombre: {PAWN_labelShort}, {SURGEON_labelShort}...
NAMED = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
# Tokens de género: {0_gender ? o : a} o {PAWN_gender ? o : a}
GENDER = re.compile(r"\{([A-Za-z0-9_]+)_gender\s*\?[^}]*\}")


def check_gender_tokens(folder: LanguageFolder) -> list[Finding]:
    """El token de género debe referirse a un argumento que exista en el texto.

    RimWorld pasa los argumentos de dos formas: por posición (`{0}`) o por
    nombre (`{PAWN_labelShort}`). El token de género tiene que usar el mismo
    prefijo que el argumento del que quiere saber el género. Si un `deathMessage`
    recibe al personaje como `{0}` y el traductor escribe `{PAWN_gender ? o : a}`,
    RimWorld no puede resolver `PAWN` y **imprime el token literal en pantalla**.

    Es un fallo silencioso: el XML es válido y el juego arranca sin quejarse.
    """
    findings: list[Finding] = []
    for key in folder.keys:
        gender_refs = GENDER.findall(key.value)
        if not gender_refs:
            continue
        positional = set(POSITIONAL.findall(key.value))
        for ref in gender_refs:
            if ref.isdigit():
                # {0_gender} exige que exista {0} en el mismo texto
                if ref not in positional:
                    findings.append(Finding(
                        Severity.ERROR, "genero-huerfano",
                        f"El token {{{ref}_gender ...}} apunta al argumento {{{ref}}}, "
                        f"que no aparece en el texto.",
                        key.source, key.line, key.id,
                    ))
            elif positional:
                # Hay argumentos posicionales pero el género usa un nombre:
                # ese nombre casi con seguridad no está definido.
                nombre = ", ".join(f"{{{n}}}" for n in sorted(positional))
                findings.append(Finding(
                    Severity.ERROR, "genero-por-nombre",
                    f"El token {{{ref}_gender ...}} usa el nombre «{ref}», pero este texto "
                    f"recibe sus argumentos por posición ({nombre}). "
                    f"RimWorld no podrá resolverlo y mostrará el token tal cual. "
                    f"Usa {{{sorted(positional)[0]}_gender ? ... : ...}}.",
                    key.source, key.line, key.id,
                ))
    return findings


def check_placeholders(folder: LanguageFolder, originals: dict[str, str]) -> list[Finding]:
    """Los argumentos de la traducción deben coincidir con los del original.

    Perder un `{0}` deja un hueco en el texto del juego; añadir uno que no
    existe provoca una excepción de formato.
    """
    findings: list[Finding] = []
    for key in folder.keys:
        original = originals.get(key.id)
        if original is None:
            continue

        faltan = set(POSITIONAL.findall(original)) - set(POSITIONAL.findall(key.value))
        sobran = set(POSITIONAL.findall(key.value)) - set(POSITIONAL.findall(original))

        def sin_genero(texto: str) -> set[str]:
            # {0_gender ? o : a} no es un argumento con nombre, es un token
            return {n for n in NAMED.findall(texto) if not n.endswith("_gender")}

        faltan_n = sin_genero(original) - sin_genero(key.value)
        sobran_n = sin_genero(key.value) - sin_genero(original)

        for grupo, etiqueta in ((faltan | faltan_n, "falta"), (sobran | sobran_n, "sobra")):
            for arg in sorted(grupo):
                findings.append(Finding(
                    Severity.ERROR, f"placeholder-{etiqueta}",
                    f"El argumento {{{arg}}} {etiqueta} respecto al original en inglés.",
                    key.source, key.line, key.id,
                ))
    return findings


# --- Integridad estructural --------------------------------------------------

def check_duplicates(folder: LanguageFolder) -> list[Finding]:
    """Claves repetidas dentro del mismo tipo de Def.

    RimWorld conserva UNA de ellas. Si la primera quedó en inglés (por un
    comentario mal cerrado, por ejemplo), la traducción buena se descarta y el
    texto sale sin traducir sin que nada avise.
    """
    findings: list[Finding] = []
    for group in folder.duplicates():
        sitios = ", ".join(f"{k.source.name}:{k.line}" for k in group)
        findings.append(Finding(
            Severity.ERROR, "clave-duplicada",
            f"«{group[0].id}» está definida {len(group)} veces ({sitios}). "
            f"RimWorld se quedará con una sola.",
            group[0].source, group[0].line, group[0].id,
        ))
    return findings


def check_parse_errors(folder: LanguageFolder) -> list[Finding]:
    """XML mal formado: RimWorld descarta el archivo entero."""
    return [
        Finding(Severity.ERROR, "xml-invalido",
                f"El archivo no se puede parsear y RimWorld lo ignorará por completo: {msg}",
                path)
        for path, msg in folder.parse_errors
    ]


def check_empty(folder: LanguageFolder) -> list[Finding]:
    """Claves vacías o con stubs de TODO sin rellenar."""
    findings: list[Finding] = []
    for key in folder.keys:
        if not key.value:
            findings.append(Finding(
                Severity.ERROR, "clave-vacia",
                "La clave no tiene texto: en el juego se verá un hueco en blanco.",
                key.source, key.line, key.id,
            ))
        elif key.value.upper().startswith("TODO"):
            findings.append(Finding(
                Severity.WARNING, "todo-pendiente",
                f"Sigue con el stub sin traducir: «{key.value}»",
                key.source, key.line, key.id,
            ))
    return findings


# --- Higiene del texto -------------------------------------------------------

INVISIBLES = {
    "​": "espacio de ancho cero (U+200B)",
    "‌": "no-joiner (U+200C)",
    "‍": "joiner (U+200D)",
    "﻿": "BOM incrustado (U+FEFF)",
    " ": "espacio duro (U+00A0)",
}


def check_invisible_chars(folder: LanguageFolder) -> list[Finding]:
    """Caracteres invisibles colados al copiar y pegar desde una web o un chat."""
    findings: list[Finding] = []
    for key in folder.keys:
        for char, nombre in INVISIBLES.items():
            if char in key.value:
                pos = key.value.index(char)
                findings.append(Finding(
                    Severity.WARNING, "caracter-invisible",
                    f"Contiene un {nombre} en la posición {pos}. "
                    f"No se ve al editar pero afecta al texto.",
                    key.source, key.line, key.id,
                ))
    return findings


# Palabras cuya forma sin tilde NO existe en español: avisar es siempre correcto.
INEQUIVOCAS = {
    "victima": "víctima", "victimas": "víctimas",
    "mantendra": "mantendrá", "morira": "morirá", "podra": "podrá",
    "tendra": "tendrá", "sera": "será", "estara": "estará", "hara": "hará",
    "quimico": "químico", "quimica": "química", "quimicos": "químicos",
    "organos": "órganos", "craneo": "cráneo", "musculo": "músculo",
    "cirugia": "cirugía", "anestesia": None, "arteria": None,
    "dano": "daño", "danos": "daños", "extremidad": None,
    "dolencia": None, "protesis": "prótesis", "higado": "hígado",
    "estomago": "estómago", "corazon": "corazón", "pulmon": "pulmón",
    "cicatriz": None, "amputacion": "amputación", "operacion": "operación",
}

# Palabras que existen con y sin tilde con significados distintos. Aquí el
# aviso solo puede ser una sugerencia: «afecto» es un sustantivo válido, y
# «perdida» un adjetivo. Marcarlas como error produce el ruido que hace que
# la gente deje de leer los avisos.
AMBIGUAS = {
    "afecto": "afectó", "perdida": "pérdida", "mas": "más",
    "sangrara": "sangrará", "medico": "médico", "practica": "práctica",
    "publico": "público", "solo": "sólo (en desuso desde 2010)",
    "esta": "está", "el": "él", "si": "sí", "mi": "mí", "tu": "tú",
}


def check_missing_accents(folder: LanguageFolder) -> list[Finding]:
    """Palabras probablemente escritas sin tilde.

    Se separan en dos niveles a propósito. Las formas que sin tilde no existen
    en español se avisan; las que existen con otro significado solo se sugieren,
    porque decidirlo exige leer la frase. Un validador que grita por todo
    entrena a quien lo usa para ignorarlo.
    """
    findings: list[Finding] = []
    for key in folder.keys:
        palabras = set(re.findall(r"\b[a-záéíóúñü]+\b", key.value.lower()))

        for palabra in palabras & INEQUIVOCAS.keys():
            correccion = INEQUIVOCAS[palabra]
            if correccion:
                findings.append(Finding(
                    Severity.WARNING, "tilde-ausente",
                    f"«{palabra}» no existe sin tilde: debería ser «{correccion}».",
                    key.source, key.line, key.id,
                ))

        for palabra in palabras & AMBIGUAS.keys():
            findings.append(Finding(
                Severity.INFO, "tilde-dudosa",
                f"«{palabra}» existe, pero comprueba si aquí toca «{AMBIGUAS[palabra]}».",
                key.source, key.line, key.id,
            ))
    return findings


def check_spanish_punctuation(folder: LanguageFolder) -> list[Finding]:
    """Signos de apertura ¿ ¡ ausentes."""
    findings: list[Finding] = []
    for key in folder.keys:
        for cierre, apertura, nombre in (("?", "¿", "interrogación"), ("!", "¡", "exclamación")):
            # Contamos solo si hay desequilibrio claro entre aperturas y cierres
            n_cierre = key.value.count(cierre)
            n_apertura = key.value.count(apertura)
            if n_cierre > n_apertura:
                findings.append(Finding(
                    Severity.INFO, "signo-apertura",
                    f"Hay {n_cierre} «{cierre}» pero solo {n_apertura} «{apertura}»: "
                    f"falta algún signo de {nombre} de apertura.",
                    key.source, key.line, key.id,
                ))
    return findings


def check_label_case(folder: LanguageFolder) -> list[Finding]:
    """Los `label` van en minúscula: RimWorld los capitaliza según el contexto.

    Escribirlos en mayúscula produce cosas como «Un Kit De Modificación» en
    mitad de una frase.
    """
    findings: list[Finding] = []
    for key in folder.keys:
        if not key.name.endswith(".label") or not key.value:
            continue
        primera = key.value[0]
        if not primera.isupper():
            continue
        # Los nombres propios y las siglas sí llevan mayúscula
        if key.value.split()[0].isupper():
            continue
        findings.append(Finding(
            Severity.INFO, "label-mayuscula",
            f"El label empieza en mayúscula («{key.value[:40]}»). "
            f"Por convención van en minúscula; el juego los capitaliza solo.",
            key.source, key.line, key.id,
        ))
    return findings


# --- Glosario ----------------------------------------------------------------

def check_glossary(folder: LanguageFolder, forbidden: dict[str, str]) -> list[Finding]:
    """Términos proscritos por el glosario del proyecto.

    `forbidden` mapea término prohibido -> término canónico. Sirve para que una
    incoherencia detectada una vez (por ejemplo «EMP» donde debe ir «PEM») no
    vuelva a aparecer en la siguiente actualización.
    """
    findings: list[Finding] = []
    for key in folder.keys:
        lowered = key.value.lower()
        for termino, canonico in forbidden.items():
            if re.search(rf"\b{re.escape(termino.lower())}\b", lowered):
                findings.append(Finding(
                    Severity.WARNING, "glosario",
                    f"Usa «{termino}»; el término canónico del proyecto es «{canonico}».",
                    key.source, key.line, key.id,
                ))
    return findings


# --- Orquestación ------------------------------------------------------------

def run_all(
    folder: LanguageFolder,
    originals: dict[str, str] | None = None,
    forbidden: dict[str, str] | None = None,
) -> list[Finding]:
    """Ejecuta todas las reglas aplicables y devuelve los hallazgos ordenados."""
    findings: list[Finding] = []
    findings += check_parse_errors(folder)
    findings += check_duplicates(folder)
    findings += check_gender_tokens(folder)
    findings += check_empty(folder)
    findings += check_invisible_chars(folder)
    findings += check_missing_accents(folder)
    findings += check_spanish_punctuation(folder)
    findings += check_label_case(folder)

    if originals:
        findings += check_placeholders(folder, originals)
    if forbidden:
        findings += check_glossary(folder, forbidden)

    orden = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
    findings.sort(key=lambda f: (orden[f.severity], str(f.source), f.line))
    return findings
