# Cambios

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
