# Privacidad

Sound funciona en el equipo del usuario. No tiene cuentas, telemetría ni servidores propios.

## Qué se guarda en el equipo

- Biblioteca, cola, historial y «Me gusta»: `%LOCALAPPDATA%\Sound\library.db`.
- Preferencias y sesión de reproducción: `ui_prefs.json` y `ui_session.json` en la misma carpeta.
- Canciones y carátulas: `%USERPROFILE%\Music\Sound`.
- Formas de onda, volumen medido, letras consultadas y registro de errores:
  `%LOCALAPPDATA%\Sound\cache` y `%LOCALAPPDATA%\Sound\sound.log`. Las letras solo se guardan si
  el usuario activa esa opción.

El registro puede contener rutas de archivos, títulos de canciones, identificadores de vídeo y
mensajes de error técnicos. No contiene cookies ni contraseñas.

## Qué sale del equipo

- **YouTube Music / yt-dlp**: búsquedas, metadatos y descargas. Es el funcionamiento normal de la
  aplicación y está sujeto a la política de privacidad de Google.
- **iTunes Search API**: solo cuando una canción llega sin género; se envía el artista y el
  título para intentar clasificarla.
- **LRCLIB**: solo si el usuario activa las letras. Se envían el título, el artista, el álbum y la
  duración de la canción que suena para pedir su letra. La letra se guarda en caché local.
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
