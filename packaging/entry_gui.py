"""Punto de entrada del ejecutable de Sound (PyInstaller), en Windows y Linux.

`Sound --self-test` comprueba que el ejecutable arranca, crea sus carpetas, escribe el registro
y (en Linux) encuentra GStreamer, sin abrir la ventana: es lo que usa GitHub Actions para validar
la build.
"""
import sys


def main() -> int:
    if "--version" in sys.argv:
        from app import __version__

        print(__version__)
        return 0

    if "--self-test" in sys.argv:
        import sys as _sys

        from app import __version__
        from app.core import config, log

        config.prepare()
        log.setup()
        log.session_header()
        if sys.platform != "win32":
            from app.ui.player import backend_available

            if not backend_available():
                raise RuntimeError("el autotest no encontró GStreamer en el ejecutable")
        log.get("selftest").info("autotest correcto: Sound %s (%s)",
                                 __version__, _sys.executable)
        return 0

    from app.ui.main import run

    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
