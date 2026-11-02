# Sound

Descarga música de YouTube Music (solo la versión original: sin mixes, covers ni directos), la
organiza por género y la reproduce en una ventana propia con cola automática y crossfade.

> Sound no aloja ni distribuye música. Descarga a petición del usuario y guarda los archivos en su
> equipo. Lee [LEGAL.md](LEGAL.md) y [PRIVACY.md](PRIVACY.md) antes de usarlo.

## Instalación para usuarios (Windows 10/11)

1. Descarga `Sound-1.0.1-setup.exe` desde la página de *Releases* del repositorio.
2. Ejecútalo. Se instala **solo para tu usuario** (no pide administrador) en
   `%LOCALAPPDATA%\Programs\Sound`, con acceso directo en el menú Inicio.
3. Abre Sound y pulsa **Descargas → Diagnóstico** si algo no funciona.

No hace falta instalar Python: el instalador incluye el intérprete, Tcl/Tk y Node.js (YouTube lo
necesita para resolver los enlaces). **ffmpeg es opcional**: sin él no hay forma de onda real ni
normalización de volumen, pero la reproducción y las descargas funcionan igual.

Para desinstalar: Configuración → Aplicaciones → Sound. La biblioteca, el historial y las
preferencias **no se borran**; si quieres eliminarlos, borra `%LOCALAPPDATA%\Sound` y la carpeta
de música.

### Modo portable

Descarga el ZIP `Sound-windows.zip` del release, descomprímelo y crea un archivo vacío llamado
`portable.txt` junto a `Sound.exe`. Los datos se guardarán en `data\` dentro de esa carpeta.

## Primeros pasos y problemas con YouTube

YouTube cambia a menudo y a veces pide iniciar sesión:

- **«Sign in to confirm you're not a bot»**: abre **Descargas → Cookies de YouTube** y elige el
  navegador donde tengas la sesión de YouTube iniciada. Vuelve a pulsar **Reintentar**. Las
  cookies se leen en tu equipo y no se envían a ningún servidor de Sound.
- **«Unable to extract…»**: pulsa **Descargas → Actualizar yt-dlp** (en la versión instalada no
  hace falta pip) y reinicia la ventana.
- **Node.js**: va incluido en el instalador. En una instalación desde el código, instálalo tú.
- **Sin conexión**: la cola se queda pendiente y se reanuda sola cuando vuelve internet.

El **Diagnóstico** comprueba Node, ffmpeg, internet, cookies, espacio libre y permisos de la
carpeta de datos. Ahí mismo puedes abrir la carpeta de datos y el registro.

## Uso desde la línea de comandos (desarrollo)

| Comando | Qué hace |
|---|---|
| `sound buscar "texto" -n 10` | Busca canciones |
| `sound generos` | Lista los géneros del sidebar de YouTube Music |
| `sound genero rock -n 20 --por-artista 3` | Encola y descarga canciones nuevas de un género (`--ver` para solo mostrar la selección) |
| `sound prompt "música de los 80 en español" -n 50` | Pide música con una frase |
| `sound importar URL [-n 200] [--genero rock] [--ver]` | Importa un álbum, playlist, artista o canción |
| `sound reanudar` | Continúa la cola pendiente |
| `sound reintentar` | Vuelve a intentar las descargas fallidas |
| `sound estado` | Pendientes, hechas, fallidas, sin carátula y archivos que faltan |
| `sound exportar [ruta.m3u]` | Exporta la biblioteca a una lista M3U |
| `sound limpiar` | Quita de la biblioteca las canciones cuyo archivo ya no está en disco |
| `sound normalizar [-n 100]` | Mide el volumen de la biblioteca (necesita ffmpeg) |
| `sound actualizar` | Actualiza yt-dlp y ytmusicapi |
| `sound diagnostico` | Comprueba Node, ffmpeg, internet, cookies, datos y espacio |
| `sound recuperar [--desde informe.json] [--si]` | Reencola las reproducciones de un informe de recuperación |
| `sound historial [-n 20]` | Lo último que escuchaste |
| `sound top [-n 20] [--dias 30]` | Lo más escuchado |
| `sound duplicados [--borrar] [--si]` | Encuentra la misma canción con distinto id |
| `sound editar TEXTO --titulo … --artista … --album … --genero … --anio …` | Cambia las etiquetas |

Las canciones que llegan sin género se clasifican al descargar: se consulta iTunes por artista y
título y, si hay coincidencia, se etiquetan y se guardan en `por-genero/<género>/`. Si no, quedan
en `sueltas/`.

El historial se alimenta de las reproducciones: una canción cuenta cuando llevas 30 segundos (o
la mitad, si es más corta). En la barra lateral tienes **Recientes** y **Más escuchado**.

La cola vive en SQLite: si cierras la ventana o se corta la luz, lo pendiente se retoma. La
ventana y la CLI pueden trabajar sobre la misma cola a la vez.

## En la ventana

- **Selección múltiple**: clic, `Ctrl+clic` y `Mayús+clic`; `Ctrl+A` selecciona la vista actual.
- **Me gusta**: clic derecho → «Me gusta» (o `Ctrl+L`).
- **Duplicados y etiquetas**: clic derecho en la lista.
- **Música por frase**, **Importar** y **Descargas**: páginas dentro de la misma ventana.
  `Esc` o «← Biblioteca» vuelve.
- **Cookies de YouTube**: en Descargas, para resolver el aviso anti-bot.
- **Diagnóstico**: en Descargas, con comprobaciones del entorno y accesos a datos y registro.
- **Sesión**: al cerrar se guardan canción, posición y cola. Al abrir aparece «Continúa donde lo
  dejaste» y no suena nada hasta que pulses play.
- **Exportar y limpiar**: clic derecho en la lista.
- **Normalizar volumen (ReplayGain)**: clic derecho → «Normalizar volumen» (necesita ffmpeg).
- **Actualizar yt-dlp**: botón en Descargas, sin cerrar la ventana (reinicia después).

## Dónde se guarda todo

| Archivo | Variable | Contenido |
|---|---|---|
| `%LOCALAPPDATA%\Sound\` | `SOUND_DATA_DIR` | Carpeta de datos |
| `Music\Sound\` en tu carpeta de usuario | `SOUND_MUSIC_DIR` | Canciones (`por-genero/<género>/`, `sueltas/`) |
| `library.db` | `SOUND_DB` | Biblioteca, cola, historial y Me gusta (SQLite, WAL) |
| `genres_cache.json` | `SOUND_GENRE_CACHE` | Géneros de YouTube Music en caché |
| `sound.log` | `SOUND_LOG` | Registro de errores (rota a 512 KB, 2 copias) |
| `cache\waves\` | `SOUND_WAVE_DIR` | Onda real de cada canción |
| `ui_prefs.json` | `SOUND_UI_PREFS` | Tema, volumen, repetición, crossfade, cookies… |
| `ui_session.json` | `SOUND_UI_SESSION` | Canción, posición y cola de la última sesión |
| `cache\loudness.json` | `SOUND_LOUDNESS_CACHE` | Ganancia de volumen medida por canción |

Antes de migrar el esquema se guarda una copia `library.vN.bak.db` junto a la base de datos. Los
archivos de estado se escriben de forma atómica para que un corte no los corrompa.

### Ajustes por entorno

| Variable | Por defecto | Qué hace |
|---|---|---|
| `SOUND_PARALLEL` | `3` | Descargas simultáneas (1 a 8) |
| `SOUND_COOKIES_FROM_BROWSER` | — | `firefox`, `chrome`, `edge`… para la CLI |
| `SOUND_DATA_DIR` | `%LOCALAPPDATA%\Sound` | Carpeta de datos |
| `SOUND_MUSIC_DIR` | `%USERPROFILE%\Music\Sound` | Carpeta de música |
| `SOUND_PORTABLE` | — | `1` para el modo portable |
| `SOUND_NO_MIGRATE` | — | `1` para no migrar la disposición antigua |

## Desarrollo

Para ejecutar desde el código necesitas **Python 3.11+** y **Node.js** en el PATH (YouTube lo
exige para resolver los enlaces; el instalador ya lo incluye). ffmpeg es opcional.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"        # añade pytest, ruff y pyinstaller
pytest
ruff check app tests
```

Quedan disponibles `sound` (CLI) y `sound-ui` (ventana). También funcionan
`python -m app.cli` y `python -m app.ui`.

### Compilar el instalador

```powershell
.\packaging\build.ps1              # genera dist\Sound\Sound.exe
.\packaging\build.ps1 -Installer   # además genera dist\Sound-1.0.1-setup.exe
```

Requiere [Inno Setup 6](https://jrsoftware.org/isinfo.php) para el instalador. El script descarga
Node.js si falta y lo incluye en la build. La firma de código no se hace automáticamente: para
evitar avisos de SmartScreen hay que firmar `Sound.exe` y el instalador con un certificado.

### Estructura

```
app/
  cli.py            comandos
  cli_library.py    comandos sobre la biblioteca ya descargada
  core/
    config.py       rutas, modo portable y migración
    library.py      SQLite: pistas, cola, historial, Me gusta
    downloader.py   yt-dlp, reintentos, etiquetas, carátulas
    service.py      cola de descargas en segundo plano
    health.py       comprobaciones, diagnóstico y actualización
    errors.py       traducción de errores de yt-dlp
    ytsearch.py     búsqueda y metadatos de YouTube Music
    importer.py     álbumes, playlists, artistas y canciones
    genres.py       selección por género
    autogenre.py    clasificación con iTunes
    prompt.py       «música por frase»
    loudness.py     ReplayGain con ffmpeg
    waveform.py     ondas reales en caché
    filters.py      descarte de mixes, covers y directos
    atomic.py       escrituras atómicas
    log.py          registro rotativo
  ui/
    main.py         ventana (coordina las piezas)
    page.py         base de las páginas integradas
    playqueue.py    cola de reproducción
    downloads.py    puente con el servicio de descargas
    queue_panel.py  página de Descargas
    health_page.py  página de Diagnóstico
    import_page.py  página de Importar
    prompt_dialog.py página «Música por frase»
    player.py       reproductor Media Foundation
    prefs.py        preferencias
    session.py      sesión de reproducción
    diagnostics.py  captura de errores
    single_instance.py  una sola ventana
    theme.py        paletas y DPI
    widgets.py      controles dibujados
packaging/          icono, entrada del .exe y build.ps1
sound.spec          PyInstaller
installer.iss       Inno Setup
tests/              pytest (no usan red ni tocan tu biblioteca)
```

## Tests

```powershell
pytest
```

Los tests usan su propia carpeta temporal: no leen ni escriben en tu biblioteca, tu registro ni
tus preferencias. La integración con YouTube no se prueba con red real (los tests la simulan).

## Licencia

MIT. Consulta [LICENSE](LICENSE), [LEGAL.md](LEGAL.md) y [PRIVACY.md](PRIVACY.md).
