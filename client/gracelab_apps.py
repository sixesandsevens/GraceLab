#!/usr/bin/env python3
"""
GraceLab app launcher — curated app menu for guestlab sessions.
Opened from the panel button. Category tabs: Games, Office, Tools, Media, Access.
Closes after launching an app.
"""
import subprocess
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

COLS = 4
ICON_PX = 48

CATEGORIES = [
    ("Games", [
        ("Solitaire",    "gnome-aisleriot",               "sol.desktop"),
        ("Mahjongg",     "org.gnome.Mahjongg",            "org.gnome.Mahjongg.desktop"),
        ("Mines",        "org.gnome.Mines",               "org.gnome.Mines.desktop"),
        ("Quadrapassel", "org.gnome.Quadrapassel",        "org.gnome.Quadrapassel.desktop"),
        ("Sudoku",       "org.gnome.Sudoku",              "org.gnome.Sudoku.desktop"),
    ]),
    ("Office", [
        ("Writer",       "libreoffice-writer",            "libreoffice-writer.desktop"),
        ("Spreadsheet",  "libreoffice-calc",              "libreoffice-calc.desktop"),
        ("Draw",         "libreoffice-draw",              "libreoffice-draw.desktop"),
        ("Presentation", "libreoffice-impress",           "libreoffice-impress.desktop"),
    ]),
    ("Tools", [
        ("Firefox",      "firefox",                       "firefox.desktop"),
        ("Drawing",      "com.github.maoschanz.drawing",  "com.github.maoschanz.drawing.desktop"),
        ("Calculator",   "org.gnome.Calculator",          "org.gnome.Calculator.desktop"),
        ("Calendar",     "org.gnome.Calendar",            "org.gnome.Calendar.desktop"),
        ("Dictionary",   "org.xfce.dictionary",           "xfce4-dict.desktop"),
        ("Text Editor",  "accessories-text-editor",       "org.x.editor.desktop"),
        ("Printers",     "printer",                       "system-config-printer.desktop"),
    ]),
    ("Media", [
        ("Video Player", "io.github.celluloid_player.Celluloid", "io.github.celluloid_player.Celluloid.desktop"),
        ("Music",        "org.gnome.Rhythmbox3",          "org.gnome.Rhythmbox3.desktop"),
    ]),
    ("Access", [
        ("On-Screen\nKeyboard", "onboard",                        "onboard.desktop"),
        ("Magnifier",           "magnus",                         "magnus.desktop"),
        ("Screen Reader",       "orca",                           "orca-launcher.desktop"),
        ("Accessibility\nSettings", "org.xfce.settings.accessibility", "xfce4-accessibility-settings.desktop"),
    ]),
]

CSS = b"""
window, notebook, stack { background-color: #2d013d; }
notebook > header { background-color: #1e0028; }
notebook > header > tabs > tab {
    background-color: #1e0028;
    background-image: none;
    color: #baabbf;
    padding: 10px 18px;
    border: none;
}
notebook > header > tabs > tab:checked {
    background-color: #2d013d;
    background-image: none;
    color: #b5d434;
    border-bottom: 3px solid #087e77;
}
button.app-btn {
    background-color: #2d013d;
    border: none;
    border-radius: 8px;
    color: #f9fafb;
    padding: 12px 8px;
    min-width: 108px;
}
button.app-btn:hover  { background-color: #46005f; }
button.app-btn:active { background-color: #5a007a; }
label.app-label {
    color: #f9fafb;
    font-size: 11px;
}
"""


def _launch(desktop_id, win):
    try:
        subprocess.Popen(["gtk-launch", desktop_id])
    except Exception:
        pass
    win.destroy()
    Gtk.main_quit()


def _make_button(label_text, icon_name, desktop_id, win):
    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

    img = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.INVALID)
    img.set_pixel_size(ICON_PX)
    vbox.pack_start(img, False, False, 0)

    lbl = Gtk.Label(label=label_text)
    lbl.set_line_wrap(True)
    lbl.set_max_width_chars(12)
    lbl.set_justify(Gtk.Justification.CENTER)
    lbl.get_style_context().add_class("app-label")
    vbox.pack_start(lbl, False, False, 0)

    btn = Gtk.Button()
    btn.get_style_context().add_class("app-btn")
    btn.set_relief(Gtk.ReliefStyle.NONE)
    btn.add(vbox)
    btn.connect("clicked", lambda _b: _launch(desktop_id, win))
    return btn


def _build_grid(apps, win):
    grid = Gtk.Grid()
    grid.set_row_spacing(8)
    grid.set_column_spacing(8)
    grid.set_margin_top(20)
    grid.set_margin_bottom(20)
    grid.set_margin_start(20)
    grid.set_margin_end(20)
    for i, (label_text, icon_name, desktop_id) in enumerate(apps):
        row, col = divmod(i, COLS)
        grid.attach(_make_button(label_text, icon_name, desktop_id, win), col, row, 1, 1)
    return grid


def main():
    win = Gtk.Window(title="Apps")
    win.set_default_size(560, 340)
    win.set_resizable(False)
    win.set_position(Gtk.WindowPosition.CENTER)
    win.set_skip_taskbar_hint(True)
    win.set_skip_pager_hint(True)
    win.set_keep_above(True)
    win.connect("destroy", Gtk.main_quit)

    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        win.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    notebook = Gtk.Notebook()
    notebook.set_tab_pos(Gtk.PositionType.TOP)
    for cat_name, apps in CATEGORIES:
        grid = _build_grid(apps, win)
        notebook.append_page(grid, Gtk.Label(label=cat_name))

    win.add(notebook)
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
