"""Botones de reproducción en la miniatura de la barra de tareas de Windows.

Al pasar el ratón por el icono de Sound en la barra de tareas aparecen «anterior», «reproducir o
pausar» y «siguiente» bajo la miniatura (ITaskbarList3::ThumbBarAddButtons). Se habla con COM
mediante ctypes para no añadir dependencias; si algo falla (otro sistema, una versión rara de
Windows), se registra y la ventana sigue igual, sin botones.

Los clics llegan como WM_COMMAND a la ventana: se intercepta con SetWindowSubclass y se pasan a
la aplicación por su cola de eventos, nunca llamando a Tk desde el procedimiento de ventana.
"""
import ctypes
import sys
import tempfile
import uuid
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageDraw

from ..core import log as _log
from .icons import icon_mask

log = _log.get("ui.taskbar")

PREV, PLAY_PAUSE, NEXT = 0, 1, 2
_WM_COMMAND = 0x0111
_WM_NCDESTROY = 0x0082
_THBN_CLICKED = 0x1800
_THB_ICON, _THB_TOOLTIP, _THB_FLAGS = 0x2, 0x4, 0x8
_THBF_ENABLED = 0
_CLSCTX_INPROC_SERVER = 0x1
_IMAGE_ICON, _LR_LOADFROMFILE = 1, 0x10
_LRESULT = ctypes.c_ssize_t


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text: str) -> "_GUID":
        raw = uuid.UUID(text).bytes_le
        guid = cls()
        ctypes.memmove(ctypes.byref(guid), raw, ctypes.sizeof(guid))
        return guid


class _THUMBBUTTON(ctypes.Structure):
    _fields_ = [("dwMask", wintypes.DWORD), ("iId", wintypes.UINT), ("iBitmap", wintypes.UINT),
                ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 260),
                ("dwFlags", wintypes.DWORD)]


_CLSID_TASKBARLIST = "{56FDF344-FD6D-11d0-958A-006097C9A090}"
_IID_ITASKBARLIST3 = "{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}"
_SUBCLASSPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                   wintypes.LPARAM, ctypes.c_size_t, ctypes.c_size_t)


def _glyph(name: str, size: int) -> Image.Image:
    """Máscara del icono: los de la ventana, más «pausa», que allí se dibuja aparte."""
    if name != "pause":
        return icon_mask(name, size, stroke=2.0)
    scale = 6
    img = Image.new("L", (24 * scale, 24 * scale), 0)
    draw = ImageDraw.Draw(img)
    for x in (6.5, 14):
        draw.rounded_rectangle((x * scale, 5 * scale, (x + 3.5) * scale, 19 * scale),
                               radius=scale, fill=255)
    return img.resize((size, size), Image.LANCZOS)


def _icon_file(name: str, folder: Path) -> Path:
    """Icono blanco con transparencia, en varios tamaños, como archivo .ico."""
    path = folder / f"sound-thumb-{name}.ico"
    if not path.exists():
        big = Image.new("RGBA", (64, 64), (255, 255, 255, 0))
        big.putalpha(_glyph(name, 64))
        big.save(path, sizes=[(16, 16), (20, 20), (24, 24), (32, 32)])
    return path


class TaskbarButtons:
    """Anterior / reproducir-pausar / siguiente bajo la miniatura de una ventana."""

    def __init__(self, hwnd: int, on_click) -> None:
        self.hwnd = hwnd
        self.on_click = on_click
        self.ok = False
        self.added = False
        self.playing = False
        self._ptr = None
        self._icons: dict[str, int] = {}
        self._proc = None
        self._created_msg = 0
        if sys.platform != "win32":
            return
        try:
            self._setup()
            self.ok = True
        except Exception as exc:  # noqa: BLE001 - sin botones, pero la ventana sigue igual
            _log.failure(log, "no se pudieron crear los botones de la barra de tareas", exc)

    # ---------- preparación ----------
    def _setup(self) -> None:
        ole32, user32, comctl32 = ctypes.windll.ole32, ctypes.windll.user32, ctypes.windll.comctl32
        ole32.CoInitialize(None)  # ya inicializado (S_FALSE) o en otro modo: sirve igual
        ptr = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(ctypes.byref(_GUID.parse(_CLSID_TASKBARLIST)), None,
                                    _CLSCTX_INPROC_SERVER,
                                    ctypes.byref(_GUID.parse(_IID_ITASKBARLIST3)),
                                    ctypes.byref(ptr))
        if hr != 0 or not ptr.value:
            raise OSError(f"CoCreateInstance(TaskbarList) devolvió {hr:#x}")
        self._ptr = ptr
        vtable = ctypes.cast(ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0],
                             ctypes.POINTER(ctypes.c_void_p))

        def method(index: int, *argtypes):
            return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[index])

        self._release = method(2)
        self._hr_init = method(3)
        self._add = method(15, wintypes.HWND, wintypes.UINT, ctypes.POINTER(_THUMBBUTTON))
        self._update = method(16, wintypes.HWND, wintypes.UINT, ctypes.POINTER(_THUMBBUTTON))
        hr = self._hr_init(self._ptr)
        if hr != 0:
            raise OSError(f"HrInit devolvió {hr:#x}")

        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                      ctypes.c_int, ctypes.c_int, wintypes.UINT]
        size = user32.GetSystemMetrics(49) or 16  # SM_CXSMICON: sigue el escalado de pantalla
        folder = Path(tempfile.gettempdir())
        for name in ("prev", "next", "play", "pause"):
            path = self._play_icon(folder) if name == "play" else _icon_file(name, folder)
            handle = user32.LoadImageW(None, str(path), _IMAGE_ICON, size, size, _LR_LOADFROMFILE)
            if not handle:
                raise OSError(f"no se pudo cargar el icono {name}")
            self._icons[name] = handle

        # los clics llegan como WM_COMMAND a la propia ventana
        self._created_msg = user32.RegisterWindowMessageW("TaskbarButtonCreated")
        comctl32.DefSubclassProc.restype = _LRESULT
        comctl32.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                             wintypes.LPARAM]
        comctl32.SetWindowSubclass.argtypes = [wintypes.HWND, _SUBCLASSPROC, ctypes.c_size_t,
                                               ctypes.c_size_t]
        comctl32.RemoveWindowSubclass.argtypes = [wintypes.HWND, _SUBCLASSPROC, ctypes.c_size_t]
        self._proc = _SUBCLASSPROC(self._wndproc)
        if not comctl32.SetWindowSubclass(self.hwnd, self._proc, 1, 0):
            raise OSError("SetWindowSubclass falló")

    @staticmethod
    def _play_icon(folder: Path) -> Path:
        """El triángulo de reproducir, relleno (el de la ventana va dentro de un disco)."""
        path = folder / "sound-thumb-play.ico"
        if not path.exists():
            scale = 6
            mask = Image.new("L", (24 * scale, 24 * scale), 0)
            ImageDraw.Draw(mask).polygon([(7 * scale, 5 * scale), (7 * scale, 19 * scale),
                                          (19 * scale, 12 * scale)], fill=255)
            img = Image.new("RGBA", mask.size, (255, 255, 255, 0))
            img.putalpha(mask)
            img.resize((64, 64), Image.LANCZOS).save(
                path, sizes=[(16, 16), (20, 20), (24, 24), (32, 32)])
        return path

    def _buttons(self):
        specs = ((PREV, "prev", "Anterior"),
                 (PLAY_PAUSE, "pause" if self.playing else "play",
                  "Pausar" if self.playing else "Reproducir"),
                 (NEXT, "next", "Siguiente"))
        array = (_THUMBBUTTON * 3)()
        for slot, (button_id, icon, tip) in zip(array, specs):
            slot.dwMask = _THB_ICON | _THB_TOOLTIP | _THB_FLAGS
            slot.iId = button_id
            slot.hIcon = self._icons[icon]
            slot.szTip = tip
            slot.dwFlags = _THBF_ENABLED
        return array

    # ---------- uso ----------
    def install(self) -> None:
        """Añade los botones; se llama cuando el icono de la barra de tareas ya existe."""
        if not self.ok:
            return
        try:
            array = self._buttons()
            hr = self._add(self._ptr, self.hwnd, 3, array)
            self.added = hr == 0
            if not self.added:
                log.info("ThumbBarAddButtons devolvió %#x; se reintenta al crearse el botón", hr)
        except Exception as exc:  # noqa: BLE001
            _log.failure(log, "no se pudieron añadir los botones de la miniatura", exc)

    def set_playing(self, playing: bool) -> None:
        """Cambia el botón central entre reproducir y pausar."""
        if playing == self.playing:
            return
        self.playing = playing
        if not (self.ok and self.added):
            return
        try:
            self._update(self._ptr, self.hwnd, 3, self._buttons())
        except Exception as exc:  # noqa: BLE001
            _log.failure(log, "no se pudo actualizar el botón de reproducir", exc)

    def _wndproc(self, hwnd, msg, wparam, lparam, _uid, _ref):
        try:
            if msg == _WM_COMMAND and (wparam >> 16) & 0xFFFF == _THBN_CLICKED:
                self.on_click(wparam & 0xFFFF)
                return 0
            if msg == self._created_msg and self._created_msg:
                self.added = False
                self.on_click(-1)  # el icono de la barra de tareas (re)aparece: hay que añadirlos
            elif msg == _WM_NCDESTROY:
                ctypes.windll.comctl32.RemoveWindowSubclass(hwnd, self._proc, 1)
        except Exception:  # noqa: BLE001 - un error aquí no debe tumbar la ventana
            pass
        return ctypes.windll.comctl32.DefSubclassProc(hwnd, msg, wparam, lparam)
