# Cambios

## Sin publicar

### Linux
- **La ventana y la CLI funcionan en Linux.** El reproductor usa GStreamer (`playbin`) en vez de
  Media Foundation: reproducción, pausa, búsqueda, volumen, streaming y crossfade. Hacen falta
  `python3-gi` y los plugins de GStreamer (ver el README).
- La rueda del ratón desplaza con los botones 4 y 5 de X11, el tema sigue al del sistema
  (GNOME/GTK), el icono de la ventana se convierte desde el `.ico` y **Iniciar con el sistema**
  escribe `~/.config/autostart/sound.desktop`.
- Las actualizaciones automáticas de Sound siguen siendo solo para Windows; el Diagnóstico
  comprueba GStreamer en lugar de los componentes de Windows.
- **Ejecutable de Linux en GitHub Actions**: cada push compila
  `Sound-<versión>-linux-x86_64.tar.gz` (Python, Tk, GStreamer y Node incluidos) y las etiquetas
  `v*` lo publican junto al instalador de Windows.

## 1.4.2 — 2026-11-21

### Vista en grande
- **La pestaña «Letra» ya no queda vacía** cuando una canción no tiene letra: lo dice claro
  (puede ser instrumental, o que LRCLIB no la tenga), cuenta algo de la canción (género,
  duración, cuántas veces la has escuchado y desde cuándo está en tu biblioteca) y enseña las
  tres siguientes de la cola, con un enlace a la cola entera. Si no se pudo buscar por falta de
  conexión, ofrece volver a intentarlo.

## 1.4.1 — 2026-11-21

### Vista en grande
- **La radio también se ve en grande**: el logo de la emisora sobre su color, su nombre, la
  ciudad y los géneros, y «En directo · 12:34». A la derecha, las emisoras de la lista desde la
  que la elegiste, con la que suena marcada: un clic cambia de emisora. El corazón la guarda en
  tus favoritas.
- **La línea de la letra que suena va en color** (el de la portada o, si no tiene, el de Sound):
  se ve de un vistazo por dónde va la canción.
- **Las letras vienen activadas de serie** y la vista en grande abre en «Letra». Se desactivan
  en Ajustes › Reproducción; si ya usabas Sound, se respeta lo que tenías.

### Arreglado
- **Letras que no avanzaban con la canción.** Si la versión exacta solo tenía el texto en LRCLIB
  (pasaba con títulos como «I Ran (So Far Away) (Main Version)»), salía sin tiempos. Ahora se
  busca otra versión de la misma canción con letra sincronizada y casi la misma duración. Las
  letras ya guardadas sin tiempos se vuelven a mirar una vez.
- Los nombres de emisora con todas sus frecuencias entre paréntesis («Blu Radio, señal nacional
  (HJCK, 89.9 MHz FM, …)») se acortan en la barra y en la vista en grande.

## 1.4.0 — 2026-11-21

### Navegación
- **Atrás y Adelante, como en un navegador**: botones ‹ › junto al buscador, `Alt+←` / `Alt+→`
  y los botones laterales del ratón. Recorren todo (Inicio, secciones, listas, artistas, álbumes,
  búsquedas y páginas como Ajustes) y, al volver, cada sitio queda como estaba: desplazamiento,
  selección y filtro de década.
- **Ruta sobre el título** en las páginas de artista y álbum («Todo › Fleetwood Mac»): cada tramo
  lleva a ese nivel. Volver a pulsar la sección del lateral también lleva a su raíz.
- **El lateral ya no se tapa**: Ajustes, Importar, Descargas y las demás páginas se abren a su
  derecha, y su enlace de vuelta dice adónde lleva («← Inicio», «← Rumours»…) en vez de
  «Biblioteca».
- Al borrar la búsqueda se vuelve a donde se estaba antes de buscar. `Esc` cierra primero lo que
  está encima (pantalla completa, Reproduciendo, la cola) y luego vuelve atrás desde un álbum, un
  artista o una página.
- Para mover canciones de una lista en la cuadrícula se usa `Alt+↑/↓`, como en la lista
  (`Alt+←/→` es Atrás y Adelante).

### Álbumes, artistas y listas
- **Cabecera con portada**: la portada en grande (la del artista, en círculo; las listas y Me
  gusta, con un mosaico de cuatro), el tipo y el año, el título, el artista como enlace y botones
  para **reproducir**, en **aleatorio**, marcar **Me gusta** todo y «⋯» (añadir a la cola o a una
  lista, descargar lo que falta, ir al artista; en una lista, renombrarla, exportarla o
  eliminarla). Al bajar por la lista se recoge para dejar sitio y vuelve al subir del todo.
- La lista ocupa **todo el ancho** (en un álbum quedaba media pantalla vacía).
- **Al pasar el ratón por una canción**: ▶ en lugar del número para reproducirla, y a la derecha
  ♡ para Me gusta y «⋯» con el resto de acciones (antes solo en el clic derecho).
- **Página de artista**: primero **lo que más escuchas** suyo y debajo su discografía.
- **Arrastrar y soltar**: canciones de cualquier lista o búsqueda sobre una lista del lateral
  para añadirlas, y dentro de una lista para cambiar su orden (en la vista de lista).
- **Importar una playlist de YouTube la guarda también en «Tus listas»**, con su nombre y en su
  orden, y la abre (se puede desactivar en Importar). Si ya la habías importado no se duplica:
  se le añaden las canciones nuevas.

### Inicio y primera vez
- **Descubre en YouTube**: Inicio tiene una estantería con canciones que aún no tienes, justo
  después de «Seguir escuchando». Salen de la «radio» de YouTube Music de tus canciones más
  escuchadas (tres de artistas distintos, que cambian cada día) o, sin historial, de lo popular
  de algunos géneros. Nada de lo que ya tienes ni versiones que no sean la original. Se
  escuchan al momento y se descargan con ↓; lo de la vez anterior se enseña al abrir mientras
  llega lo nuevo. Se desactiva en Ajustes → Apariencia.
- **Bienvenida con la biblioteca vacía**: en vez de una tabla vacía con sus columnas, Inicio
  explica en una frase qué hace Sound y ofrece portadas para escuchar ya («Para empezar a
  escuchar», con «Ver otras» y ↓ para descargar), tres maneras de empezar (buscar, importar un
  enlace o música por frase) y los géneros de YouTube como atajo. Mientras bajan las primeras
  canciones dice cuántas quedan, con un enlace a Descargas, y se va sola cuando llega la
  primera. Con la ventana baja, deja fuera primero la nota de dónde se guardan las canciones,
  luego los géneros y, si hay portadas, las tarjetas.

### Radio
- **Radio en directo**: «Radio» en el lateral abre emisoras de todo el mundo del directorio libre
  Radio Browser. Empieza por las de tu país (según la región de Windows; en Colombia, Caracol,
  Olímpica, Blu, La X…), con listas a un clic (más escuchadas del mundo, noticias, pop, rock,
  salsa, vallenato, reggaetón, clásica, jazz) y un buscador por nombre, ciudad o género.
- Un clic en una emisora la pone a sonar en el mismo reproductor: la barra dice su nombre y
  «En directo · 12:34» en lugar del tiempo, su logo tiñe la ventana, ⏮ ⏭ (y las teclas
  multimedia) pasan a la emisora anterior o siguiente, y pausa corta la conexión (play vuelve al
  directo). Si se corta, se reconecta sola; si no responde, avisa con «Reintentar».
- **Emisoras favoritas** con el corazón de la tarjeta o de la barra; salen primero al abrir la
  radio. El temporizador para dormir también apaga la radio.
- La radio no cuenta en el historial ni toca tu cola de canciones, y no se graba ni se descarga.
- **Estaciones de Sound**: radios sin fin hechas con lo que escuchas. «Para ti», en la página
  Radio, ofrece la radio de tus artistas y canciones más escuchados, la de tus Me gusta y la de
  tus géneros con más canciones (sin biblioteca todavía, los géneros del lateral). También
  «Iniciar radio» en el menú de cualquier canción, y en el «⋯» de un artista, un álbum, una
  lista o Me gusta. Empiezan por esa canción y unas cuantas tuyas parecidas, y siguen con lo
  que YouTube Music pone en su radio (sin internet, con tu biblioteca), aunque la cola
  automática esté desactivada. «A continuación» dice de qué estación viene lo que suena.

### Reproducción
- **Temporizador para dormir**: el botón de la luna, en la barra de abajo, apaga la música al
  cabo de 15, 30 o 45 minutos, 1 hora u hora y media, o **al terminar la canción**. Lleva los
  minutos que quedan y se puede alargar o desactivar desde el mismo menú. A la hora, la música
  baja poco a poco durante unos segundos y se pausa: play sigue donde estaba, y «Seguir
  escuchando» lo deshace si te pilla despierto. «Al terminar la canción» la deja sonar entera,
  sin mezclarla con la siguiente, y play empieza la siguiente.
- Con la ventana muy estrecha, el botón de crossfade se oculta para que el tiempo se vea entero
  (sigue en Ajustes › Transición). Antes, al pasar de maximizada al tamaño mínimo, la parte
  derecha del reproductor podía quedar cortada.

### Lateral y barra de arriba
- **«＋ Añadir música»** reúne lo que antes eran iconos sueltos sin texto: buscar en YouTube,
  importar desde un enlace y música por frase (`Ctrl+G`).
- **Descargas con contador**: el icono enseña cuántas canciones quedan por bajar (gris si están
  en pausa) y su ayuda lo dice con palabras.
- **«Tus listas», «Géneros» y «YouTube» se pliegan** con un clic en su título (▾ / ▸) y se
  recuerda al cerrar. Plegada, una sección deja a la vista solo lo que esté elegido.
- **El tema pasa a Ajustes**, con una opción nueva, **«Como Windows»**: sigue al sistema y cambia
  sola cuando cambia Windows. Quien había elegido claro u oscuro a propósito lo conserva.

### Ajustes
- **Interruptores** para lo que es sí / no (aleatorio, cola automática, letras, normalizar,
  animaciones, iniciar con Windows…) y **todas las opciones a la vista** para lo demás: Tema
  (Como Windows · Claro · Oscuro), Vista (Lista · Cuadrícula), Repetir (No · Lista · Canción) y
  Transición (No · 2 s … 12 s). Antes eran botones que cambiaban de texto al pulsarlos.
- **Índice a la izquierda** que salta a cada sección y marca en cuál estás; con la ventana
  estrecha se oculta para dejar sitio.
- Importar ya no repite «Pega un enlace de YouTube Music» cuatro veces: el título dice qué es,
  debajo qué se puede traer y el campo dónde va el enlace.

### Actualizaciones
- **Aviso de versiones nuevas de Sound**: una vez al día se mira la lista pública de versiones en
  GitHub (no se envía nada tuyo; se desactiva en Ajustes). Si hay una nueva, sale un aviso y el
  engranaje se ilumina; en Ajustes → Actualizaciones se leen las novedades y «Actualizar ahora»
  la descarga, comprueba que coincide con la publicada (SHA-256) y la instala: Sound se cierra
  unos segundos y vuelve a abrirse solo, con la cola donde estaba y respetando «Iniciar con
  Windows». Funciona a partir de esta versión (quien tenga la 1.3.0 la instala a mano una vez).

### Arreglado
- Al borrar la lista que se estaba viendo, seguía apareciendo en el lateral hasta el siguiente
  cambio de vista.

## 1.3.0 — 2026-11-14

### Arreglado
- **Al hacer doble clic sonaba otra canción (o varias seguidas, al azar).** Sin ffmpeg, las
  canciones se guardaban en el MP4 «fragmentado» que entrega YouTube, y Windows las daba por
  terminadas nada más empezar: la cola saltaba de una en una (al azar con el orden aleatorio).
  Ahora se convierten a MP4 normal, sin recodificar ni perder calidad ni etiquetas: al
  descargarlas y, las que ya tenías, una sola vez al abrir la app. Si una canción vuelve a
  terminar antes de lo posible, la cola se detiene con un aviso en vez de encadenar saltos.

### Listas de reproducción
- **Tus listas** en el lateral: «＋ Nueva lista» la crea y su nombre se escribe ahí mismo.
- «Añadir a lista» en el clic derecho de canciones, resultados de búsqueda y álbumes (✓ si ya
  están), o «Nueva lista…» con lo seleccionado. Admiten canciones aún sin descargar.
- Dentro de una lista: suena en su orden (y luego sigue la cola automática), `Supr` quita
  canciones de la lista sin borrar el archivo, `Alt+↑/↓` las sube o baja, y todo se puede
  deshacer. Clic derecho en la lista para reproducirla, renombrarla, exportarla a M3U o
  eliminarla.

### Windows
- **Teclas multimedia** (⏯ ⏭ ⏮ del teclado o de auriculares Bluetooth) y el **panel multimedia
  de Windows** (el recuadro que sale al cambiar el volumen): portada, título, artista, progreso
  y controles de Sound, también desde la pantalla de bloqueo.
- La ventana **recuerda su tamaño, su posición y si estaba maximizada**; si el monitor donde
  estaba ya no existe, vuelve a una pantalla visible.

## 1.2.0 — 2026-11-12

### Reproduciendo, en grande
- **Vista «Reproduciendo»**: la canción que suena en grande, sobre su portada desenfocada, con
  título, artista y álbum (ambos llevan a su página) y tres pestañas: las canciones de su
  **álbum** que tienes, en orden de pista; la **letra** sincronizada, que sigue la canción (un clic
  en una línea salta a ese momento), y lo que sigue en la **cola**.
- Se abre con un clic en la portada del reproductor, con su botón junto al mini reproductor o con
  `Ctrl+Shift+F`. `F11` la pone a **pantalla completa** y `Esc` vuelve paso a paso.
- Si las letras están desactivadas, la pestaña explica qué datos se enviarían a LRCLIB antes de
  ofrecer activarlas.

### Inicio
- Nueva pantalla de entrada con estanterías: **Seguir escuchando**, **Lo que más escuchas**,
  **Añadidas hace poco**, **Tus Me gusta** y **Todavía sin escuchar** (un sorteo que cambia cada
  día). Cada canción sale una sola vez y la cola de lo que pongas es su estantería. Un clic en
  «Sound» vuelve a Inicio.

### Biblioteca
- **Me gusta a la vista**: corazón junto al título de lo que suena y al pasar el ratón por cada
  portada. `Ctrl+L` marca la canción que suena si no hay nada seleccionado.
- En la cuadrícula, las canciones de un mismo **álbum** se juntan en una tarjeta. Al abrirlo se
  ve como lista **en orden de pista** (el orden se pide a YouTube Music la primera vez).
- **Página de artista**: clic en el artista bajo una portada, en el reproductor o desde el menú
  contextual («Ir a…», «Ir al álbum…»). `Esc` vuelve atrás nivel a nivel.
- **Filtro por década** encima de la lista y **selector de orden** visible (recién añadidas,
  título, artista, álbum, año, más escuchadas, duración).
- Los géneros con una sola canción se pliegan en el lateral («N géneros más…»).
- Si hay **canciones repetidas**, un aviso ofrece revisarlas (solo vuelve a avisar si cambian).

### Búsqueda
- Los resultados se separan en **«En tu biblioteca»** y **«En YouTube»**, y cada resultado de
  YouTube tiene su botón **↓ Descargar**.

### Interfaz
- **Avisos con «Deshacer»** (también `Ctrl+Z`) para Me gusta, la cola y eliminar canciones.
  Eliminar ya no pide confirmación: el archivo se borra cuando el aviso desaparece.
- **Mini reproductor** siempre encima (`Ctrl+Shift+M` o el botón junto a «A continuación»).
- Botones **anterior / reproducir / siguiente** en la miniatura de la barra de tareas.
- Cabecera más compacta: el título y el estado comparten línea.
- Los nombres recortados muestran el **texto completo** al pasar el ratón.
- La onda tiene un **cabezal** (línea y punto) que crece al pasar el ratón: ya no se confunde dónde
  va la canción.
- Aleatorio, repetición, cola automática y crossfade llevan un **punto** cuando están activos, y
  su ayuda dice si lo están.
- Con la ventana estrecha, el volumen se acorta para que el tiempo de la canción no se corte.

### Reproducción
- Al terminar un álbum, la **cola automática** sigue con canciones similares (si está activa).

## 1.1.0 — 2026-11-06

### Búsqueda
- La búsqueda en YouTube Music es **mucho más rápida**: empieza a buscar a las tres letras,
  muestra **pre-resultados** al instante mientras escribes y los sustituye por los definitivos
  cuando llegan. Las primeras 20 canciones aparecen con una sola petición y el resto se completa
  por detrás, sin hacer esperar.
- Las búsquedas recientes se guardan en memoria: repetir una consulta (o escribir una que empiece
  igual) responde al momento, y la conexión con YouTube se deja preparada al abrir la ventana.

### Reproducción
- La **onda** y el **ecualizador** siguen el volumen real de la canción: crecen en los pasajes
  fuertes y se calman en los suaves.
- El **color de la ventana** se adelanta a la portada de la siguiente canción antes del crossfade.
- La **transición** se adapta a la energía: más larga entre canciones tranquilas y más corta
  cuando el salto de intensidad es grande.
- La **cola automática** evita saltos bruscos de intensidad y muestra bajo cada canción por qué la
  eligió («similar a…», «mismo género», «otro género»).
- **Letras sincronizadas** opcionales en la pestaña «Letra» de «A continuación». Se activan en
  Ajustes → Reproducción, se piden a LRCLIB y se guardan en caché para escucharlas sin conexión.

### Interfaz
- **Vista de cuadrícula** con la portada de cada canción, además de la lista. Se cambia con el
  icono de la esquina superior derecha de la lista, `Ctrl+Shift+L` o Ajustes → Apariencia, y se
  recuerda al cerrar.
- El menú **Más acciones de la biblioteca** se traslada junto al selector de vista, a la derecha,
  con un nuevo icono de caja de herramientas.
- La ventana y los diálogos usan el **icono de Sound** (la onda), no el de Python/Tk. El `.ico`
  viaja empaquetado con la aplicación y se aplica al abrirla.
- Un clic en **«Sound»** (arriba a la izquierda) vuelve a la pantalla principal: cierra páginas y
  «A continuación», limpia la búsqueda y selecciona «Todo» en el lateral.
- Las portadas de la cuadrícula se cargan solo para las tarjetas visibles, se guardan en caché y,
  si una canción no tiene carátula, se usa su degradado propio. La selección, el clic derecho, el
  teclado y el ecualizador de la canción que suena funcionan igual que en la lista.
- La **rueda del ratón** y el **gesto de dos dedos del touchpad** desplazan la vista que hay bajo
  el puntero: lista, cuadrícula, géneros, «A continuación», Ajustes y también las propias barras
  de desplazamiento. En Tk 9 el touchpad usa su propio evento, así que se atiende aparte de la
  rueda clásica.

## 1.0.2 — 2026-11-06

### Interfaz
- Página **Ajustes** (icono de engranaje o `Ctrl+,`) que reúne tema, animaciones,
  orden, repetición, cola automática, transición, normalización, cookies, mantenimiento
  de la biblioteca y la lista de atajos.
- Menú **Más acciones** en la barra superior: duplicados, exportar M3U, archivos que
  faltan, normalización y diagnóstico, sin depender del clic derecho.
- Preferencia para **reducir animaciones** (además de respetar la opción de Windows).
- Atajos nuevos: `Ctrl+,`/`F1` (ajustes), `Ctrl+M` (silenciar) y `Ctrl+↑/↓` (volumen).
  `Esc` también cierra el panel «A continuación».
- La cola «A continuación» muestra cuántas canciones quedan y su duración total.
- Las cookies del navegador elegido se aplican también al streaming; volver a elegirlo las relee
  sin reiniciar la ventana.
- **Iniciar con Windows** en Ajustes → Sistema (clave `Run` del usuario, sin administrador). El
  instalador lo deja activado y la desinstalación lo borra.
- Descargas muestra el **porcentaje en curso** de cada canción en su propia columna.
- Importar usa el mismo campo dibujado que el resto de la aplicación; Diagnóstico
  distingue los estados con ✓, ! y ✗ y permite abrir la carpeta de música.
- Textos secundarios con contraste WCAG AA en ambos temas.

### Descargas
- Protección preventiva de espacio: por debajo de 2 GB las descargas van de una en una y por
  debajo de 500 MB se pausan, con aviso en la ventana y en la CLI. Se reanudan solas al liberar
  espacio y se tiene en cuenta la duración para estimar cuánto falta.

### Estabilidad y datos
- Un archivo local que no supera la comprobación de duración ya no se borra: se aparta con la
  marca `.damaged-…` y se puede volver a descargar desde el menú contextual.
- El bucle de eventos y el tick de reproducción se reprograman aunque un manejador falle.
- Los trabajadores de descarga se recuperan de errores de disco o SQLite y un supervisor repone
  los que mueran. Al cerrar se espera a los hilos antes de soltar el bloqueo entre procesos.
- La comprobación de duración usa una sola tolerancia compartida (15 s / 20 %).
- Volver a descargar un archivo incompleto conserva su género y el reintento vuelve a avisar si falla.
- Calentar yt-dlp al arrancar ya no resuelve un vídeo de muestra: no hace red ni falla cuando ese
  vídeo cambia de formatos.
- yt-dlp se actualiza solo cuando hay una versión nueva (como mucho una comprobación al día): la
  app empaquetada la descarga a `vendor/` y la usa al reiniciar; en desarrollo la instala con pip.
  Si varias descargas seguidas fallan como si yt-dlp estuviera viejo, se intenta en ese momento.
  Se puede desactivar en **Ajustes → Descargas**.

### Rendimiento y mantenimiento
- Una descarga terminada actualiza solo su fila; ya no se relee toda la biblioteca ni se analizan
  todos los archivos al arrancar.
- Búsqueda con caché de texto normalizado y filtro en SQLite; índices por artista, título y género.
- El género y los metadatos se resuelven en un pool compartido mientras baja el audio.
- Caché de categorías de géneros para buscar sin conexión; caché de streaming atómica y podada.
- Historial con la fecha real de la última reproducción y comando `sound historial --limpiar`.
- Actualizador: extracción segura del wheel, ruta de `vendor` validada y versión anterior conservada.
- Tests nuevos para cuarentena, búsqueda, historial, caché offline y ciclo de vida del servicio.

## 1.0.1 — 2026-11-02

### Reproducción
- Si un archivo local dura mucho menos de lo anunciado (descarga incompleta o truncada), la canción
  se detiene con un aviso y queda marcada en la lista, en vez de saltar en cadena a la siguiente.
  Queda registrado en `sound.log`.

## 1.0.0 — 2026-11-02

Primera versión pensada para entregar a usuarios.

### Instalación y datos
- Instalador de Windows por usuario (`Sound-1.0.0-setup.exe`) y carpeta portable generada con
  PyInstaller. Incluye icono y accesos directos.
- Los datos ya no viven dentro de la carpeta de instalación: base de datos, preferencias, caché y
  registro van a `%LOCALAPPDATA%\Sound`; la música, a `%USERPROFILE%\Music\Sound`.
- Migración automática e idempotente desde la disposición 0.x (junto al proyecto), con informe en
  el registro. Modo portable con `SOUND_PORTABLE=1` o un archivo `portable.txt`.
- Copia de seguridad automática de `library.db` antes de migrar el esquema y escrituras atómicas
  de preferencias, sesión y cachés.

### Descargas y YouTube
- Detección del aviso «Sign in to confirm you're not a bot»: no se reintenta en vano y se explica
  cómo resolverlo.
- Selector de cookies de navegador en la página de Descargas (Chrome, Edge, Firefox, Brave,
  Opera, Vivaldi, Chromium), sin reiniciar.
- Mensajes de error en español y accionables en vez del volcado de yt-dlp; el original queda en
  `sound.log`.
- Actualización de yt-dlp sin pip en la versión empaquetada: descarga el wheel oficial de PyPI,
  verifica su SHA-256 y lo carga al reiniciar.

### Estabilidad y soporte
- Página de Diagnóstico con Node.js, ffmpeg, internet, cookies, espacio y permisos.
- Captura de errores de Tk, del hilo principal y de los hilos de descarga, con diálogo y acceso
  al registro.
- Instancia única: no se abren dos ventanas que se pisen la sesión.
- Registro con cabecera de versión, sistema y dependencias al arrancar.
- Los tests usan su propia carpeta temporal y ya no escriben en `sound.log` ni tocan la biblioteca.
- Comando `sound recuperar` para reencolar las reproducciones de un informe de recuperación.

### Calidad
- Versión 1.0.0, licencia MIT, aviso legal y política de privacidad.
- CI en Python 3.11–3.14 con ruff, tests y compilación del ejecutable.
- Workflow de release que genera y publica el instalador al etiquetar `v*`.
- Empaquetado reproducible: `packaging/build.ps1`, `sound.spec`, `installer.iss`.

### Funciones de la aplicación
- Biblioteca local por género, búsqueda en YouTube Music, importación de álbumes/playlists/
  artistas, «Música por frase», cola persistente, historial, «Me gusta», duplicados, edición de
  etiquetas, exportación M3U, normalización de volumen y crossfade.
