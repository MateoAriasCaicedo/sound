# Sound

Descarga música de YouTube Music (solo la versión original: sin mixes, covers ni directos), la
organiza por género y la reproduce en una ventana propia con cola automática y crossfade.

## Requisitos

- Python 3.11 o superior.
- Windows 10/11: la ventana usa Media Foundation (las dependencias `winrt-*` se instalan solas).
- Pillow (se instala solo): dibuja la interfaz y las portadas.
- ffmpeg en el PATH (opcional): calcula la forma de onda real de cada canción y permite normalizar
  el volumen. Sin él, la barra de progreso usa una onda estándar y la normalización queda inactiva.
- **Node.js en el PATH**: YouTube exige un entorno JavaScript para resolver los enlaces. Sin él,
  muchas descargas fallan (`sound` avisa al arrancar).

## Instalación

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"        # [dev] añade pytest
```

Queda disponible `sound` (línea de comandos) y `sound-ui` (ventana). También funcionan
`python -m app.cli` y `python -m app.ui`.

## Uso

| Comando | Qué hace |
|---|---|
| `sound buscar "texto" -n 10` | Busca canciones |
| `sound generos` | Lista los géneros oficiales de YouTube Music |
| `sound genero rock -n 20 --por-artista 3` | Encola y descarga canciones nuevas de un género (`--ver` para solo mostrar la selección) |
| `sound importar URL [-n 200] [--genero rock] [--ver]` | Importa un álbum, playlist, artista o canción desde un enlace de YouTube Music |
| `sound reanudar` | Continúa la cola pendiente |
| `sound reintentar` | Vuelve a intentar las descargas fallidas |
| `sound estado` | Pendientes, hechas, fallidas, canciones sin carátula y archivos que faltan |
| `sound exportar [ruta.m3u]` | Exporta la biblioteca a una lista M3U |
| `sound limpiar` | Quita de la biblioteca las canciones cuyo archivo ya no está en disco |
| `sound normalizar [-n 100]` | Mide el volumen de la biblioteca para nivelar las canciones (necesita ffmpeg) |
| `sound actualizar` | Actualiza yt-dlp y ytmusicapi |

Sobre lo que ya tienes descargado:

| Comando | Qué hace |
|---|---|
| `sound playlist crear\|ver\|agregar\|quitar\|renombrar\|borrar NOMBRE …` | Playlists propias. `agregar` acepta el id o un texto del título/artista (si coincide con varias te muestra los ids) |
| `sound historial [-n 20]` | Lo último que escuchaste |
| `sound top [-n 20] [--dias 30]` | Lo más escuchado |
| `sound duplicados [--borrar] [--si]` | Encuentra la misma canción con distinto id (p. ej. álbum y videoclip). Con `--borrar` pide confirmación y deja la mejor de cada grupo (con carátula, álbum y año) |
| `sound editar TEXTO --titulo … --artista … --album … --genero … --anio …` | Cambia las etiquetas del archivo y de la biblioteca (`""` quita álbum, género o año). No renombra el archivo |

El historial se alimenta de las reproducciones de la ventana: una canción cuenta cuando llevas
escuchados 30 segundos (o la mitad, si es más corta). En la barra lateral tienes **Recientes** y
**Más escuchado**, y los comandos `historial` y `top` leen esos mismos datos.

La cola vive en SQLite: si cierras la ventana o se corta la luz, lo pendiente se retoma. La
ventana y la CLI pueden trabajar sobre la misma cola a la vez.

## En la ventana

Además de la biblioteca y del buscador:

- **Playlists**: en la barra lateral, «＋ Nueva playlist». Clic derecho sobre una playlist para
  reproducirla, renombrarla o borrarla. Clic derecho sobre canciones para añadirlas a una lista o
  quitarlas, y «Mover arriba/abajo» para ordenarlas.
- **Me gusta**: clic derecho sobre canciones descargadas → «Me gusta» (o `Ctrl+L`). Se marcan con ♥
  y aparecen en la vista «Me gusta» de la barra lateral.
- **Duplicados y etiquetas**: clic derecho en la lista → «Buscar canciones repetidas…» (deja la
  mejor versión de cada grupo) y «Editar etiquetas…» para título, artista, álbum, género y año.
- **Música por frase** y **Descargas**: los iconos de arriba abren páginas dentro de la misma
  ventana (no ventanas emergentes). La flecha «← Biblioteca» o `Esc` vuelve a la lista.
- **Importar**: el icono del enlace pide una URL de álbum, playlist, artista o canción y encola
  todo lo que encuentre.
- **Sesión**: al cerrar se guardan la canción, la posición y la cola. Al abrir aparece «Continúa
  donde lo dejaste» y no suena nada hasta que pulses play.
- **Exportar y limpiar**: clic derecho en la lista → «Exportar biblioteca a M3U…» o «Quitar
  archivos que faltan…».
- **Normalizar volumen (ReplayGain)**: clic derecho en la lista → «Normalizar volumen». Mide cada
  canción con ffmpeg y la nivela a −14 LUFS. Sin ffmpeg la opción queda inactiva. Para medir toda
  la biblioteca de una vez: `sound normalizar`.
- **Actualizar yt-dlp**: botón en la página de Descargas, sin cerrar la ventana (reinícala después).

**Si las descargas empiezan a fallar todas** con «Unable to extract…», yt-dlp se quedó viejo
(YouTube cambia a menudo): ejecuta `sound actualizar` y luego `sound reintentar`. La ventana lo
avisa sola tras varios fallos seguidos de ese tipo, y la CLI lo sugiere al terminar.

## Ajustes por entorno

Además de las rutas de la tabla siguiente:

| Variable | Por defecto | Qué hace |
|---|---|---|
| `SOUND_PARALLEL` | `3` | Descargas simultáneas (1 a 8) |
| `SOUND_COOKIES_FROM_BROWSER` | — | `firefox`, `chrome`… para vídeos con restricción de edad |

## Dónde se guarda todo

Junto al proyecto, salvo que lo cambies con variables de entorno:

| Archivo | Variable | Contenido |
|---|---|---|
| `Music/` | `SOUND_MUSIC_DIR` | Canciones (`por-genero/<género>/`, `sueltas/`) |
| `library.db` | `SOUND_DB` | Biblioteca, cola, playlists, historial y Me gusta (SQLite, modo WAL) |
| `genres_cache.json` | `SOUND_GENRE_CACHE` | Géneros de YouTube Music en caché |
| `sound.log` | `SOUND_LOG` | Registro de errores (rota a 512 KB, 2 copias) |
| `cache/waves/` | `SOUND_WAVE_DIR` | Onda real ya analizada de cada canción (hasta 600 niveles, ~600 bytes) |
| `ui_prefs.json` | — | Tema, volumen, repetición, crossfade… |
| `ui_session.json` | — | Canción, posición y cola de la última sesión |
| `cache/loudness.json` | `SOUND_LOUDNESS_CACHE` | Ganancia de volumen medida por canción |

Las ondas se analizan una sola vez, en segundo plano: primero la canción que suena y luego el
resto de la biblioteca. Mientras llega la real se muestra una onda estándar, la misma para todas
las canciones. Si borras `cache/waves/` (o instalas ffmpeg después), se vuelven a generar solas al
abrir la ventana; sin ffmpeg la onda estándar se queda en su sitio.

Cuando algo falle sin mensaje claro, mira `sound.log`: ahí queda la traza completa de lo que la
ventana y las descargas absorben para seguir funcionando.

## Estructura

```
app/
  cli.py            comandos
  core/             sin interfaz: library (SQLite), downloader, service (cola en
                    segundo plano), ytsearch, importer, genres, filters, streaming, loudness,
                    health, log
  ui/
    main.py         ventana (coordina las piezas)
    page.py         base de las páginas integradas (no ventanas emergentes)
    playqueue.py    cola de reproducción: orden, repetición, barajado
    downloads.py    descargas desde la ventana
    prompt_dialog.py  página «Música por frase»
    queue_panel.py  página de la cola de descargas
    prefs.py        preferencias
    player.py       reproductor
    tag_dialog.py   diálogo para editar etiquetas
    session.py      guarda y recupera la sesión de reproducción
tests/              pytest (no usan red ni tocan tu biblioteca)
```

## Tests

```powershell
pytest
```
