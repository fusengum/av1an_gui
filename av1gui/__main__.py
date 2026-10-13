import sys


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in ("check", "encode"):
        from .cli import main as cli_main
        return cli_main(sys.argv[1:])

    from pathlib import Path

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from shared.gui import theme

    from . import APP_NAME
    from .gui.main_window import AvMainWindow

    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("av1an_gui")
        except Exception:  # noqa: BLE001
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    icon = Path(__file__).parent / "assets" / ("icon.ico" if sys.platform.startswith("win") else "icon.png")
    app.setWindowIcon(QIcon(str(icon)))
    theme.apply(app)
    w = AvMainWindow()
    w.show()
    files = [a for a in sys.argv[1:] if not a.startswith("-")]
    if files:
        w.add_paths([Path(f) for f in files])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
