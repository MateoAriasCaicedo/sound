# Privacidad

Sound funciona en el equipo del usuario. No tiene cuentas, telemetría ni servidores propios.

## Qué se guarda en el equipo

- Biblioteca, cola, historial, «Me gusta», listas y emisoras favoritas:
  `%LOCALAPPDATA%\Sound\library.db`.
- Preferencias y sesión de reproducción: `ui_prefs.json` y `ui_session.json` en la misma carpeta.
- Canciones y carátulas: `%USERPROFILE%\Music\Sound`.
- Formas de onda, volumen medido, letras consultadas y registro de errores:
  `%LOCALAPPDATA%\Sound\cache` y `%LOCALAPPDATA%\Sound\sound.log`. Las letras solo se guardan
  mientras esa opción esté activada.
- Sugerencias de «Descubre en YouTube» de la última vez: `%LOCALAPPDATA%\Sound\discover.json`.

El registro puede contener rutas de archivos, títulos de canciones, identificadores de vídeo y
mensajes de error técnicos. No contiene cookies ni contraseñas.

## Qué sale del equipo

- **YouTube Music / yt-dlp**: búsquedas, metadatos y descargas. Es el funcionamiento normal de la
  aplicación y está sujeto a la política de privacidad de Google.
  - La **cola automática**, las **estaciones de Sound** y **«Descubre en Inicio»** piden a
    YouTube Music canciones parecidas a una dada: se envía el identificador de esa canción (la
    que suena, la que empieza la estación o, en Descubre, hasta tres de las que más escuchas).
    La cola automática y Descubre se desactivan en Ajustes; una estación solo empieza si la
    eliges.
- **GitHub**: si el aviso de versiones nuevas está activado, una vez al día se consulta la lista
  pública de versiones de Sound; al actualizar, el instalador se descarga de GitHub. No se envía
  nada del usuario.
- **Radio Browser** (radio-browser.info): al abrir «Radio» se piden las emisoras de tu país
  (el código de la región de Windows, p. ej. «CO»), la lista o el género elegidos y lo que
  escribas en su buscador. Al escuchar una emisora, el equipo se conecta al servidor de esa
  emisora, y sus logos se descargan de las direcciones que publica cada una, como en cualquier
  reproductor de radio.
- **iTunes Search API**: solo cuando una canción llega sin género; se envía el artista y el
  título para intentar clasificarla.
- **LRCLIB**: mientras las letras estén activadas (lo están de serie; se desactivan en Ajustes ›
  Reproducción). Se envían el título, el artista, el álbum y la duración de la canción que suena
  para pedir su letra; si esa versión solo tiene el texto, también se busca por el título sin la
  coletilla de versión («(Main Version)», «- Remastered»…). La letra se guarda en caché local.
- **PyPI**: al usar «Actualizar yt-dlp», se consulta la versión publicada y se descarga el wheel
  oficial.

No se envía la biblioteca, el historial ni las cookies a ningún servicio de Sound.

## Cookies de navegador

Si el usuario elige un navegador en «Cookies de YouTube», Sound pide a yt-dlp que lea la cookie
de sesión de ese perfil local. La cookie se usa solo para las peticiones a YouTube y no se copia,
registra ni transmite a terceros. Para dejar de usarla, selecciona la opción vacía.

## Borrar los datos

Cierra Sound y elimina `%LOCALAPPDATA%\Sound` y la carpeta de música que hayas elegido. La
desinstalación del programa no borra esos datos a propósito, para no perder la biblioteca.
