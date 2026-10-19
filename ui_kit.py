#!/usr/bin/env python3
"""
ui_kit.py - kit d'interface partagé (customtkinter) : police Roboto Mono embarquée, icônes dessinées,
infobulles, étapes guidées, zone de dépôt, notifications, boîtes de dialogue, thème clair / sombre.

Installation : pip install customtkinter pillow      (optionnel : tkinterdnd2 pour le glisser-déposer)
"""

import json
import os
import subprocess
import sys
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont

try:  # glisser-déposer de fichiers (facultatif)
    from tkinterdnd2 import TkinterDnD, DND_FILES

    HAS_DND = True
except Exception:  # noqa
    TkinterDnD, DND_FILES, HAS_DND = None, None, False

# --- palette (clair, sombre) -------------------------------------------------
ACCENT = "#4C7DFF"
ACCENT_HOVER = "#3A66E0"
ACCENT_SOFT = ("#E8EEFF", "#222E54")
BG = ("#EEF1F6", "#0D0F14")
PANEL = ("#FFFFFF", "#151821")
CARD = ("#F5F7FB", "#1C202B")
CARD_HOVER = ("#EAEEF7", "#252A38")
BORDER = ("#DDE2EC", "#2B3040")
TEXT = ("#1A1E29", "#EEF0F6")
MUTED = ("#69718A", "#97A0B6")
CANVAS_BG = ("#DCE1EA", "#0A0C10")
OK, WARN, ERR, NEUTRAL = "#2FBF71", "#E9A23B", "#E5534B", "#7A8399"
SOFT = {  # fonds doux pour les bandeaux (clair, sombre)
    "info": (("#E8EEFF", "#222E54"), ACCENT),
    "ok": (("#E3F6EC", "#1B3A2B"), OK),
    "warn": (("#FDF1DC", "#3E3017"), WARN),
    "error": (("#FDECEA", "#3A2020"), ERR),
}

FAMILY = "Roboto Mono"
_FALLBACKS = ("Consolas", "DejaVu Sans Mono", "Menlo", "Courier New")
_fonts = {}
_font_dir_cache = None


# =============================================================================
# Chemins, préférences
# =============================================================================
def base_dir():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


PREFS_PATH = Path.home() / ".planche_photos_prefs.json"


def load_prefs():
    try:
        return json.loads(PREFS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_prefs(d):
    try:
        PREFS_PATH.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


# =============================================================================
# Police : Roboto Mono embarquée (fichiers TTF dans le dossier fonts/)
# =============================================================================
def charger_police():
    """Enregistre les fichiers TTF auprès du système (Windows / Linux). Sans effet si absents."""
    ok = False
    for nom in ("RobotoMono-Regular.ttf", "RobotoMono-Bold.ttf"):
        p = base_dir() / "fonts" / nom
        if p.is_file():
            try:
                ok = bool(ctk.FontManager.load_font(str(p))) or ok
            except Exception:  # noqa
                pass
    return ok


def verifier_police(root):
    """À appeler une fois la fenêtre racine créée : choisit la police de secours si Roboto Mono manque."""
    global FAMILY
    try:
        dispo = set(tkfont.families(root))
    except tk.TclError:
        return FAMILY
    if "Roboto Mono" in dispo:
        FAMILY = "Roboto Mono"
    else:
        FAMILY = next((f for f in _FALLBACKS if f in dispo), "TkFixedFont")
    _fonts.clear()
    return FAMILY


def font(size=12, bold=False):
    key = (FAMILY, size, bold)
    if key not in _fonts:
        _fonts[key] = ctk.CTkFont(family=FAMILY, size=size, weight="bold" if bold else "normal")
    return _fonts[key]


def setup(mode="dark"):
    charger_police()
    ctk.set_appearance_mode(mode)
    ctk.set_default_color_theme("blue")


def pick(couleur):
    """Teinte (clair/sombre) d'un tuple de couleurs selon le mode actif."""
    if isinstance(couleur, (tuple, list)):
        return couleur[1] if ctk.get_appearance_mode() == "Dark" else couleur[0]
    return couleur


# =============================================================================
# Fenêtre racine (avec glisser-déposer si tkinterdnd2 est installé)
# =============================================================================
class Root(ctk.CTk):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.dnd_ok = False
        if HAS_DND:
            try:
                TkinterDnD._require(self)
                self.dnd_ok = True
            except Exception:  # noqa
                pass
        verifier_police(self)


def enable_drop(widget, callback):
    """Rend `widget` sensible au dépôt de fichiers ; callback(liste_de_chemins). Renvoie True si actif."""
    if not HAS_DND:
        return False
    try:
        widget.drop_target_register(DND_FILES)

        def _on_drop(e):
            try:
                paths = list(widget.tk.splitlist(e.data))
            except tk.TclError:
                paths = [e.data]
            callback([p for p in paths if p])

        widget.dnd_bind("<<Drop>>", _on_drop)
        return True
    except Exception:  # noqa
        return False


# =============================================================================
# Système
# =============================================================================
def open_file(path):
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def open_folder(path):
    d = Path(path)
    d = d if d.is_dir() else d.parent
    if sys.platform.startswith("win"):
        os.startfile(str(d))  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(d)])
    else:
        subprocess.Popen(["xdg-open", str(d)])


def center_on(win, parent, third=False):
    try:
        win.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_reqwidth()) // 2
        h = parent.winfo_height() - win.winfo_reqheight()
        y = parent.winfo_rooty() + (h // 3 if third else h // 2)
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
    except tk.TclError:
        pass


# =============================================================================
# Icônes (dessinées avec PIL : nettes à toute échelle, aucune image à embarquer)
# =============================================================================
_icon_cache = {}


def _rgba(c):
    c = c.lstrip("#")
    return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4)) + (255,)


def _draw_icon(name, S, col):
    K = 4
    N = S * K
    u = N / 24.0
    w = max(2, int(round(1.9 * u)))
    im = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def P(*pts):
        return [(x * u, y * u) for x, y in pts]

    def ln(*pts, close=False):
        seq = list(pts) + ([pts[0]] if close else [])
        d.line(P(*seq), fill=col, width=w, joint="curve")
        for x, y in (seq[0], seq[-1]):
            r = w / 2
            d.ellipse([x * u - r, y * u - r, x * u + r, y * u + r], fill=col)

    def circ(cx, cy, r, fill=False):
        b = [(cx - r) * u, (cy - r) * u, (cx + r) * u, (cy + r) * u]
        d.ellipse(b, fill=col if fill else None, outline=None if fill else col, width=w)

    def rrect(x0, y0, x1, y1, r=3, fill=False):
        b = [x0 * u, y0 * u, x1 * u, y1 * u]
        d.rounded_rectangle(b, radius=r * u, fill=col if fill else None, outline=None if fill else col, width=w)

    def glyph(ch, size):
        try:
            f = ImageFont.truetype(str(base_dir() / "fonts" / "RobotoMono-Bold.ttf"), int(size * u))
        except OSError:
            f = ImageFont.load_default()
        d.text((12 * u, 12.6 * u), ch, font=f, fill=col, anchor="mm")

    n = name
    if n == "plus":
        ln((12, 5), (12, 19)); ln((5, 12), (19, 12))
    elif n == "close":
        ln((6, 6), (18, 18)); ln((18, 6), (6, 18))
    elif n == "check":
        ln((5, 12.5), (10, 17.5), (19, 7))
    elif n == "up":
        ln((6, 15), (12, 9), (18, 15))
    elif n == "down":
        ln((6, 9), (12, 15), (18, 9))
    elif n == "right":
        ln((5, 12), (19, 12)); ln((13, 6), (19, 12), (13, 18))
    elif n == "left":
        ln((19, 12), (5, 12)); ln((11, 6), (5, 12), (11, 18))
    elif n == "folder":
        ln((3, 6), (9, 6), (11, 8.5), (21, 8.5), (21, 19), (3, 19), close=True)
    elif n == "image":
        rrect(3, 4, 21, 20)
        ln((3.5, 17), (9, 11.5), (14, 16.5), (17, 13.5), (20.5, 17))
        circ(16, 9, 1.7, fill=True)
    elif n == "rotate":
        d.arc(P((4.5, 4.5), (19.5, 19.5)), start=40, end=320, fill=col, width=w)
        ln((18.5, 3), (18.5, 8.5), (13, 8.5))
    elif n == "doc":
        rrect(5, 3, 19, 21)
        ln((8.5, 9), (15.5, 9)); ln((8.5, 13), (15.5, 13)); ln((8.5, 17), (12.5, 17))
    elif n == "sun":
        circ(12, 12, 4)
        for a in range(8):
            import math
            t = a * math.pi / 4
            ln((12 + 7 * math.cos(t), 12 + 7 * math.sin(t)), (12 + 9.6 * math.cos(t), 12 + 9.6 * math.sin(t)))
    elif n == "moon":
        circ(12, 12, 8.5, fill=True)
        d.ellipse([9 * u, 3.2 * u, 24.5 * u, 18.7 * u], fill=(0, 0, 0, 0))
    elif n == "sliders":
        for y, x in ((6, 8), (12, 15), (18, 10)):
            ln((3, y), (21, y))
            circ(x, y, 2.6, fill=True)
    elif n == "wand":
        d.polygon(P((12, 2.5), (14.2, 9.8), (21.5, 12), (14.2, 14.2), (12, 21.5), (9.8, 14.2), (2.5, 12), (9.8, 9.8)), fill=col)
    elif n == "layout":
        rrect(3.5, 3.5, 10.5, 10.5, 2); rrect(13.5, 3.5, 20.5, 10.5, 2)
        rrect(3.5, 13.5, 10.5, 20.5, 2); rrect(13.5, 13.5, 20.5, 20.5, 2)
    elif n == "print":
        rrect(3.5, 9, 20.5, 18, 2.5)
        ln((7, 9), (7, 3.5), (17, 3.5), (17, 9))
        rrect(7, 14, 17, 21, 1)
    elif n == "save":
        ln((12, 4), (12, 15)); ln((7, 10.5), (12, 15.5), (17, 10.5)); ln((4, 19.5), (20, 19.5))
    elif n == "upload":
        ln((12, 16), (12, 4)); ln((7, 9), (12, 4), (17, 9)); ln((4, 15), (4, 20), (20, 20), (20, 15))
    elif n == "paste":
        rrect(5, 5, 19, 21, 2.5)
        rrect(9, 2.8, 15, 6.8, 1.5, fill=True)
    elif n == "trash":
        ln((4, 7), (20, 7)); ln((6, 7), (7, 20), (17, 20), (18, 7))
        ln((9, 7), (9, 4), (15, 4), (15, 7)); ln((10, 11), (10, 16)); ln((14, 11), (14, 16))
    elif n == "gear":
        import math
        circ(12, 12, 6); circ(12, 12, 2.4, fill=True)
        for a in range(8):
            t = a * math.pi / 4
            ln((12 + 6.2 * math.cos(t), 12 + 6.2 * math.sin(t)), (12 + 9.3 * math.cos(t), 12 + 9.3 * math.sin(t)))
    elif n == "help":
        circ(12, 12, 9.2); glyph("?", 13)
    elif n == "info":
        circ(12, 12, 9.2); glyph("i", 13)
    elif n == "warn":
        ln((12, 3.5), (21.5, 20), (2.5, 20), close=True); ln((12, 9.5), (12, 14.5)); circ(12, 17.4, 0.8, fill=True)
    elif n == "dot":
        circ(12, 12, 4, fill=True)
    elif n == "ring":
        circ(12, 12, 6)
    elif n == "eye":
        ln((2.5, 12), (6, 7.5), (12, 5.5), (18, 7.5), (21.5, 12), (18, 16.5), (12, 18.5), (6, 16.5), close=True)
        circ(12, 12, 2.8, fill=True)
    elif n == "keyboard":
        rrect(2.5, 6, 21.5, 18, 2.5)
        for x in (6.5, 10.5, 14.5, 18):
            circ(x, 10, 0.5, fill=True)
        ln((7, 14.5), (17, 14.5))
    else:
        circ(12, 12, 8)
    return im.resize((S * 2, S * 2), Image.LANCZOS)


def icon(name, size=18, color=None):
    """CTkImage d'une icône ; `color` : hex ou tuple (clair, sombre). Par défaut la couleur du texte."""
    color = color or TEXT
    key = (name, size, str(color))
    if key not in _icon_cache:
        lc, dc = (color if isinstance(color, (tuple, list)) else (color, color))
        _icon_cache[key] = ctk.CTkImage(
            light_image=_draw_icon(name, size, _rgba(lc)),
            dark_image=_draw_icon(name, size, _rgba(dc)),
            size=(size, size),
        )
    return _icon_cache[key]


# =============================================================================
# Infobulles
# =============================================================================
class Tooltip:
    def __init__(self, widget, text, delay=500):
        self.widget, self.text, self.delay, self.tw, self._id = widget, text, delay, None, None
        cibles = [widget]
        if hasattr(widget, "_buttons_dict"):  # CTkSegmentedButton : on écoute chacun de ses boutons
            cibles = list(widget._buttons_dict.values())
        for w in cibles:
            try:
                w.bind("<Enter>", self._enter, add="+")
                w.bind("<Leave>", self._leave, add="+")
                w.bind("<ButtonPress>", self._leave, add="+")
            except (NotImplementedError, tk.TclError):
                pass

    def _enter(self, _e=None):
        self._cancel()
        self._id = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._id is not None:
            try:
                self.widget.after_cancel(self._id)
            except tk.TclError:
                pass
            self._id = None

    def _show(self):
        if self.tw is not None or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            self.tw = tw = tk.Toplevel(self.widget)
            tw.wm_overrideredirect(True)
            tw.attributes("-topmost", True)
            f = tk.Frame(tw, bg=pick(BORDER), padx=1, pady=1)
            f.pack()
            tk.Label(f, text=self.text, justify="left", wraplength=300, bg=pick(PANEL), fg=pick(TEXT),
                     font=(FAMILY, 10), padx=9, pady=6).pack()
            tw.update_idletasks()
            sw = tw.winfo_screenwidth()
            x = min(x, sw - tw.winfo_reqwidth() - 8)
            tw.geometry(f"+{max(0, x)}+{y}")
        except tk.TclError:
            self.tw = None

    def _leave(self, _e=None):
        self._cancel()
        if self.tw is not None:
            try:
                self.tw.destroy()
            except tk.TclError:
                pass
            self.tw = None


def tip(widget, text):
    if text:
        Tooltip(widget, text)
    return widget


# =============================================================================
# Boutons
# =============================================================================
def primary_button(parent, text, command, icon_name=None, tooltip=None, **kw):
    kw.setdefault("height", 40)
    if icon_name:
        kw["image"] = icon(icon_name, 18, "#FFFFFF")
        kw["compound"] = "left"
    b = ctk.CTkButton(parent, text=text, command=command, corner_radius=10, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, text_color="#FFFFFF", text_color_disabled="#E2E8F8",
                      font=font(12, True), **kw)
    return tip(b, tooltip)


def ghost_button(parent, text, command, icon_name=None, tooltip=None, **kw):
    kw.setdefault("height", 34)
    if icon_name:
        kw["image"] = icon(icon_name, 16)
        kw["compound"] = "left"
    b = ctk.CTkButton(parent, text=text, command=command, corner_radius=10, fg_color=ACCENT_SOFT,
                      hover_color=BORDER, text_color=TEXT, font=font(12), **kw)
    return tip(b, tooltip)


def danger_button(parent, text, command, icon_name=None, tooltip=None, **kw):
    kw.setdefault("height", 34)
    if icon_name:
        kw["image"] = icon(icon_name, 16, ERR)
        kw["compound"] = "left"
    b = ctk.CTkButton(parent, text=text, command=command, corner_radius=10, fg_color=("#FDECEA", "#3A2020"),
                      hover_color=("#F9D5D1", "#522B2B"), text_color=ERR, font=font(12), **kw)
    return tip(b, tooltip)


def icon_button(parent, icon_name, command, tooltip=None, size=32, icon_size=16, color=None, **kw):
    b = ctk.CTkButton(parent, text="", image=icon(icon_name, icon_size, color or MUTED), command=command,
                      width=size, height=size, corner_radius=8, fg_color="transparent", hover_color=BORDER, **kw)
    return tip(b, tooltip)


# =============================================================================
# Contrôles stylés
# =============================================================================
def segmented(parent, values, variable, command=None, **kw):
    return ctk.CTkSegmentedButton(parent, values=values, variable=variable, command=command,
                                  selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
                                  unselected_color=kw.pop("unselected_color", CARD),
                                  unselected_hover_color=BORDER, text_color=TEXT,
                                  font=font(11, True), corner_radius=8, **kw)


def option_menu(parent, values, variable, command=None, **kw):
    return ctk.CTkOptionMenu(parent, values=values, variable=variable, command=command, height=32,
                             corner_radius=8, fg_color=CARD, button_color=ACCENT,
                             button_hover_color=ACCENT_HOVER, text_color=TEXT, dropdown_fg_color=PANEL,
                             dropdown_text_color=TEXT, dropdown_hover_color=ACCENT_SOFT,
                             font=font(11), dropdown_font=font(11), anchor="w", **kw)


def switch(parent, text, variable, command=None, tooltip=None, **kw):
    s = ctk.CTkSwitch(parent, text=text, variable=variable, command=command, progress_color=ACCENT,
                      font=font(12), text_color=TEXT, **kw)
    return tip(s, tooltip)


def entry(parent, **kw):
    kw.setdefault("height", 32)
    return ctk.CTkEntry(parent, corner_radius=8, border_color=BORDER, fg_color=PANEL, text_color=TEXT,
                        font=font(12), **kw)


def label(parent, text, size=12, bold=False, muted=False, **kw):
    kw.setdefault("anchor", "w")
    kw.setdefault("justify", "left")
    return ctk.CTkLabel(parent, text=text, font=font(size, bold), text_color=MUTED if muted else TEXT, **kw)


def section_label(parent, text):
    return ctk.CTkLabel(parent, text=text, font=font(11), text_color=MUTED, anchor="w")


def field(parent, text, tooltip=None):
    """Libellé de champ (au-dessus du contrôle) ; renvoie le libellé."""
    lb = section_label(parent, text)
    lb.pack(fill="x", pady=(8, 2))
    return tip(lb, tooltip)


# =============================================================================
# Conteneurs
# =============================================================================
class Card(ctk.CTkFrame):
    """Carte arrondie avec titre (et icône) ; placez les widgets dans card.body."""

    def __init__(self, parent, title=None, subtitle=None, bg=None, icon_name=None, wrap=300, **kw):
        if bg is not None:  # fond DERRIÈRE la carte (coins arrondis propres au changement de thème)
            kw["bg_color"] = bg
        super().__init__(parent, corner_radius=14, fg_color=CARD, border_width=1, border_color=BORDER, **kw)
        if title:
            row = ctk.CTkFrame(self, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=(12, 0 if subtitle else 6))
            if icon_name:
                ctk.CTkLabel(row, text="", image=icon(icon_name, 16, ACCENT)).pack(side="left", padx=(0, 8))
            ctk.CTkLabel(row, text=title, font=font(13, True), text_color=TEXT, anchor="w").pack(side="left")
        if subtitle:
            ctk.CTkLabel(self, text=subtitle, font=font(11), text_color=MUTED, anchor="w", justify="left",
                         wraplength=wrap).pack(fill="x", padx=14, pady=(2, 6))
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=14, pady=(0, 12))


class Collapsible(ctk.CTkFrame):
    """Section repliable (options avancées). Contenu dans .body."""

    def __init__(self, parent, title, opened=False, **kw):
        super().__init__(parent, fg_color="transparent", **kw)
        self._open = opened
        self.btn = ctk.CTkButton(self, text=title, command=self.toggle, height=30, corner_radius=8,
                                 fg_color="transparent", hover_color=BORDER, text_color=MUTED, anchor="w",
                                 font=font(11, True), compound="right")
        self.btn.pack(fill="x")
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self._sync()

    def _sync(self):
        self.btn.configure(image=icon("up" if self._open else "down", 14, MUTED))
        if self._open:
            self.body.pack(fill="x", pady=(2, 0))
        else:
            self.body.pack_forget()

    def toggle(self):
        self._open = not self._open
        self._sync()


class SliderRow(ctk.CTkFrame):
    """Libellé + valeur + curseur ; bouton de remise à zéro visible quand la valeur a changé."""

    def __init__(self, parent, text, var, lo, hi, fmt="{:+.0f}", default=0, steps=None, tooltip=None):
        super().__init__(parent, fg_color="transparent")
        self.var, self.fmt, self.default = var, fmt, default
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x")
        top.columnconfigure(0, weight=1)
        self.lbl = ctk.CTkLabel(top, text=text, font=font(12), text_color=TEXT, anchor="w")
        self.lbl.grid(row=0, column=0, sticky="w")
        self.reset = ctk.CTkButton(top, text="", image=icon("rotate", 13, MUTED), width=22, height=22,
                                   corner_radius=6, fg_color="transparent", hover_color=BORDER,
                                   command=lambda: var.set(default))
        self.reset.grid(row=0, column=1, padx=(0, 4))
        tip(self.reset, "Remettre la valeur par défaut")
        self.val = ctk.CTkLabel(top, text="", font=font(11, True), text_color=ACCENT, anchor="e", width=70)
        self.val.grid(row=0, column=2, sticky="e")
        self.lbl.bind("<Double-Button-1>", lambda e: var.set(default))
        tip(self.lbl, tooltip or "Double-clic pour remettre à zéro")
        self.slider = ctk.CTkSlider(self, from_=lo, to=hi, variable=var, number_of_steps=steps or int(hi - lo),
                                    height=16, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
                                    progress_color=ACCENT, fg_color=BORDER)
        self.slider.pack(fill="x", pady=(3, 8))
        var.trace_add("write", lambda *a: self._maj())
        self._maj()

    def _maj(self):
        try:
            v = self.var.get()
            self.val.configure(text=self.fmt.format(v))
            if abs(v - self.default) > 1e-9:
                self.reset.grid()
            else:
                self.reset.grid_remove()
        except (tk.TclError, ValueError):
            pass


class Chip(ctk.CTkLabel):
    """Pastille d'état colorée."""

    def __init__(self, parent, text, color=NEUTRAL):
        super().__init__(parent, text=f"  {text}  ", corner_radius=12, height=26, font=font(11, True),
                         fg_color=color, text_color="#FFFFFF")

    def set(self, text, color):
        self.configure(text=f"  {text}  ", fg_color=color)


class Banner(ctk.CTkFrame):
    """Bandeau d'information (info / ok / warn / error) avec icône, texte et bouton facultatif."""

    def __init__(self, parent, text, kind="info", wrap=300, action=None, action_text=None):
        bgc, fg = SOFT.get(kind, SOFT["info"])
        super().__init__(parent, corner_radius=10, fg_color=bgc)
        self._wrap = wrap
        ico = {"info": "info", "ok": "check", "warn": "warn", "error": "close"}.get(kind, "info")
        self.ic = ctk.CTkLabel(self, text="", image=icon(ico, 16, fg))
        self.ic.pack(side="left", padx=(10, 6), pady=8, anchor="n")
        self.lb = ctk.CTkLabel(self, text=text, font=font(11), text_color=TEXT, anchor="w", justify="left",
                               wraplength=wrap)
        self.lb.pack(side="left", fill="x", expand=True, padx=(0, 10), pady=8)
        self.btn = None
        if action and action_text:
            self.btn = ctk.CTkButton(self, text=action_text, command=action, width=80, height=26,
                                     corner_radius=8, fg_color=fg, hover_color=fg, text_color="#FFFFFF",
                                     font=font(11, True))
            self.btn.pack(side="right", padx=(0, 10))

    def set(self, text, kind=None):
        self.lb.configure(text=text)
        if kind:
            bgc, fg = SOFT.get(kind, SOFT["info"])
            self.configure(fg_color=bgc)
            ico = {"info": "info", "ok": "check", "warn": "warn", "error": "close"}.get(kind, "info")
            self.ic.configure(image=icon(ico, 16, fg))


class Stepper(ctk.CTkFrame):
    """Indicateur d'étapes cliquable : cercles numérotés reliés par un trait."""

    def __init__(self, parent, steps, command):
        super().__init__(parent, fg_color="transparent")
        self.steps, self.command = steps, command
        self.cur, self.done = 0, set()
        self.items = []
        for i, (nom, _ic) in enumerate(steps):
            col = ctk.CTkFrame(self, fg_color="transparent")
            col.pack(side="left")
            c = ctk.CTkLabel(col, text=str(i + 1), width=30, height=30, corner_radius=15, font=font(12, True))
            c.pack()
            t = ctk.CTkLabel(col, text=nom, font=font(10), width=54)
            t.pack(pady=(3, 0))
            for w in (col, c, t):
                w.bind("<Button-1>", lambda e, i=i: self.command(i))
                try:
                    w.configure(cursor="hand2")
                except (tk.TclError, ValueError):
                    pass
            self.items.append((c, t))
            if i < len(steps) - 1:
                ln = ctk.CTkFrame(self, width=1, height=2, fg_color=BORDER)
                ln.pack(side="left", fill="x", expand=True, pady=(0, 18), anchor="s")
        self.refresh()

    def set(self, index, done=None):
        self.cur = index
        if done is not None:
            self.done = set(done)
        self.refresh()

    @staticmethod
    def _vider_image(lbl):
        """CTkLabel garde l'ancienne image avec image=None : on la retire aussi du label interne."""
        try:
            lbl._label.configure(image="")
            lbl._image = None
        except Exception:  # noqa
            pass

    def refresh(self):
        for i, (c, t) in enumerate(self.items):
            if i == self.cur:
                c.configure(fg_color=ACCENT, text_color="#FFFFFF", text=str(i + 1), image=None)
                self._vider_image(c)
                t.configure(text_color=TEXT, font=font(10, True))
            elif i in self.done:
                c.configure(fg_color=OK, text_color="#FFFFFF", text="", image=icon("check", 14, "#FFFFFF"))
                t.configure(text_color=MUTED, font=font(10))
            else:
                c.configure(fg_color=BORDER, text_color=MUTED, text=str(i + 1), image=None)
                self._vider_image(c)
                t.configure(text_color=MUTED, font=font(10))


class Tile(ctk.CTkFrame):
    """Tuile cliquable : titre + détail (réglages rapides, choix guidés)."""

    def __init__(self, parent, title, detail, command, icon_name=None):
        super().__init__(parent, corner_radius=10, fg_color=PANEL, border_width=1, border_color=BORDER)
        self.command = command
        self.columnconfigure(1, weight=1)
        widgets = [self]
        if icon_name:
            ic = ctk.CTkLabel(self, text="", image=icon(icon_name, 18, ACCENT), width=34)
            ic.grid(row=0, column=0, rowspan=2, padx=(8, 0), pady=8)
            widgets.append(ic)
        t = ctk.CTkLabel(self, text=title, font=font(12, True), text_color=TEXT, anchor="w")
        t.grid(row=0, column=1, sticky="ew", padx=(6, 10), pady=(8, 0))
        d = ctk.CTkLabel(self, text=detail, font=font(10), text_color=MUTED, anchor="w", justify="left")
        d.grid(row=1, column=1, sticky="ew", padx=(6, 10), pady=(0, 8))
        widgets += [t, d]
        for w in widgets:
            w.bind("<Button-1>", lambda e: self.command())
            w.bind("<Enter>", lambda e: self.configure(border_color=ACCENT))
            w.bind("<Leave>", lambda e: self.configure(border_color=BORDER))
            try:
                w.configure(cursor="hand2")
            except (tk.TclError, ValueError):
                pass


class DropZone(ctk.CTkFrame):
    """Grande zone « glissez vos images ici » : cliquable, accepte le glisser-déposer."""

    def __init__(self, parent, title, subtitle, command, button_text="Parcourir les fichiers…",
                 icon_name="upload", compact=False):
        super().__init__(parent, corner_radius=18, fg_color=ACCENT_SOFT, border_width=2, border_color=ACCENT)
        self.command = command
        pad = 14 if compact else 30
        self.ic = ctk.CTkLabel(self, text="", image=icon(icon_name, 28 if compact else 44, ACCENT))
        self.ic.pack(pady=(pad, 6))
        self.t = ctk.CTkLabel(self, text=title, font=font(14 if compact else 18, True), text_color=TEXT)
        self.t.pack()
        self.s = ctk.CTkLabel(self, text=subtitle, font=font(11), text_color=MUTED, justify="center")
        self.s.pack(pady=(4, 12 if compact else 16))
        self.b = primary_button(self, button_text, command, height=36)
        self.b.pack(pady=(0, pad))
        for w in (self, self.ic, self.t, self.s):
            w.bind("<Button-1>", lambda e: self.command())
            w.bind("<Enter>", lambda e: self.configure(border_color=ACCENT_HOVER))
            w.bind("<Leave>", lambda e: self.configure(border_color=ACCENT))


class Toast(ctk.CTkFrame):
    """Notification discrète en bas à droite de la fenêtre (disparaît seule)."""

    _current = None

    def __init__(self, parent, text, kind="ok", ms=4000, action=None, action_text=None):
        top = parent.winfo_toplevel()
        if Toast._current is not None:
            try:
                Toast._current.destroy()
            except tk.TclError:
                pass
        _bgc, fg = SOFT.get(kind, SOFT["info"])
        super().__init__(top, corner_radius=12, fg_color=PANEL, border_width=2, border_color=fg)
        ico = {"info": "info", "ok": "check", "warn": "warn", "error": "close"}.get(kind, "info")
        ctk.CTkLabel(self, text="", image=icon(ico, 18, fg)).pack(side="left", padx=(12, 8), pady=10)
        ctk.CTkLabel(self, text=text, font=font(11), text_color=TEXT, justify="left", wraplength=340,
                     anchor="w").pack(side="left", padx=(0, 10), pady=10)
        if action and action_text:
            ctk.CTkButton(self, text=action_text, width=90, height=28, corner_radius=8, fg_color=fg,
                          hover_color=fg, text_color="#FFFFFF", font=font(11, True),
                          command=lambda: (action(), self.destroy())).pack(side="left", padx=(0, 10))
        icon_button(self, "close", self.destroy, size=24, icon_size=12).pack(side="left", padx=(0, 8))
        self.place(relx=1.0, rely=1.0, anchor="se", x=-22, y=-40)
        self.lift()
        Toast._current = self
        self.after(ms, self._fin)

    def _fin(self):
        try:
            self.destroy()
        except tk.TclError:
            pass


def toast(parent, text, kind="ok", **kw):
    return Toast(parent, text, kind, **kw)


# =============================================================================
# Boîtes de dialogue intégrées (remplacent messagebox)
# =============================================================================
class Dialog(ctk.CTkToplevel):
    ICONS = {"info": ("info", ACCENT), "ok": ("check", OK), "warn": ("warn", WARN), "error": ("close", ERR),
             "ask": ("help", ACCENT)}

    def __init__(self, parent, title, message, kind="info", yes="OK", no=None, danger=False):
        super().__init__(parent)
        self.result = False
        self.title(title)
        self.resizable(False, False)
        self.configure(fg_color=PANEL)
        ico, col = self.ICONS.get(kind, self.ICONS["info"])
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(22, 8))
        ctk.CTkLabel(row, text="", width=40, height=40, corner_radius=20, fg_color=col,
                     image=icon(ico, 20, "#FFFFFF")).pack(side="left", anchor="n")
        col_t = ctk.CTkFrame(row, fg_color="transparent")
        col_t.pack(side="left", padx=14)
        ctk.CTkLabel(col_t, text=title, font=font(14, True), text_color=TEXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(col_t, text=message, font=font(12), text_color=MUTED, justify="left", anchor="w",
                     wraplength=380).pack(anchor="w", pady=(4, 0))
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=22, pady=(8, 20))
        main = (danger_button if danger else primary_button)(bar, yes, self._yes, width=120, height=36)
        main.pack(side="right")
        if no:
            ghost_button(bar, no, self._no, width=110, height=36).pack(side="right", padx=(0, 8))
        try:
            self.transient(parent)
        except tk.TclError:
            pass
        center_on(self, parent, third=True)
        self.after(120, self._grab)
        self.bind("<Return>", lambda e: self._yes())
        self.bind("<Escape>", lambda e: self._no())
        self.protocol("WM_DELETE_WINDOW", self._no)

    def _yes(self):
        self.result = True
        self.destroy()

    def _no(self):
        self.result = False
        self.destroy()

    def _grab(self):
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass


def info(parent, title, message):
    Dialog(parent, title, message, "info")


def ok(parent, title, message):
    Dialog(parent, title, message, "ok")


def warn(parent, title, message):
    Dialog(parent, title, message, "warn")


def error(parent, title, message):
    Dialog(parent, title, message, "error")


def ask(parent, title, message, yes="Oui", no="Annuler", danger=False):
    """Question oui / non : renvoie True si l'utilisateur confirme."""
    d = Dialog(parent, title, message, "ask", yes=yes, no=no, danger=danger)
    parent.wait_window(d)
    return d.result
