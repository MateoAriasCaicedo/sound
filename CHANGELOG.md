# Cambios

## Sin publicar

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
