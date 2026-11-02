"""Punto de entrada del ejecutable de Windows (PyInstaller).

`Sound.exe --self-test` comprueba que el ejecutable arranca, crea sus carpetas y escribe el
registro sin abrir la ventana: es lo que usa GitHub Actions para validar la build.
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
        log.get("selftest").info("autotest correcto: Sound %s (%s)",
                                 __version__, _sys.executable)
        return 0

    from app.ui.main import run

    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
