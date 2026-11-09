# STEP Drawing

A Windows desktop app that turns a STEP (`.step` / `.stp`) model into a dimensioned 2D engineering drawing.

- **Gerber import:** open a PCB board-outline Gerber (KiCad Edge.Cuts `.gbr`, Altium `.gko`/`.gm1`, Eagle `.gml`)
  and get a drawing of the board: outline, corner radii, cutouts and mounting holes, at 1.6 mm thick
  (`--board-thickness` on the command line). Copper and silkscreen layers are not used.
- **Views:** front, top, side (third-angle or first-angle projection) and isometric, with hidden lines.
- **Automatic dimensions:** overall size, step and shoulder positions, hole diameters with counts (`4× Ø8`), hole positions from a datum edge, fillet radii, centre marks.
- **Sheet:** A4–A1 or ANSI A–D with automatic standard scale and an editable title block.
- **Edit dimensions:** click any dimension to add a tolerance (± symmetric, +/− deviation, or max/min limits),
  turn a leader, or delete it. Deleted dimensions can be restored, Ctrl+Z undoes, and edits are remembered per file.
- **Notes:** numbered drawing notes, a general tolerance ("unless otherwise specified"), and a one-click
  Confidential / Proprietary notice that names the company from the title block.
- **Export:** PDF, DXF (layers and linetypes for CAD), SVG and PNG.

## Install

Download `STEP-Drawing-<version>-Setup.exe` from the [latest release](https://github.com/ra8506/step-drawing/releases/latest) and run it.
It installs for your user only (no admin rights needed) and adds a Start-menu shortcut, plus "Open with" for STEP files.

Windows SmartScreen may warn the first time because the installer isn't code-signed: click **More info → Run anyway**.

## Updates

The app checks GitHub for a newer release on startup and offers **Update now**, which installs it silently and restarts.
That automatic check only works while the repository is public. While it's private, use **Help → Check for updates**:
it opens the releases page (sign in to GitHub), and you run the newest `Setup.exe` over the top. Settings are kept.

## Use

1. **Open STEP…** (or drag a file onto the window).
2. Adjust sheet size, projection, units, views and which dimensions to show on the left. The preview updates live
   (scroll to zoom, drag to pan, double-click to fit).
3. **Export PDF / DXF / SVG.**

If a model comes in lying on its side (some CAD tools export Y-up), tick **Model is Y-up**.

Command line (same engine): `"STEP Drawing.exe" --cli part.step -o part.pdf -o part.dxf --sheet A3 --projection third`

## Releasing a new version (maintainers)

1. Bump `__version__` in `stepdraw/__init__.py`.
2. Commit and push to `main`.
3. GitHub Actions builds and tests the installer, and because no release exists for that version yet, publishes
   release `v<version>` with the Setup.exe. Installed copies offer the update on next start.

The installer bundles its own Python runtime (embedded CPython 3.13) with Open CASCADE (`cadquery-ocp`), Qt (`PySide6`) and `ezdxf`;
see `packaging/build_windows.ps1`. To build locally on Windows you need Python 3.13 and Inno Setup 6, then run that script.

## Development

```
pip install -r requirements.txt pytest
python -m stepdraw            # GUI
pytest -q                     # tests (uses tests/samples/*.step)
```

## Licences

Geometry kernel: Open CASCADE Technology (LGPL 2.1 with exception). GUI: Qt for Python / PySide6 (LGPL 3). DXF: ezdxf (MIT).
