"""`python -m stepdraw` starts the app; `python -m stepdraw --cli ...` runs the command line."""
import sys


def run():
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        from .cli import main

        sys.exit(main(sys.argv[2:]))
    if sys.platform == "win32":  # own taskbar icon/group instead of pythonw's
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("STEPDrawing.App")
        except Exception:
            pass
    from .app import main

    main()


if __name__ == "__main__":
    run()
