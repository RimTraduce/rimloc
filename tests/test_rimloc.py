"""Pruebas de rimloc.

Cada prueba corresponde a un fallo real encontrado en una traducción publicada
o a un falso positivo que la herramienta llegó a producir. Se ejecutan con la
librería estándar, sin instalar nada:

    python -m unittest discover tests
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rimloc import checks, defs
from rimloc.checks import Severity
from rimloc.cli import _guess_rename
from rimloc.model import load_language_folder


def _write(root: Path, relative: str, body: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _lang_data(*entries: str) -> str:
    inner = "\n".join(f"  {e}" for e in entries)
    return f'<?xml version="1.0" encoding="utf-8"?>\n<LanguageData>\n{inner}\n</LanguageData>\n'


class GenderTokenTests(unittest.TestCase):
    """La regla que detecta el bug más traicionero: token de género huérfano."""

    def _findings(self, def_type: str, entry: str):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Spanish"
            _write(root, f"DefInjected/{def_type}/T.xml", _lang_data(entry))
            return checks.check_gender_tokens(load_language_folder(root))

    def test_pawn_gender_con_argumento_posicional_es_error(self):
        """{PAWN_gender} en un texto que recibe {0}: RimWorld imprime el token."""
        found = self._findings(
            "DamageDef",
            "<X.deathMessage>{0} ha sido golpead{PAWN_gender ? o : a}.</X.deathMessage>",
        )
        self.assertEqual(len(found), 1)
        self.assertIs(found[0].severity, Severity.ERROR)
        self.assertEqual(found[0].rule, "genero-por-nombre")

    def test_gender_posicional_correcto_no_avisa(self):
        found = self._findings(
            "DamageDef",
            "<X.deathMessage>{0} ha sido golpead{0_gender ? o : a}.</X.deathMessage>",
        )
        self.assertEqual(found, [])

    def test_pawn_gender_sin_posicionales_es_valido(self):
        """En ThoughtDef el argumento llega con nombre: aquí PAWN_gender es correcto.

        Este es el falso positivo que hay que evitar: la regla no puede limitarse
        a prohibir PAWN_gender.
        """
        found = self._findings(
            "ThoughtDef",
            "<X.stages.0.label>borrach{PAWN_gender ? o : a}</X.stages.0.label>",
        )
        self.assertEqual(found, [])

    def test_gender_apunta_a_argumento_inexistente(self):
        found = self._findings(
            "DamageDef",
            "<X.deathMessage>{0} murió {1_gender ? solo : sola}.</X.deathMessage>",
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].rule, "genero-huerfano")


class DuplicateTests(unittest.TestCase):
    def test_mismo_defname_en_tipos_distintos_no_es_duplicado(self):
        """Un ThingDef y un HediffDef pueden llamarse igual y traducirse distinto.

        RimWorld los resuelve por carpeta. Agrupar solo por nombre de clave
        produce alarmas falsas.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Spanish"
            _write(root, "DefInjected/ThingDef/A.xml",
                   _lang_data("<Item.label>incubadora</Item.label>"))
            _write(root, "DefInjected/HediffDef/B.xml",
                   _lang_data("<Item.label>incubadora implantada</Item.label>"))
            self.assertEqual(checks.check_duplicates(load_language_folder(root)), [])

    def test_misma_clave_repetida_en_el_mismo_tipo_es_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Spanish"
            _write(root, "DefInjected/ThingDef/A.xml", _lang_data(
                "<Item.label>organ modification kit</Item.label>",
                "<Item.label>kit de modificación de órganos</Item.label>",
            ))
            found = checks.check_duplicates(load_language_folder(root))
            self.assertEqual(len(found), 1)
            self.assertIs(found[0].severity, Severity.ERROR)

    def test_los_comentarios_no_cuentan_como_claves(self):
        """La convención de estos archivos guarda el original dentro de comentarios."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Spanish"
            _write(root, "DefInjected/ThingDef/A.xml", _lang_data(
                "<!-- <Item.label>organ modification kit</Item.label> -->",
                "<Item.label>kit de modificación de órganos</Item.label>",
            ))
            folder = load_language_folder(root)
            self.assertEqual(len(folder.keys), 1)
            self.assertEqual(checks.check_duplicates(folder), [])


class DefExtractionTests(unittest.TestCase):
    def _defs(self, body: str) -> list:
        tmp = tempfile.mkdtemp()
        root = Path(tmp)
        _write(root, "Defs/T.xml", f'<?xml version="1.0"?>\n<Defs>\n{body}\n</Defs>\n')
        return defs.extract_keys(root)

    def test_recipe_maker_genera_claves_make(self):
        """Todo ThingDef con <recipeMaker> produce un RecipeDef Make_* traducible.

        No existe en ningún XML: un extractor que no lo sepa marca esas
        traducciones como obsoletas e invita a borrarlas.
        """
        keys = self._defs("""
          <ThingDef>
            <defName>Widget</defName>
            <label>widget</label>
            <recipeMaker><workSpeedStat>GeneralLaborSpeed</workSpeedStat></recipeMaker>
          </ThingDef>
        """)
        ids = {k.id for k in keys}
        self.assertIn("RecipeDef/Make_Widget.label", ids)
        self.assertIn("RecipeDef/Make_Widget.jobString", ids)

    def test_defs_abstractos_no_generan_claves(self):
        keys = self._defs("""
          <RecipeDef Name="Base" Abstract="True">
            <label>plantilla</label>
          </RecipeDef>
        """)
        self.assertEqual(keys, [])

    def test_los_hijos_heredan_campos_del_padre_abstracto(self):
        """Un hijo que no declara `description` la hereda, y hay que traducirla."""
        keys = self._defs("""
          <RecipeDef Name="Base" Abstract="True">
            <description>Descripción heredada.</description>
          </RecipeDef>
          <RecipeDef ParentName="Base">
            <defName>Hijo</defName>
            <label>hijo</label>
          </RecipeDef>
        """)
        ids = {k.id for k in keys}
        self.assertIn("RecipeDef/Hijo.label", ids)
        self.assertIn("RecipeDef/Hijo.description", ids)

    def test_las_listas_se_indexan_por_posicion(self):
        keys = self._defs("""
          <HediffDef>
            <defName>H</defName>
            <stages>
              <li><label>leve</label></li>
              <li><label>grave</label></li>
            </stages>
          </HediffDef>
        """)
        ids = {k.id for k in keys}
        self.assertIn("HediffDef/H.stages.0.label", ids)
        self.assertIn("HediffDef/H.stages.1.label", ids)


class RenameGuessTests(unittest.TestCase):
    class _Key:
        def __init__(self, def_type, name, value=""):
            self.def_type, self.name, self.value = def_type, name, value
            self.id = f"{def_type}/{name}"

    def test_detecta_un_renombrado_real(self):
        huerfano = self._Key("RecipeDef", "WCE2_HarvestNeutroamine.label")
        faltan = [self._Key("RecipeDef", "WCE2_HarvestNeutroamineGrowth.label")]
        guess = _guess_rename(huerfano, faltan)
        self.assertIsNotNone(guess)
        self.assertEqual(guess[0], "RecipeDef/WCE2_HarvestNeutroamineGrowth.label")

    def test_no_empareja_claves_sin_relacion(self):
        """Compartir el sufijo `.label` no basta: sugerir de más invita a
        reciclar una traducción en la clave equivocada."""
        huerfano = self._Key("RecipeDef", "WCE2_CutoutTongue.label")
        faltan = [self._Key("RecipeDef", "WCE2_HarvestNeutroamineGrowth.label")]
        self.assertIsNone(_guess_rename(huerfano, faltan))

    def test_no_empareja_entre_tipos_de_def_distintos(self):
        huerfano = self._Key("RecipeDef", "WCE2_Algo.label")
        faltan = [self._Key("HediffDef", "WCE2_Algo.label")]
        self.assertIsNone(_guess_rename(huerfano, faltan))


class TextHygieneTests(unittest.TestCase):
    def _folder(self, entry: str):
        tmp = tempfile.mkdtemp()
        root = Path(tmp) / "Spanish"
        _write(root, "DefInjected/ThingDef/A.xml", _lang_data(entry))
        return load_language_folder(root)

    def test_detecta_espacio_de_ancho_cero(self):
        folder = self._folder("<X.description>causados​ por PEM</X.description>")
        found = checks.check_invisible_chars(folder)
        self.assertEqual(len(found), 1)

    def test_glosario_marca_termino_proscrito(self):
        folder = self._folder("<X.label>daño EMP</X.label>")
        found = checks.check_glossary(folder, {"daño EMP": "daño PEM"})
        self.assertEqual(len(found), 1)
        self.assertIn("daño PEM", found[0].message)

    def test_clave_vacia_es_error(self):
        folder = self._folder("<X.label></X.label>")
        found = checks.check_empty(folder)
        self.assertEqual(len(found), 1)
        self.assertIs(found[0].severity, Severity.ERROR)


if __name__ == "__main__":
    unittest.main()
