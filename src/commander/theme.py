"""Light / dark / system appearance."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

_original: dict = {}


def dark_palette() -> QPalette:
    p = QPalette()
    base, window, text = QColor(30, 30, 30), QColor(45, 45, 45), QColor(225, 225, 225)
    for role, color in (
        (QPalette.Window, window), (QPalette.WindowText, text), (QPalette.Base, base),
        (QPalette.AlternateBase, window), (QPalette.ToolTipBase, window), (QPalette.ToolTipText, text),
        (QPalette.Text, text), (QPalette.Button, window), (QPalette.ButtonText, text),
        (QPalette.BrightText, QColor(255, 90, 90)), (QPalette.Link, QColor(90, 160, 255)),
        (QPalette.Highlight, QColor(38, 99, 235)), (QPalette.HighlightedText, QColor(255, 255, 255)),
        (QPalette.PlaceholderText, QColor(140, 140, 140)),
    ):
        p.setColor(role, color)
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, QColor(120, 120, 120))
    return p


def is_dark(app: QApplication) -> bool:
    return app.palette().color(QPalette.Window).lightness() < 128


def customized() -> bool:
    """True once a non-system theme has been applied in this process."""
    return bool(_original)


def apply(app: QApplication, theme: str) -> str:
    """Switch the whole application to "system", "light" or "dark".

    Returns how it was done: "system", "native" (the platform style follows the requested
    colour scheme, possibly a moment later) or "palette" (Fusion style with our own palette).
    """
    if not _original:
        _original["style"] = app.style().objectName()
        _original["palette"] = QPalette(app.palette())
    hints = app.styleHints()
    wanted = {"dark": Qt.ColorScheme.Dark, "light": Qt.ColorScheme.Light}.get(theme, Qt.ColorScheme.Unknown)
    native = False
    if hasattr(hints, "setColorScheme"):  # Qt 6.8+: let the platform style do it when it can
        hints.setColorScheme(wanted)
        native = theme != "system" and hints.colorScheme() == wanted
    if theme == "system" or native:
        style = QStyleFactory.create(_original["style"])
        if style is not None and app.style().objectName() != _original["style"]:
            app.setStyle(style)
        if theme == "system":
            app.setPalette(_original["palette"])
        return "native" if native else "system"
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setPalette(dark_palette() if theme == "dark" else app.style().standardPalette())
    return "palette"
