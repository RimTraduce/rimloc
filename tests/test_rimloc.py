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

    def test_los_comps_se_indexan_por_nombre_de_clase(self):
        """Un comp se referencia por su clase, no por su posición.

        El índice numérico se rompe cuando otro mod reordena los comps: Combat
        Extended reemplaza HediffComp_TendDuration y desplaza todo lo demás.
        RimWorld nombra el comp, no sus propiedades: HediffCompProperties_X se
        referencia como HediffComp_X.
        """
        keys = self._defs("""
          <HediffDef>
            <defName>H</defName>
            <comps>
              <li Class="HediffCompProperties_TendDuration">
                <labelTendedWell>bandaged</labelTendedWell>
              </li>
              <li Class="HediffCompProperties_GetsPermanent">
                <permanentLabel>torture scar</permanentLabel>
              </li>
            </comps>
          </HediffDef>
        """)
        ids = {k.id for k in keys}
        self.assertIn("HediffDef/H.comps.HediffComp_GetsPermanent.permanentLabel", ids)
        self.assertIn("HediffDef/H.comps.HediffComp_TendDuration.labelTendedWell", ids)
        self.assertNotIn("HediffDef/H.comps.1.permanentLabel", ids)

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


class KeyedExtractionTests(unittest.TestCase):
    """Las Keyed no se deducen de los Defs: hay que leer las del propio mod.

    LWM's Deep Storage tiene 109 claves en `Languages/English/Keyed/` y solo 36
    en Defs. Mientras `extract_keys` las ignoró, `diff` daba por completa una
    traducción a la que le faltaban tres cuartas partes del texto.
    """

    def test_extrae_las_keyed_del_idioma_de_origen(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod = Path(tmp) / "mod"
            _write(mod, "Languages/English/Keyed/UI.xml",
                   _lang_data("<LWM_DS_Total>Total: {0}</LWM_DS_Total>"))
            keys = {k.id: k for k in defs.extract_keys(mod)}
            self.assertIn("LWM_DS_Total", keys)
            self.assertEqual(keys["LWM_DS_Total"].english, "Total: {0}")

    def test_la_identidad_de_una_keyed_no_lleva_barra(self):
        """Debe casar con `TranslationKey.id`, que para Keyed es el nombre solo.

        Con `/LWM_DS_Total` a un lado y `LWM_DS_Total` al otro, `diff` contaba
        cada clave como ausente y sobrante a la vez.
        """
        clave = defs.SourceKey("", "LWM_DS_Total", "Total: {0}", Path("x.xml"))
        self.assertEqual(clave.id, "LWM_DS_Total")

    def test_no_confunde_las_keyed_de_otro_idioma(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod = Path(tmp) / "mod"
            _write(mod, "Languages/German/Keyed/UI.xml",
                   _lang_data("<SoloAleman>Lagerung</SoloAleman>"))
            self.assertEqual(defs.extract_keys(mod), [])


class GeneratedDefTests(unittest.TestCase):
    """Defs que RimWorld fabrica en tiempo de carga y no están en ningún XML."""

    def _defs_de(self, cuerpo: str, carpeta: str = "Defs"):
        with tempfile.TemporaryDirectory() as tmp:
            mod = Path(tmp) / "mod"
            _write(mod, f"{carpeta}/D.xml", f"<Defs>{cuerpo}</Defs>")
            return {k.id: k for k in defs.extract_keys(mod)}

    def test_cada_categoria_del_arquitecto_genera_su_atajo(self):
        """RimWorld crea una KeyBindingCategoryDef por DesignationCategoryDef.

        Los otros idiomas del mod la traducen; sin sintetizarla, `diff` la daría
        por sobrante e invitaría a borrarla.
        """
        keys = self._defs_de("""
          <DesignationCategoryDef>
            <defName>LWM_DS_Storage</defName>
            <label>storage</label>
          </DesignationCategoryDef>
        """)
        self.assertIn("KeyBindingCategoryDef/Architect_LWM_DS_Storage.label", keys)
        self.assertEqual(
            keys["KeyBindingCategoryDef/Architect_LWM_DS_Storage.label"].english,
            "storage tab")
        desc = keys["KeyBindingCategoryDef/Architect_LWM_DS_Storage.description"]
        self.assertIn('"Storage"', desc.english)

    def test_extrae_el_grouping_label_de_un_edificio(self):
        """`building.groupingLabel` es traducible y estaba fuera de la lista.

        Lo reclamó el informe del juego en Deep Storage. No hay redacción
        oficial que consultar: el juego base no usa el campo en ningún Def, solo
        los mods, así que ninguna traducción vanilla lo delataba.
        """
        keys = self._defs_de("""
          <ThingDef>
            <defName>LWM_DS_RimFridge_Refrigerator</defName>
            <label>Deep Refrigerator</label>
            <building>
              <groupingLabel>Deep Refrigerator</groupingLabel>
            </building>
          </ThingDef>
        """)
        self.assertIn(
            "ThingDef/LWM_DS_RimFridge_Refrigerator.building.groupingLabel", keys)

    def test_extrae_defs_anadidos_por_un_patch(self):
        """El contenido condicional se declara en Patches/, no en Defs/.

        LWM's Deep Storage añade así su nevera profunda cuando detecta
        RimFridge: dos claves que solo viven dentro de un PatchOperationAdd.
        """
        keys = self._defs_de("", carpeta="Defs")  # el mod base, vacío
        self.assertEqual(keys, {})

        with tempfile.TemporaryDirectory() as tmp:
            mod = Path(tmp) / "mod"
            _write(mod, "Patches/RimFridge.xml", """
              <Patch>
                <Operation Class="PatchOperationFindMod">
                  <mods><li>RimFridge Updated</li></mods>
                  <match Class="PatchOperationAdd">
                    <xpath>/Defs</xpath>
                    <value>
                      <ThingDef>
                        <defName>LWM_DS_RimFridge_Refrigerator</defName>
                        <label>Deep Refrigerator</label>
                      </ThingDef>
                    </value>
                  </match>
                </Operation>
              </Patch>
            """)
            keys = {k.id for k in defs.extract_keys(mod)}
            self.assertIn("ThingDef/LWM_DS_RimFridge_Refrigerator.label", keys)

    def test_no_inventa_claves_de_anteproyecto_ni_armazon(self):
        """RimWorld genera X_Blueprint y X_Frame, pero NO se traducen.

        Compone su nombre con el label ya traducido del ThingDef más un sufijo
        de Keyed (BlueprintLabelExtra, FrameLabelExtra). El español oficial no
        trae ni una de estas claves en sus 707 archivos.

        Importa como regresión porque RimTrans sí las generaba: los mods
        traducidos con él las arrastran a decenas, y en LWM's Deep Storage son
        más de un tercio del archivo ruso sin hacer nada.
        """
        keys = self._defs_de("""
          <ThingDef>
            <defName>LWM_BigShelf</defName>
            <label>big shelf</label>
          </ThingDef>
        """)
        self.assertIn("ThingDef/LWM_BigShelf.label", keys)
        for sobra in ("_Blueprint", "_Blueprint_Install", "_Frame"):
            self.assertNotIn(f"ThingDef/LWM_BigShelf{sobra}.label", keys)

    def test_un_patch_sin_defs_no_aporta_claves(self):
        """Los patches de compatibilidad solo retocan campos existentes.

        Un `<value>` con un fragmento suelto no es un Def y no debe emitir nada:
        de lo contrario cada uno de los 26 patches del mod inventaría claves.
        """
        with tempfile.TemporaryDirectory() as tmp:
            mod = Path(tmp) / "mod"
            _write(mod, "Patches/Compat.xml", """
              <Patch>
                <Operation Class="PatchOperationAdd">
                  <xpath>/Defs/ThingDef[defName="Ajeno"]/comps</xpath>
                  <value><li Class="LWM.DeepStorage.Properties"><label>nope</label></li></value>
                </Operation>
              </Patch>
            """)
            self.assertEqual(defs.extract_keys(mod), [])


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

    def test_glosario_respeta_los_nombres_propios(self):
        """«stack» está prohibido, pero «Stack XXL» es el nombre de un mod.

        El glosario de Deep Storage prohíbe «stack» (canónico: «pila») y a la
        vez manda dejar los títulos de otros mods sin traducir. Sin la lista
        `keep`, la regla delataba lo que ella misma ordena escribir.
        """
        folder = self._folder(
            "<X.description>No lo actives si usas Stack XXL.</X.description>")
        self.assertEqual(
            checks.check_glossary(folder, {"stack": "pila"}, keep=("Stack XXL",)), [])

    def test_el_nombre_propio_no_tapa_el_termino_suelto(self):
        """La excepción protege el nombre, no la palabra en todo el archivo."""
        folder = self._folder(
            "<X.description>Con Stack XXL cada stack ocupa más.</X.description>")
        found = checks.check_glossary(folder, {"stack": "pila"}, keep=("Stack XXL",))
        self.assertEqual(len(found), 1)

    def test_tilde_inequivoca_es_aviso(self):
        folder = self._folder("<X.description>La victima grita.</X.description>")
        found = [f for f in checks.check_missing_accents(folder) if f.rule == "tilde-ausente"]
        self.assertEqual(len(found), 1)
        self.assertIs(found[0].severity, Severity.WARNING)

    def test_tilde_ambigua_solo_sugiere(self):
        """«afecto» es un sustantivo válido: no puede marcarse como aviso.

        La primera versión de la regla lo trataba igual que a las formas que no
        existen, y eso llenaba la salida de ruido.
        """
        folder = self._folder("<X.description>Le tiene afecto.</X.description>")
        found = checks.check_missing_accents(folder)
        self.assertTrue(found)
        self.assertTrue(all(f.severity is Severity.INFO for f in found))

    def test_avisa_de_comps_referenciados_por_indice(self):
        folder = self._folder("<H.comps.2.permanentLabel>cicatriz</H.comps.2.permanentLabel>")
        found = checks.check_comp_index(folder, {"ThingDef/H.comps.HediffComp_GetsPermanent.permanentLabel": "torture scar"})
        self.assertEqual(len(found), 1)
        self.assertIs(found[0].severity, Severity.WARNING)

    def test_no_avisa_de_comps_por_nombre(self):
        folder = self._folder(
            "<H.comps.HediffComp_GetsPermanent.permanentLabel>cicatriz"
            "</H.comps.HediffComp_GetsPermanent.permanentLabel>")
        self.assertEqual(checks.check_comp_index(folder, {"ThingDef/H.comps.HediffComp_GetsPermanent.permanentLabel": "x"}), [])

    def test_clave_vacia_es_error(self):
        folder = self._folder("<X.label></X.label>")
        found = checks.check_empty(folder)
        self.assertEqual(len(found), 1)
        self.assertIs(found[0].severity, Severity.ERROR)


class DeployTests(unittest.TestCase):
    def test_no_copia_material_de_desarrollo(self):
        """Un mod publicado no debe llevar dentro el glosario ni los workflows.

        La primera versión solo excluía .git, *.md y .github, y colaba el
        glosario.json en la carpeta de mods del juego.
        """
        import argparse

        from rimloc.cli import cmd_deploy

        with tempfile.TemporaryDirectory() as tmp:
            mod = Path(tmp) / "mimod"
            _write(mod, "About/About.xml", "<ModMetaData><name>X</name></ModMetaData>")
            _write(mod, "Languages/Spanish/DefInjected/ThingDef/A.xml",
                   _lang_data("<X.label>algo</X.label>"))
            _write(mod, "glosario.json", "{}")
            _write(mod, "README.md", "# doc")
            _write(mod, ".github/workflows/ci.yml", "name: ci")

            rimworld = Path(tmp) / "RimWorld"
            (rimworld / "Mods").mkdir(parents=True)
            (rimworld / "Version.txt").write_text("1.6", encoding="utf-8")

            code = cmd_deploy(argparse.Namespace(
                mod=str(mod), rimworld=str(rimworld), name="prueba", force=True))
            self.assertEqual(code, 0)

            destino = rimworld / "Mods" / "prueba"
            self.assertTrue((destino / "About/About.xml").exists())
            self.assertTrue((destino / "Languages").is_dir())
            for sobra in ("glosario.json", "README.md", ".github"):
                self.assertFalse((destino / sobra).exists(), f"{sobra} no debería copiarse")


class ListasCompletasTests(unittest.TestCase):
    """Claves que traducen una lista entera en vez de un texto suelto.

    RimWorld admite sustituir toda una lista declarando la clave sin índice y
    metiendo dentro los `<li>`. Es lo que recomienda el propio informe («this
    list allows full-list translation») y lo único viable cuando la traducción
    no tiene el mismo número de elementos que el original.
    """

    LISTA = (
        "<W.generalRules.rulesStrings>"
        "<li>subject->el camino de la innovación</li>"
        "<li>subject_story->reveló verdades simples</li>"
        "</W.generalRules.rulesStrings>"
    )

    def _folder(self, entry: str):
        tmp = tempfile.mkdtemp()
        root = Path(tmp) / "Spanish"
        _write(root, "DefInjected/ResearchProjectDef/R.xml", _lang_data(entry))
        return load_language_folder(root)

    def test_una_lista_no_es_una_clave_vacia(self):
        """El nodo no lleva texto propio: lo suyo está en los <li>.

        Mientras el modelo no lo supo, cada bloque de lista se contaba como
        clave vacía y `validate` daba 6 errores sobre una traducción correcta.
        """
        folder = self._folder(self.LISTA)
        self.assertEqual(checks.check_empty(folder), [])

    def test_un_elemento_vacio_si_es_error(self):
        folder = self._folder(
            "<W.generalRules.rulesStrings><li>algo</li><li></li>"
            "</W.generalRules.rulesStrings>")
        found = checks.check_empty(folder)
        self.assertEqual(len(found), 1)
        self.assertIs(found[0].severity, Severity.ERROR)

    def test_la_lista_cubre_las_claves_con_indice(self):
        """El mod original las declara con índice; la lista las sustituye todas.

        Sin esto, `diff` daba por ausentes las 450 que la lista ya traduce.
        """
        folder = self._folder(self.LISTA)
        ids = folder.by_id
        self.assertIn("ResearchProjectDef/W.generalRules.rulesStrings", ids)
        self.assertIn("ResearchProjectDef/W.generalRules.rulesStrings.0", ids)
        self.assertIn("ResearchProjectDef/W.generalRules.rulesStrings.1", ids)
        self.assertNotIn("ResearchProjectDef/W.generalRules.rulesStrings.2", ids)

    def test_el_contenido_sigue_pasando_las_reglas_de_texto(self):
        """Una lista no puede ser un agujero por donde colar cualquier cosa."""
        folder = self._folder(
            "<W.generalRules.rulesStrings><li>daño EMP</li>"
            "</W.generalRules.rulesStrings>")
        self.assertEqual(len(checks.check_glossary(folder, {"daño EMP": "daño PEM"})), 1)

    def test_extrae_rulesStrings_del_mod_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod = Path(tmp) / "mod"
            _write(mod, "Defs/R.xml", """
              <Defs>
                <ResearchProjectDef>
                  <defName>W</defName>
                  <generalRules>
                    <rulesStrings>
                      <li>subject-&gt;innovation</li>
                      <li>subject_story-&gt;unveiled truths</li>
                    </rulesStrings>
                  </generalRules>
                </ResearchProjectDef>
              </Defs>
            """)
            ids = {k.id for k in defs.extract_keys(mod)}
            self.assertIn("ResearchProjectDef/W.generalRules.rulesStrings.0", ids)
            self.assertIn("ResearchProjectDef/W.generalRules.rulesStrings.1", ids)


class ClavesExternasTests(unittest.TestCase):
    """Claves que el juego pide y que este parser no puede ver.

    Otro mod se las inyecta en tiempo de carga: las `generalRules` de los
    proyectos de WCE2 las pone Vanilla Expanded Framework, así que no están en
    ningún XML del mod y `diff` las daba por sobrantes —457 traducciones
    correctas señaladas para borrar—.
    """

    def test_los_patrones_evitan_el_falso_sobrante(self):
        import json as _json

        from rimloc.cli import _claves_externas

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "glosario.json"
            cfg.write_text(_json.dumps({
                "claves_externas": ["ResearchProjectDef/WCE2_*.generalRules.rulesStrings*"]
            }), encoding="utf-8")

            patrones = _claves_externas(cfg)
            from fnmatch import fnmatch
            self.assertTrue(any(fnmatch(
                "ResearchProjectDef/WCE2_CrudeTorture.generalRules.rulesStrings.7", p)
                for p in patrones))
            # Y no debe tapar lo que sí sobra de verdad
            self.assertFalse(any(fnmatch(
                "ThingDef/WCE2_Borrado.label", p) for p in patrones))

    def test_sin_glosario_no_hay_patrones(self):
        from rimloc.cli import _claves_externas
        self.assertEqual(_claves_externas(None), ())


class CarpetasCondicionalesTests(unittest.TestCase):
    """`LoadFolders.xml` permite cargar una carpeta solo si otro mod está activo.

    Es la forma de traducir Defs que otro mod condiciona —Deep Storage declara su
    nevera solo si detecta RimFridge— sin que salgan como errores de carga para
    quien no lo tenga. Pero esas traducciones existen, y la herramienta tiene que
    verlas: mientras solo miró `Languages/` en la raíz, quedaban sin validar.
    """

    def _mod(self, tmp: str) -> Path:
        mod = Path(tmp) / "mimod"
        _write(mod, "Languages/Spanish/DefInjected/ThingDef/A.xml",
               _lang_data("<LWM_Safe.label>caja fuerte</LWM_Safe.label>"))
        _write(mod, "RimFridge/Languages/Spanish/DefInjected/ThingDef/B.xml",
               _lang_data("<LWM_Fridge.label>refrigerador</LWM_Fridge.label>"))
        _write(mod, "LoadFolders.xml",
               '<loadFolders><v1.6><li>/</li>'
               '<li IfModActive="rimfridge.kv.rw">RimFridge</li></v1.6></loadFolders>')
        return mod

    def test_encuentra_las_carpetas_condicionales(self):
        from rimloc.model import find_language_roots

        with tempfile.TemporaryDirectory() as tmp:
            mod = self._mod(tmp)
            raices = {str(r.relative_to(mod)) for r in find_language_roots(mod)}
            self.assertEqual(raices, {"Languages", str(Path("RimFridge/Languages"))})

    def test_un_idioma_repartido_cuenta_como_uno_solo(self):
        """Dos carpetas del mismo idioma no son dos traducciones a medias."""
        from rimloc.cli import _language_dirs

        with tempfile.TemporaryDirectory() as tmp:
            grupos = _language_dirs(self._mod(tmp), None)
            self.assertEqual(len(grupos), 1, "debería haber un único idioma")
            idioma, carpetas = grupos[0]
            self.assertEqual(idioma, "Spanish")
            self.assertEqual(len(carpetas), 2)

    def test_carga_fusionada_ve_todas_las_claves(self):
        from rimloc.cli import _language_dirs
        from rimloc.model import load_language

        with tempfile.TemporaryDirectory() as tmp:
            _, carpetas = _language_dirs(self._mod(tmp), "Spanish")[0]
            folder = load_language(carpetas)
            ids = {k.id for k in folder.keys}
            self.assertIn("ThingDef/LWM_Safe.label", ids)
            self.assertIn("ThingDef/LWM_Fridge.label", ids)

    def test_no_mira_dentro_de_git(self):
        """`.git` guarda copias de los archivos; contarlas duplicaría claves."""
        from rimloc.model import find_language_roots

        with tempfile.TemporaryDirectory() as tmp:
            mod = self._mod(tmp)
            _write(mod, ".git/modules/x/Languages/Spanish/A.xml", _lang_data("<X.label>x</X.label>"))
            for raiz in find_language_roots(mod):
                self.assertNotIn(".git", raiz.parts)

    def test_sync_replica_tambien_la_condicional(self):
        """Si solo se sincronizara la raíz, la otra variante saldría incompleta."""
        import argparse

        from rimloc.cli import cmd_sync

        with tempfile.TemporaryDirectory() as tmp:
            mod = self._mod(tmp)
            code = cmd_sync(argparse.Namespace(
                mod=str(mod), source_lang="Spanish",
                target_lang="SpanishLatin", dry_run=False))
            self.assertEqual(code, 0)
            self.assertTrue(
                (mod / "Languages/SpanishLatin/DefInjected/ThingDef/A.xml").exists())
            self.assertTrue(
                (mod / "RimFridge/Languages/SpanishLatin/DefInjected/ThingDef/B.xml").exists(),
                "la carpeta condicional también debe sincronizarse")


class PreviewTests(unittest.TestCase):
    """La carátula del Workshop. Pillow es opcional: sin él, estas se saltan."""

    def setUp(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("Pillow no instalado (extra opcional 'preview')")

    def test_el_modulo_se_importa_sin_pillow(self):
        """`cli` importa `preview` siempre, así que no puede tocar PIL al cargar.

        Si algún día alguien sube el `from PIL import ...` al principio del
        módulo, rimloc entero dejaría de arrancar sin Pillow —justo lo que el
        extra opcional pretende evitar—.
        """
        import inspect

        from rimloc import preview

        cabecera = inspect.getsource(preview).split("def _cargar_fuente")[0]
        self.assertNotIn("from PIL", cabecera)
        self.assertNotIn("import PIL", cabecera)

    def test_genera_una_imagen_del_tamano_previsto(self):
        from PIL import Image

        from rimloc import preview

        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "Preview.png"
            preview.generar(destino, "LWM's Deep Storage [ES]", autor="Ghost_Ranger")
            self.assertTrue(destino.exists())
            with Image.open(destino) as img:
                self.assertEqual(img.size, (preview.ANCHO, preview.ALTO))
            # Steam rechaza los previews de más de 1 MB.
            self.assertLess(destino.stat().st_size, 1_000_000)

    def test_un_titulo_larguisimo_no_se_sale(self):
        """Sirve de plantilla para toda la colección o no sirve de nada."""
        from rimloc import preview

        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "p.png"
            preview.generar(
                destino,
                "Vanilla Genetics Expanded - More Lab Stuff and Even More Words [ES]",
            )
            self.assertTrue(destino.exists())

    def test_el_titulo_pierde_el_sufijo_de_idioma(self):
        """«[ES]» está en el nombre del mod, pero la carátula ya dice el idioma."""
        from rimloc import preview

        with tempfile.TemporaryDirectory() as tmp:
            # No se puede leer el texto pintado, pero sí comprobar que dos
            # títulos que solo difieren en el sufijo dan la misma imagen.
            a = preview.generar(Path(tmp) / "a.png", "Deep Storage [ES]")
            b = preview.generar(Path(tmp) / "b.png", "Deep Storage")
            self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_enmarca_el_preview_del_mod_original(self):
        """Se reutiliza la imagen del mod para que la carátula lo represente."""
        from PIL import Image

        from rimloc import preview

        with tempfile.TemporaryDirectory() as tmp:
            origen = Path(tmp) / "original.png"
            Image.new("RGB", (620, 620), (40, 90, 140)).save(origen)

            destino = Path(tmp) / "Preview.png"
            preview.generar_con_marco(destino, "Deep Storage [ES]", origen,
                                      autor="Ghost_Ranger")
            with Image.open(destino) as img:
                self.assertEqual(img.size, (preview.ANCHO, preview.ALTO))
            self.assertLess(destino.stat().st_size, 1_000_000)

    def test_el_marco_no_deforma_el_original(self):
        """Encajar, no estirar: un preview cuadrado no puede salir aplastado.

        Se comprueba con una imagen de mitades de color muy distinto: si se
        deformara o recortara mal, la frontera dejaría de caer en el centro de
        la zona nítida.
        """
        from PIL import Image

        from rimloc import preview

        with tempfile.TemporaryDirectory() as tmp:
            origen = Path(tmp) / "original.png"
            src = Image.new("RGB", (400, 400), (255, 0, 0))
            src.paste(Image.new("RGB", (400, 200), (0, 0, 255)), (0, 200))
            src.save(origen)

            destino = Path(tmp) / "p.png"
            preview.generar_con_marco(destino, "X", origen, codigo="")
            with Image.open(destino) as img:
                pixeles = img.convert("RGB").load()
                # La zona nítida es cuadrada y está centrada horizontalmente.
                alto_util = preview.ALTO - 132 - 60
                cx = preview.ANCHO // 2
                arriba = pixeles[cx, 30 + alto_util // 4]
                abajo = pixeles[cx, 30 + alto_util * 3 // 4]
            self.assertGreater(arriba[0], arriba[2], "la mitad de arriba debe ser roja")
            self.assertGreater(abajo[2], abajo[0], "la mitad de abajo debe ser azul")

    def test_nombre_del_mod_sale_de_about(self):
        from rimloc.cli import _nombre_del_mod

        with tempfile.TemporaryDirectory() as tmp:
            mod = Path(tmp) / "carpeta-fea"
            _write(mod, "About/About.xml",
                   "<ModMetaData><name>LWM's Deep Storage [ES]</name></ModMetaData>")
            self.assertEqual(_nombre_del_mod(mod), "LWM's Deep Storage [ES]")

    def test_nombre_del_mod_cae_a_la_carpeta_si_no_hay_about(self):
        from rimloc.cli import _nombre_del_mod

        with tempfile.TemporaryDirectory() as tmp:
            mod = Path(tmp) / "lwm-deepstorage-es"
            mod.mkdir()
            self.assertEqual(_nombre_del_mod(mod), "lwm-deepstorage-es")


if __name__ == "__main__":
    unittest.main()
