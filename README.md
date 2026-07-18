# rimloc

Utilidades de línea de comandos para mantener traducciones de mods de **RimWorld**.

No pretende reemplazar la herramienta que trae el propio juego, sino cubrir lo que
esta no hace: validar de forma automática, comparar contra el mod original sin
arrancar RimWorld y mantener sincronizadas dos variantes de un mismo idioma.

## Por qué existe

[RimTrans](https://github.com/RimWorld-zh/RimTrans), la herramienta de referencia
durante años, está abandonada: su última versión es de **2018** y apunta a RimWorld 1.0.
Cargaba los ensamblados del mod por reflexión, así que se rompía con cualquier mod
que llevara C# propio.

Mientras tanto, RimWorld incorporó en el menú principal **«Clean up translation files»**
y **«Save translation report»**, que generan las plantillas de traducción resolviendo
los Defs por reflexión real. Para *extraer*, eso es mejor que cualquier herramienta
externa y es lo que deberías usar.

Lo que el juego no ofrece:

- Validar en **integración continua**, sin abrir RimWorld.
- Detectar **tokens de género mal formados**, que el juego acepta sin rechistar
  y luego imprime literalmente en pantalla.
- Mantener a la vez **`Spanish` y `SpanishLatin`** sin copiar y pegar a mano.
- Imponer un **glosario** de términos canónicos del proyecto.

Eso es rimloc.

## Instalación

Requiere Python 3.10 o superior. **No tiene dependencias**: solo librería estándar.
Es deliberado — una herramienta de traducción debería seguir funcionando dentro de
cinco años sin que nadie la mantenga, y cada dependencia es una forma de que eso no ocurra.

```bash
pip install "git+https://github.com/RimTraduce/rimloc"
```

O para trabajar sobre el código:

```bash
git clone https://github.com/RimTraduce/rimloc
cd rimloc
pip install -e .
```

O sin instalar nada, desde la carpeta del repo:

```bash
python -m rimloc <comando>
```

## Comandos

### `validate` — busca errores

```bash
rimloc validate ruta/al/mod-traduccion --glossary glosario.json
rimloc validate ruta/al/mod --source ruta/al/mod-original --version 1.6
```

Devuelve código de salida **1** si encuentra errores, para poder encadenarlo en CI.

| Regla | Gravedad | Qué detecta |
|---|---|---|
| `xml-invalido` | ERROR | XML mal formado: RimWorld ignora el archivo entero |
| `clave-duplicada` | ERROR | La misma clave dos veces en un tipo de Def; el juego se queda con una |
| `genero-por-nombre` | ERROR | `{PAWN_gender ? o : a}` en un texto que recibe argumentos por posición |
| `genero-huerfano` | ERROR | `{0_gender ...}` sin un `{0}` al que referirse |
| `clave-vacia` | ERROR | Traducción vacía: hueco en blanco en pantalla |
| `placeholder-falta` / `-sobra` | ERROR | Los argumentos no cuadran con el original inglés |
| `todo-pendiente` | AVISO | Stubs `TODO` sin rellenar |
| `caracter-invisible` | AVISO | Espacios de ancho cero y similares colados al copiar y pegar |
| `tilde-ausente` | AVISO | Palabras frecuentes sin tilde |
| `glosario` | AVISO | Términos proscritos por el glosario del proyecto |
| `signo-apertura` | INFO | Faltan `¿` o `¡` |
| `label-mayuscula` | INFO | Un `label` empieza en mayúscula |

Las sugerencias (INFO) se ocultan salvo que pases `--all`.

### `diff` — compara con el mod original

```bash
rimloc diff mod-traduccion mod-original --version 1.6
```

Informa de cobertura, claves sin traducir con su texto inglés, y claves que ya no
existen. Cuando detecta que una clave se **renombró**, lo indica con el porcentaje
de parecido para que puedas reciclar la traducción en vez de rehacerla.

### `sync` — replica una variante sobre otra

```bash
rimloc sync ruta/al/mod --from Spanish --to SpanishLatin --dry-run
```

Pensado para traducir en español neutro una sola vez y publicar ambas variantes.
También señala archivos huérfanos que quedaron en destino.

### `deploy` — instálalo para probarlo

```bash
rimloc deploy ruta/al/mod --force
```

Copia el mod a la carpeta `Mods` de RimWorld (detecta la instalación de Steam sola).
Excluye `.git`, `*.md` y demás material que no debe viajar al juego.

### `stats` — recuento

```bash
rimloc stats ruta/al/mod
```

## Sobre el alcance de `diff`

La fuente de verdad definitiva es RimWorld: solo el juego resuelve los Defs por
reflexión real. `diff` parsea XML, lo que le permite funcionar sin arrancar el juego
y en CI, a costa de algún caso límite. Implementa explícitamente las trampas que más
duelen:

- Los **Defs abstractos** (`Abstract="True"`) no generan claves, pero sus hijos
  **heredan** campos que sí hay que traducir.
- Todo `ThingDef` con `<recipeMaker>` genera un `RecipeDef` **`Make_<defName>`** que
  no existe en ningún XML. Sin esta regla, sus traducciones parecen obsoletas.
- La identidad de una clave es **`TipoDef/defName.campo`**, no `defName.campo`: un
  mismo `defName` puede existir como `ThingDef` y `HediffDef` con traducciones
  distintas y legítimas.

Ante una discrepancia entre `rimloc diff` y el informe del juego, manda el juego.

## Glosario

Un JSON que fija los términos canónicos del proyecto:

```json
{
  "terms": [
    {
      "canonical": "daño PEM",
      "forbidden": ["daño EMP", "carga EMP"],
      "note": "La sigla se traduce siempre."
    }
  ]
}
```

Sirve para que una incoherencia detectada una vez no vuelva a colarse en la
siguiente actualización.

## Desarrollo

```bash
python -m unittest discover tests
```

Cada prueba corresponde a un fallo real encontrado en una traducción publicada, o a
un falso positivo que la herramienta llegó a producir.

## Licencia

MIT.
