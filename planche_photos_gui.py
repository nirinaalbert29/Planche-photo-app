#!/usr/bin/env python3
"""
planche_photos_gui.py  -  Planche photos : retouche, changement de fond, mise en page, impression.

Installation :  pip install -r requirements.txt
Lancement    :  python planche_photos_gui.py
Fichiers à garder dans le même dossier : ui_kit.py, bg_remove.py, ocr_word.py, ocr_ui.py, dossier fonts/
"""

import os
import queue
import tempfile
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, colorchooser

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageEnhance, ImageOps, ImageTk

import ui_kit as kit
import bg_remove as bg

try:  # module OCR (image -> Word) : l'application reste utilisable s'il est absent
    from ocr_ui import ouvrir_fenetre_ocr
except Exception:  # noqa
    ouvrir_fenetre_ocr = None

# Formats papier gérés par la L3250 (largeur x hauteur en mm, portrait)
PAPIERS = {
    "A4": (210, 297),
    "A5": (148, 210),
    "A6": (105, 148),
    "10x15": (102, 152),
    "13x18": (127, 178),
    "Letter": (216, 279),
}
LIBRE = "Libre (sans recadrage)"
PERSO = "Personnalisé"
FORMATS_PHOTO = {
    LIBRE: None,
    "35 × 45 mm - identité FR/UE": (35, 45),
    "40 × 50 mm": (40, 50),
    "50 × 50 mm": (50, 50),
    "30 × 40 mm": (30, 40),
    "25 × 35 mm": (25, 35),
    PERSO: None,
}
METHODES = {
    "Auto (IA locale)": "auto",
    "GrabCut (sans IA)": "grabcut",
    "Fond uni (mur, drap)": "uni",
}
VUES = ["Planche", "Photo", "Avant", "Masque"]
ETAPES = [("Photo", "image"), ("Fond", "wand"), ("Retouche", "sliders"), ("Page", "layout"), ("Export", "save")]
INDICES = [
    "Ouvrez une photo (Ctrl+O), glissez-la dans la fenêtre ou collez-la (Ctrl+V).",
    "Cochez « Remplacer le fond » : la personne est détectée automatiquement.",
    "Ajustez la lumière et le contraste. Le petit bouton rond remet un réglage à zéro.",
    "Choisissez le format de la photo, le papier et le nombre de copies.",
    "Enregistrez en PDF ou imprimez : la planche est générée en 300 dpi.",
]
PRESETS = [
    dict(nom="Photo d'identité", detail="35×45 mm, fond blanc, 6 sur 10×15", format="35 × 45 mm - identité FR/UE",
         papier="10x15", copies="6", fond="#FFFFFF"),
    dict(nom="Photo d'identité sur A4", detail="35×45 mm, fond blanc, 8 sur A4", format="35 × 45 mm - identité FR/UE",
         papier="A4", copies="8", fond="#FFFFFF"),
    dict(nom="Petit format 25×35", detail="25×35 mm, 8 sur A4, fond inchangé", format="25 × 35 mm",
         papier="A4", copies="8", fond=None),
    dict(nom="Tirage simple", detail="photo entière, 4 sur A4, sans recadrage", format=LIBRE,
         papier="A4", copies="4", fond=None),
]
EXT_IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
DPI = 300


# ----------------------------------------------------------------------------
# Logique (mise en page + retouches + fond)
# ----------------------------------------------------------------------------
def mm_px(mm, dpi):
    return int(round(mm * dpi / 25.4))


def grilles(n):
    """Toutes les grilles (colonnes, lignes) possibles pour n copies."""
    return [(c, n // c) for c in range(1, n + 1) if n % c == 0]


def calcul_mise_en_page(ratio, p):
    """Choisit la meilleure disposition (ou celle imposée par l'utilisateur)."""
    pw, ph = PAPIERS[p["papier"]]
    if p["orientation"] == "auto":
        orientations = [False, True]
    else:
        orientations = [p["orientation"] == "paysage"]
    grids = [p["grille"]] if p["grille"] else grilles(p["n"])

    meilleur = None
    for paysage in orientations:
        W, H = (ph, pw) if paysage else (pw, ph)
        for cols, lignes in grids:
            cw = (W - 2 * p["marge"] - (cols - 1) * p["espace"]) / cols
            ch = (H - 2 * p["marge"] - (lignes - 1) * p["espace"]) / lignes
            if cw <= 0 or ch <= 0:
                continue
            if p["taille_fixe"]:
                w, h = p["taille_fixe"]
                if w > cw + 1e-6 or h > ch + 1e-6:
                    continue
            else:
                h = min(ch, cw / ratio)
                w = h * ratio
                w, h = w * p["echelle"], h * p["echelle"]
            if meilleur is None or w * h > meilleur["w"] * meilleur["h"]:
                meilleur = dict(W=W, H=H, cols=cols, lignes=lignes, w=w, h=h, paysage=paysage)
    return meilleur


def appliquer_retouches(img, r):
    if r["auto"]:
        img = ImageOps.autocontrast(img, cutoff=1)
    img = ImageEnhance.Brightness(img).enhance(1 + r["lum"] / 100)
    img = ImageEnhance.Contrast(img).enhance(1 + r["contr"] / 100)
    img = ImageEnhance.Color(img).enhance(1 + r["sat"] / 100)
    img = ImageEnhance.Sharpness(img).enhance(1 + r["nette"] / 100)
    if r["nb"]:
        img = ImageOps.grayscale(img).convert("RGB")
    return img


def fabriquer_photo(raw, pw, ph, taille_fixe, cadre=None):
    """Taille fixe : recadrage au ratio demandé autour du point (cx, cy) avec un zoom z (photo d'identité)."""
    if not taille_fixe:
        return raw.resize((pw, ph), Image.LANCZOS)
    cx, cy, z = cadre or (0.5, 0.45, 1.0)
    w, h = raw.size
    cible = pw / ph
    if w / h > cible:
        ch, cw = float(h), h * cible
    else:
        cw, ch = float(w), w / cible
    cw, ch = cw / max(1.0, z), ch / max(1.0, z)
    x0 = min(max(cx * w - cw / 2, 0), w - cw)
    y0 = min(max(cy * h - ch / 2, 0), h - ch)
    return raw.crop((int(round(x0)), int(round(y0)), int(round(x0 + cw)), int(round(y0 + ch)))).resize(
        (pw, ph), Image.LANCZOS
    )


def photo_finale(raw, retouches, pw, ph, taille_fixe, fond=None, cadre=None):
    """Recadrage + retouches, puis remplacement du fond (le masque subit le même recadrage)."""
    photo = appliquer_retouches(fabriquer_photo(raw, pw, ph, taille_fixe, cadre), retouches)
    if fond:
        mk = fabriquer_photo(fond["mask"], pw, ph, taille_fixe, cadre)
        photo = bg.composer_fond(photo, mk, fond["couleur"], fond["adoucir"], fond["contracter"])
    return photo


def rendre_planche(raw, retouches, p, dpi, fond=None, cadre=None):
    """Retourne (planche, infos) ou (None, message d'erreur)."""
    m = calcul_mise_en_page(raw.width / raw.height, p)
    if m is None:
        return None, "Les photos ne rentrent pas : réduisez la taille, la marge ou l'espace."

    W, H = mm_px(m["W"], dpi), mm_px(m["H"], dpi)
    planche = Image.new("RGB", (W, H), "white")
    pw, ph = max(1, mm_px(m["w"], dpi)), max(1, mm_px(m["h"], dpi))
    photo = photo_finale(raw, retouches, pw, ph, p["taille_fixe"], fond, cadre)

    esp, mg = mm_px(p["espace"], dpi), mm_px(p["marge"], dpi)
    bw = m["cols"] * pw + (m["cols"] - 1) * esp
    bh = m["lignes"] * ph + (m["lignes"] - 1) * esp
    if p["align"] == "Haut gauche":
        x0, y0 = mg, mg
    elif p["align"] == "Haut centré":
        x0, y0 = (W - bw) // 2, mg
    else:
        x0, y0 = (W - bw) // 2, (H - bh) // 2

    d = ImageDraw.Draw(planche)
    for r in range(m["lignes"]):
        for c in range(m["cols"]):
            x, y = x0 + c * (pw + esp), y0 + r * (ph + esp)
            planche.paste(photo, (x, y))
            if p["reperes"]:
                d.rectangle([x - 1, y - 1, x + pw, y + ph], outline=(160, 160, 160), width=1)

    infos = (
        f"{p['papier']} {'paysage' if m['paysage'] else 'portrait'}  •  "
        f"grille {m['cols']}×{m['lignes']}  •  photo {m['w']:.1f} × {m['h']:.1f} mm"
    )
    return planche, infos


def charger_image(chemin):
    """Ouvre une image, applique l'orientation EXIF, remplace la transparence par du blanc."""
    img = ImageOps.exif_transpose(Image.open(chemin))
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        fond = Image.new("RGB", img.size, "white")
        fond.paste(img, mask=img.split()[-1])
        return fond
    return img.convert("RGB")


# ----------------------------------------------------------------------------
# Interface
# ----------------------------------------------------------------------------
def carte(parent, *a, **k):
    return kit.Card(parent, *a, bg=kit.PANEL, **k)


class App(kit.Root):
    def __init__(self):
        self.prefs = kit.load_prefs()
        kit.setup(self.prefs.get("theme", "dark"))
        super().__init__()
        self.title("Planche photos")
        self.geometry("1440x880")
        self.minsize(1180, 720)
        self.configure(fg_color=kit.BG)

        self.raw = None  # image complète (pour l'export)
        self.raw_small = None  # version réduite (pour l'aperçu)
        self.mask_full = None  # masque du sujet (taille de raw)
        self.mask_small = None
        self.nom, self.chemin = "", None
        self._job = self._job_tol = None
        self._tkimg = None
        self.q = queue.Queue()
        self.etape = 0
        self.visites = set()
        self.dernier_dossier = self.prefs.get("dossier") or str(Path.home())

        V = tk
        # --- retouches
        self.lum, self.contr = V.DoubleVar(value=0), V.DoubleVar(value=0)
        self.sat, self.nette = V.DoubleVar(value=0), V.DoubleVar(value=0)
        self.auto, self.nb = V.BooleanVar(value=False), V.BooleanVar(value=False)
        # --- fond
        self.fond_on = V.BooleanVar(value=False)
        self.fond_voulu = False  # réglage rapide choisi avant l'ouverture de la photo
        self.fond_hex = V.StringVar(value=self.prefs.get("fond", "#FFFFFF"))
        self.fond_methode = V.StringVar(value=list(METHODES)[0])
        self.fond_adoucir, self.fond_contracter = V.DoubleVar(value=4), V.DoubleVar(value=1)
        self.fond_tol = V.DoubleVar(value=30)
        # --- mise en page
        self.copies = V.StringVar(value=self.prefs.get("copies", "8"))
        self.papier = V.StringVar(value=self.prefs.get("papier", "A4"))
        if self.papier.get() not in PAPIERS:
            self.papier.set("A4")
        self.orient = V.StringVar(value="Auto")
        self.grille = V.StringVar(value="Auto")
        self.marge, self.espace = V.DoubleVar(value=5.0), V.DoubleVar(value=2.0)
        self.echelle = V.DoubleVar(value=100)
        self.fixe = V.BooleanVar(value=False)
        self.fw, self.fh = V.StringVar(value="35"), V.StringVar(value="45")
        self.fmt_photo = V.StringVar(value=LIBRE)
        self.zoom, self.cadre_x, self.cadre_y = V.DoubleVar(value=100), V.DoubleVar(value=50), V.DoubleVar(value=45)
        self.align = V.StringVar(value="Centré")
        self.reperes = V.BooleanVar(value=True)
        self.vue = V.StringVar(value="Planche")
        self.sombre = V.BooleanVar(value=self.prefs.get("theme", "dark") == "dark")

        self.construire()
        self.maj_grilles()
        self.maj_cadrage()
        for v in (
            self.lum, self.contr, self.sat, self.nette, self.auto, self.nb, self.copies, self.papier,
            self.orient, self.grille, self.marge, self.espace, self.echelle, self.fixe, self.fw, self.fh,
            self.align, self.reperes, self.vue, self.fond_on, self.fond_hex, self.fond_adoucir,
            self.fond_contracter, self.zoom, self.cadre_x, self.cadre_y,
        ):
            v.trace_add("write", lambda *a: self.planifier())
        self.copies.trace_add("write", lambda *a: self.maj_grilles())
        self.fixe.trace_add("write", lambda *a: self.maj_cadrage())
        self.fond_on.trace_add("write", lambda *a: self.sur_fond_on())
        self.fond_tol.trace_add("write", lambda *a: self.planifier_tolerance())
        self.fond_methode.trace_add("write", lambda *a: self.maj_methode())

        self.bind("<Control-o>", lambda e: self.ouvrir_image())
        self.bind("<Control-s>", lambda e: self.enregistrer())
        self.bind("<Control-p>", lambda e: self.imprimer())
        self.bind("<Control-v>", lambda e: self.coller(silencieux=True))
        self.bind("<Control-Right>", lambda e: self.suivant())
        self.bind("<Control-Left>", lambda e: self.precedent())
        self.bind("<F1>", lambda e: self.aide())
        self.protocol("WM_DELETE_WINDOW", self.fermer)
        kit.enable_drop(self, self.sur_depot)
        self.aller(0, force=True)
        self.after(100, self.poll)

    # ------------------------------------------------------------------ construction
    def construire(self):
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # ================= barre latérale
        side = ctk.CTkFrame(self, width=400, corner_radius=0, fg_color=kit.PANEL)
        side.grid(row=0, column=0, sticky="ns")
        side.grid_propagate(False)
        side.pack_propagate(False)

        head = ctk.CTkFrame(side, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(16, 4))
        ctk.CTkLabel(head, text="", width=38, height=38, corner_radius=10, fg_color=kit.ACCENT,
                     image=kit.icon("image", 20, "#FFFFFF")).pack(side="left")
        tt = ctk.CTkFrame(head, fg_color="transparent")
        tt.pack(side="left", padx=10)
        kit.label(tt, "Planche photos", 16, True).pack(anchor="w")
        kit.label(tt, "Retouche, fond, impression", 10, muted=True).pack(anchor="w")
        self.btn_aide = kit.icon_button(head, "help", self.aide, "Aide et raccourcis (F1)")
        self.btn_aide.pack(side="right")
        self.btn_theme = kit.icon_button(head, "sun" if self.sombre.get() else "moon", self.basculer_theme,
                                         "Basculer thème clair / sombre")
        self.btn_theme.pack(side="right")

        self.stepper = kit.Stepper(side, ETAPES, self.aller)
        self.stepper.pack(fill="x", padx=18, pady=(12, 6))

        ctk.CTkFrame(side, height=1, fg_color=kit.BORDER).pack(fill="x", padx=18, pady=(4, 0))

        # navigation en bas (créée avant la zone pour garder sa place)
        nav = ctk.CTkFrame(side, fg_color="transparent")
        nav.pack(side="bottom", fill="x", padx=18, pady=(8, 16))
        self.btn_prec = kit.ghost_button(nav, "Précédent", self.precedent, icon_name="left", width=130, height=38)
        self.btn_prec.pack(side="left")
        self.btn_suiv = kit.primary_button(nav, "Suivant", self.suivant, height=38)
        self.btn_suiv.pack(side="right", fill="x", expand=True, padx=(10, 0))

        self.zone = ctk.CTkFrame(side, fg_color="transparent")
        self.zone.pack(fill="both", expand=True, padx=6, pady=(4, 0))
        self.pages = []
        for builder in (self.page_photo, self.page_fond, self.page_retouche, self.page_page, self.page_export):
            sf = ctk.CTkScrollableFrame(self.zone, fg_color="transparent")
            builder(sf)
            self.pages.append(sf)

        # ================= zone principale
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=16, pady=14)
        main.rowconfigure(1, weight=1)
        main.columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(main, corner_radius=14, fg_color=kit.PANEL, border_width=1, border_color=kit.BORDER)
        bar.grid(row=0, column=0, sticky="ew")
        kit.primary_button(bar, "Ouvrir", self.ouvrir_image, icon_name="folder", width=110, height=36,
                           tooltip="Ouvrir une photo (Ctrl+O)").pack(side="left", padx=(10, 6), pady=10)
        kit.ghost_button(bar, "Coller", lambda: self.coller(), icon_name="paste", width=100,
                         tooltip="Coller une image copiée (Ctrl+V)").pack(side="left", padx=3)
        kit.ghost_button(bar, "Pivoter", self.pivoter, icon_name="rotate", width=104,
                         tooltip="Pivoter la photo de 90°").pack(side="left", padx=3)
        kit.ghost_button(bar, "Texte vers Word", self.ocr_word, icon_name="doc", width=150,
                         tooltip="Extraire le texte d'une image dans un document Word (OCR)").pack(side="left", padx=3)
        self.seg_vue = kit.segmented(bar, VUES, self.vue, height=32)
        self.seg_vue.pack(side="right", padx=10, pady=10)
        kit.tip(self.seg_vue, "Planche : résultat imprimé  •  Photo : une seule photo  •  Avant : original  •  "
                              "Masque : ce qui est conservé")

        stage = ctk.CTkFrame(main, corner_radius=16, fg_color=kit.CANVAS_BG)
        stage.grid(row=1, column=0, sticky="nsew", pady=10)
        self.canvas = tk.Canvas(stage, bg=kit.pick(kit.CANVAS_BG), highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True, padx=8, pady=8)
        self.canvas.bind("<Configure>", lambda e: self.planifier())
        kit.enable_drop(self.canvas, self.sur_depot)

        self.vide = ctk.CTkFrame(stage, fg_color="transparent")
        self.vide.place(relx=0.5, rely=0.5, anchor="center")
        self.dropzone = kit.DropZone(
            self.vide, "Glissez votre photo ici",
            "JPG, PNG, WEBP, BMP, TIFF  •  ou collez avec Ctrl+V", self.ouvrir_image,
            button_text="Choisir une photo…", icon_name="upload")
        self.dropzone.pack(ipadx=44)
        kit.enable_drop(self.dropzone, self.sur_depot)
        kit.label(self.vide, "Ou démarrez avec un réglage rapide :", 11, muted=True).pack(pady=(18, 6))
        tiles = ctk.CTkFrame(self.vide, fg_color="transparent")
        tiles.pack(fill="x")
        tiles.columnconfigure((0, 1), weight=1, uniform="t")
        for i, ps in enumerate(PRESETS[:2]):
            kit.Tile(tiles, ps["nom"], ps["detail"], lambda ps=ps: self.preset_puis_ouvrir(ps),
                     "image").grid(row=0, column=i, sticky="ew", padx=4)

        status = ctk.CTkFrame(main, fg_color="transparent")
        status.grid(row=2, column=0, sticky="ew")
        self.lbl_info = ctk.CTkLabel(status, text="", font=kit.font(11), text_color=kit.MUTED, anchor="w")
        self.lbl_info.pack(side="left")
        self.chip_alerte = kit.Chip(status, "", kit.WARN)
        self.lbl_hint = ctk.CTkLabel(main, text="", font=kit.font(11), text_color=kit.ACCENT, anchor="w")
        self.lbl_hint.grid(row=3, column=0, sticky="ew", pady=(2, 0))

    def _menu(self, parent, label, var, values, tooltip=None, command=None):
        kit.field(parent, label, tooltip)
        m = kit.option_menu(parent, values, var, command=command)
        m.pack(fill="x")
        return m

    # ---- étape 1 : Photo
    def page_photo(self, t):
        c = carte(t, "Votre photo", icon_name="image")
        c.pack(fill="x", pady=6, padx=4)
        self.lbl_nom = kit.label(c.body, "Aucune photo ouverte", 12, True, wraplength=320)
        self.lbl_nom.pack(fill="x")
        self.lbl_dims = kit.label(c.body, "Cliquez sur « Ouvrir » ou glissez une image dans la fenêtre.", 11,
                                  muted=True, wraplength=320)
        self.lbl_dims.pack(fill="x", pady=(2, 8))
        row = ctk.CTkFrame(c.body, fg_color="transparent")
        row.pack(fill="x")
        row.columnconfigure((0, 1, 2), weight=1, uniform="b")
        kit.primary_button(row, "Ouvrir", self.ouvrir_image, icon_name="folder", height=36).grid(
            row=0, column=0, sticky="ew", padx=(0, 4))
        kit.ghost_button(row, "Coller", lambda: self.coller(), icon_name="paste", height=36).grid(
            row=0, column=1, sticky="ew", padx=4)
        kit.ghost_button(row, "Pivoter", self.pivoter, icon_name="rotate", height=36).grid(
            row=0, column=2, sticky="ew", padx=(4, 0))

        c2 = carte(t, "Réglages rapides", "Un clic règle le format, le papier et le fond.", icon_name="wand")
        c2.pack(fill="x", pady=6, padx=4)
        for ps in PRESETS:
            kit.Tile(c2.body, ps["nom"], ps["detail"], lambda ps=ps: self.appliquer_preset(ps)).pack(
                fill="x", pady=3)

    # ---- étape 2 : Fond
    def page_fond(self, t):
        c = carte(t, "Changer le fond", "Détecte la personne puis remplace l'arrière-plan "
                                        "(idéal pour les photos d'identité).", icon_name="wand")
        c.pack(fill="x", pady=6, padx=4)
        kit.switch(c.body, "Remplacer le fond", self.fond_on,
                   tooltip="Active le remplacement du fond et lance la détection").pack(anchor="w", pady=(2, 4))
        self.menu_methode = self._menu(c.body, "Méthode de détection", self.fond_methode, list(METHODES),
                                       "Auto : la plus précise. GrabCut : sans IA. Fond uni : mur ou drap uni.")
        self.btn_detect = kit.primary_button(c.body, "Détecter la personne", self.lancer_detection,
                                             icon_name="wand", height=38)
        self.btn_detect.pack(fill="x", pady=(10, 0))
        self.prog = ctk.CTkProgressBar(c.body, mode="indeterminate", height=6, progress_color=kit.ACCENT)
        self.lbl_fond = kit.label(c.body, "Aucun masque. Ouvrez une photo puis cliquez sur « Détecter ».", 11,
                                  muted=True, wraplength=320)
        self.lbl_fond.pack(fill="x", pady=(8, 0))
        if not bg.disponibilites()["rembg"]:
            kit.Banner(c.body, "rembg n'est pas installé : « Auto » utilisera GrabCut. "
                               "Pour plus de précision : pip install rembg onnxruntime", "warn", wrap=270).pack(
                fill="x", pady=(8, 0))

        c2 = carte(t, "Couleur du fond", icon_name="image")
        c2.pack(fill="x", pady=6, padx=4)
        grid = ctk.CTkFrame(c2.body, fg_color="transparent")
        grid.pack(fill="x")
        grid.columnconfigure(tuple(range(len(bg.COULEURS_FOND))), weight=1)
        for i, (nom, hx) in enumerate(bg.COULEURS_FOND.items()):
            col = ctk.CTkFrame(grid, fg_color="transparent")
            col.grid(row=0, column=i)
            b = ctk.CTkButton(col, text="", width=38, height=38, corner_radius=19, fg_color=hx, hover_color=hx,
                              border_width=2, border_color=kit.BORDER, command=lambda h=hx: self.choisir_fond(h))
            b.pack()
            kit.tip(b, nom)
            ctk.CTkLabel(col, text={"Gris clair": "Gris", "Bleu ciel": "Ciel"}.get(nom, nom), font=kit.font(9),
                         text_color=kit.MUTED).pack()
        row = ctk.CTkFrame(c2.body, fg_color="transparent")
        row.pack(fill="x", pady=(10, 0))
        self.swatch = ctk.CTkLabel(row, text="", width=34, height=34, corner_radius=8, fg_color=self.fond_hex.get())
        self.swatch.pack(side="left")
        ctk.CTkLabel(row, textvariable=self.fond_hex, font=kit.font(12, True), text_color=kit.TEXT).pack(
            side="left", padx=10)
        kit.ghost_button(row, "Autre…", self.couleur_perso, width=90).pack(side="right")

        c3 = carte(t, "Finition des bords", "Augmentez « Rogner » si un liseré de l'ancien fond reste visible.",
                   icon_name="sliders")
        c3.pack(fill="x", pady=6, padx=4)
        kit.SliderRow(c3.body, "Adoucir les bords", self.fond_adoucir, 0, 20, fmt="{:.0f}", default=4,
                      tooltip="Flou léger entre la personne et le nouveau fond").pack(fill="x")
        kit.SliderRow(c3.body, "Rogner / élargir", self.fond_contracter, -10, 10, default=1,
                      tooltip="Positif : rogne la personne (supprime un liseré). Négatif : l'élargit").pack(fill="x")
        self.row_tol = kit.SliderRow(c3.body, "Tolérance (fond uni)", self.fond_tol, 5, 80, fmt="{:.0f}", default=30,
                                     tooltip="Écart de couleur accepté pour considérer un pixel comme fond")
        self.row_tol.pack(fill="x")
        self.maj_methode()
        kit.Banner(t, "Astuce : la vue « Masque » (barre du haut) montre ce qui est conservé.", "info",
                   wrap=300).pack(fill="x", padx=4, pady=6)

    # ---- étape 3 : Retouche
    def page_retouche(self, t):
        c = carte(t, "Réglages de l'image", "Le petit bouton rond à droite d'un réglage le remet à zéro.", icon_name="sliders")
        c.pack(fill="x", pady=6, padx=4)
        kit.SliderRow(c.body, "Luminosité", self.lum, -100, 100).pack(fill="x")
        kit.SliderRow(c.body, "Contraste", self.contr, -100, 100).pack(fill="x")
        kit.SliderRow(c.body, "Netteté", self.nette, -100, 200).pack(fill="x")
        kit.SliderRow(c.body, "Saturation", self.sat, -100, 100).pack(fill="x")
        kit.switch(c.body, "Auto-niveaux", self.auto,
                   tooltip="Équilibre automatiquement les tons clairs et sombres").pack(anchor="w", pady=4)
        kit.switch(c.body, "Noir et blanc", self.nb).pack(anchor="w", pady=4)
        b = ctk.CTkFrame(t, fg_color="transparent")
        b.pack(fill="x", pady=4, padx=4)
        kit.ghost_button(b, "Tout réinitialiser", self.reinitialiser, icon_name="rotate").pack(fill="x", pady=3)
        kit.ghost_button(b, "Aperçu détaillé (300 dpi)", self.apercu_detail, icon_name="eye",
                         tooltip="Zone centrale de la photo à la résolution d'impression").pack(fill="x", pady=3)

    # ---- étape 4 : Page
    def page_page(self, t):
        c = carte(t, "Format de la photo", icon_name="image")
        c.pack(fill="x", pady=6, padx=4)
        self._menu(c.body, "Taille de chaque photo", self.fmt_photo, list(FORMATS_PHOTO),
                   "Choisissez un format officiel ou « Libre » pour garder la photo entière",
                   command=self.choisir_format)
        self.grp_dims = ctk.CTkFrame(c.body, fg_color="transparent")
        r = ctk.CTkFrame(self.grp_dims, fg_color="transparent")
        r.pack(fill="x", pady=(8, 0))
        kit.entry(r, textvariable=self.fw, width=70, justify="center").pack(side="left")
        ctk.CTkLabel(r, text="  ×  ", text_color=kit.MUTED, font=kit.font(12)).pack(side="left")
        kit.entry(r, textvariable=self.fh, width=70, justify="center").pack(side="left")
        ctk.CTkLabel(r, text="  mm", text_color=kit.MUTED, font=kit.font(12)).pack(side="left")
        self.grp_cadre = ctk.CTkFrame(c.body, fg_color="transparent")
        kit.field(self.grp_cadre, "Cadrage (déplace le visage dans la photo)")
        kit.SliderRow(self.grp_cadre, "Zoom", self.zoom, 100, 300, fmt="{:.0f} %", default=100).pack(fill="x")
        kit.SliderRow(self.grp_cadre, "Position horizontale", self.cadre_x, 0, 100, fmt="{:.0f} %",
                      default=50).pack(fill="x")
        kit.SliderRow(self.grp_cadre, "Position verticale", self.cadre_y, 0, 100, fmt="{:.0f} %",
                      default=45).pack(fill="x")

        c2 = carte(t, "Planche d'impression", icon_name="layout")
        c2.pack(fill="x", pady=6, padx=4)
        kit.field(c2.body, "Nombre de copies")
        kit.segmented(c2.body, ["4", "6", "8"], self.copies, height=32).pack(fill="x")
        self._menu(c2.body, "Format du papier", self.papier, list(PAPIERS))
        kit.field(c2.body, "Orientation du papier")
        kit.segmented(c2.body, ["Auto", "Portrait", "Paysage"], self.orient, height=32).pack(fill="x")

        adv = kit.Collapsible(t, "Options avancées")
        adv.pack(fill="x", padx=4, pady=(4, 8))
        c3 = kit.Card(adv.body, bg=kit.PANEL)
        c3.pack(fill="x")
        self.cb_grille = self._menu(c3.body, "Grille (colonnes x lignes)", self.grille, ["Auto"])
        self._menu(c3.body, "Alignement sur la page", self.align, ["Centré", "Haut centré", "Haut gauche"])
        kit.SliderRow(c3.body, "Marge du papier", self.marge, 0, 40, fmt="{:.1f} mm", default=5, steps=80).pack(
            fill="x", pady=(8, 0))
        kit.SliderRow(c3.body, "Espace entre photos", self.espace, 0, 30, fmt="{:.1f} mm", default=2,
                      steps=60).pack(fill="x")
        kit.SliderRow(c3.body, "Taille (% du maximum)", self.echelle, 30, 100, fmt="{:.0f} %", default=100,
                      tooltip="Réduit les photos (sans effet avec une taille fixe)").pack(fill="x")
        kit.switch(c3.body, "Traits de découpe", self.reperes,
                   tooltip="Contour gris autour de chaque photo pour faciliter la découpe").pack(anchor="w", pady=4)

    # ---- étape 5 : Export
    def page_export(self, t):
        c = carte(t, "Votre planche", icon_name="layout")
        c.pack(fill="x", pady=6, padx=4)
        self.lbl_recap = kit.label(c.body, "Ouvrez une photo pour voir le récapitulatif.", 11, muted=True,
                                   wraplength=320)
        self.lbl_recap.pack(fill="x")

        c1 = carte(t, "Enregistrer et imprimer", "La planche est générée en 300 dpi.", icon_name="save")
        c1.pack(fill="x", pady=6, padx=4)
        kit.primary_button(c1.body, "Enregistrer en PDF ou PNG", self.enregistrer, icon_name="save", height=42,
                           tooltip="Ctrl+S").pack(fill="x", pady=4)
        kit.ghost_button(c1.body, "Imprimer", self.imprimer, icon_name="print", height=38,
                         tooltip="Ouvre le PDF pour l'imprimer (Ctrl+P)").pack(fill="x", pady=4)
        kit.ghost_button(c1.body, "Tous les formats de papier…", self.exporter_tous, icon_name="folder",
                         height=38, tooltip="Crée un PDF et un PNG pour chaque format de papier").pack(fill="x", pady=4)

        c2 = carte(t, "Réglages d'impression", "Epson L3250 ou autre imprimante", icon_name="print")
        c2.pack(fill="x", pady=6, padx=4)
        kit.label(c2.body, "1.  Échelle 100 % / taille réelle\n     (pas « ajuster à la page »)\n"
                           "2.  Qualité : Photo\n3.  Marge de 3 mm ou plus pour ne\n     pas couper les bords", 11,
                  muted=True).pack(fill="x")

    # ------------------------------------------------------------------ navigation
    def aller(self, i, force=False):
        if i != 0 and self.raw is None and not force:
            kit.toast(self, "Ouvrez d'abord une photo pour continuer.", "info")
            return
        if not force and i != self.etape:
            self.visites.add(self.etape)
        self.etape = max(0, min(i, len(self.pages) - 1))
        for k, p in enumerate(self.pages):
            if k == self.etape:
                p.pack(fill="both", expand=True)
            else:
                p.pack_forget()
        self.maj_navigation()

    def maj_navigation(self):
        n = len(self.pages)
        done = {k for k in self.visites if k != self.etape}
        if self.raw is not None:
            done.add(0)
        else:
            done.discard(0)
        self.stepper.set(self.etape, done)
        self.btn_prec.configure(state="normal" if self.etape > 0 else "disabled")
        if self.etape < n - 1:
            nxt = ETAPES[self.etape + 1][0]
            self.btn_suiv.configure(text=f"Suivant : {nxt}", image=kit.icon("right", 18, "#FFFFFF"),
                                    compound="right", command=self.suivant)
        else:
            self.btn_suiv.configure(text="Enregistrer", image=kit.icon("save", 18, "#FFFFFF"), compound="left",
                                    command=self.enregistrer)
        self.lbl_hint.configure(text=INDICES[self.etape])
        if self.etape == 4:
            self.maj_recap()

    def suivant(self):
        if self.etape < len(self.pages) - 1:
            self.aller(self.etape + 1)

    def precedent(self):
        if self.etape > 0:
            self.aller(self.etape - 1)

    # ------------------------------------------------------------------ thème / aide
    def basculer_theme(self):
        self.sombre.set(not self.sombre.get())
        ctk.set_appearance_mode("dark" if self.sombre.get() else "light")
        self.btn_theme.configure(image=kit.icon("sun" if self.sombre.get() else "moon", 16, kit.MUTED))
        self.canvas.configure(bg=kit.pick(kit.CANVAS_BG))
        self.planifier()

    def aide(self):
        kit.info(self, "Comment ça marche",
                 "1. Photo : ouvrez, glissez ou collez une image.\n"
                 "2. Fond : remplacez l'arrière-plan en un clic.\n"
                 "3. Retouche : lumière, contraste, netteté.\n"
                 "4. Page : format de photo, papier, copies.\n"
                 "5. Export : PDF, PNG ou impression.\n\n"
                 "Raccourcis :\n"
                 "Ctrl+O ouvrir   Ctrl+V coller\n"
                 "Ctrl+S enregistrer   Ctrl+P imprimer\n"
                 "Ctrl+Gauche / Droite : étape précédente / suivante\n"
                 "F1 : cette aide")

    def fermer(self):
        self.prefs.update(theme="dark" if self.sombre.get() else "light", fond=self.fond_hex.get(),
                          copies=self.copies.get(), papier=self.papier.get(), dossier=self.dernier_dossier)
        kit.save_prefs(self.prefs)
        self.destroy()

    # ------------------------------------------------------------------ paramètres
    def maj_grilles(self):
        valeurs = ["Auto"] + [f"{c}x{l}" for c, l in grilles(int(self.copies.get()))]
        self.cb_grille.configure(values=valeurs)
        if self.grille.get() not in valeurs:
            self.grille.set("Auto")

    def maj_cadrage(self):
        """Affiche les champs de taille et de cadrage seulement quand une taille fixe est choisie."""
        if self.fixe.get():
            self.grp_dims.pack(fill="x")
            self.grp_cadre.pack(fill="x", pady=(6, 0))
        else:
            self.grp_dims.pack_forget()
            self.grp_cadre.pack_forget()

    def choisir_format(self, nom):
        dim = FORMATS_PHOTO.get(nom)
        if nom == LIBRE:
            self.fixe.set(False)
        elif dim:
            self.fw.set(str(dim[0]))
            self.fh.set(str(dim[1]))
            self.fixe.set(True)
        else:  # personnalisé
            self.fixe.set(True)

    def appliquer_preset(self, ps, silencieux=False):
        self.fmt_photo.set(ps["format"])
        self.choisir_format(ps["format"])
        self.papier.set(ps["papier"])
        self.copies.set(ps["copies"])
        self.orient.set("Auto")
        self.grille.set("Auto")
        self.fond_voulu = bool(ps["fond"])
        if ps["fond"]:
            self.choisir_fond(ps["fond"], active=False)
        if self.raw is not None:
            self.fond_on.set(self.fond_voulu)
        if not silencieux:
            kit.toast(self, f"Réglage appliqué : {ps['nom']}", "ok", ms=2500)

    def preset_puis_ouvrir(self, ps):
        self.appliquer_preset(ps, silencieux=True)
        self.ouvrir_image()

    def params(self):
        fixe = None
        if self.fixe.get():
            fixe = (float(self.fw.get().replace(",", ".")), float(self.fh.get().replace(",", ".")))
            if fixe[0] <= 0 or fixe[1] <= 0:
                raise ValueError
        g = self.grille.get()
        grille = None if g == "Auto" else tuple(int(x) for x in g.split("x"))
        if self.marge.get() < 0 or self.espace.get() < 0:
            raise ValueError
        return dict(
            papier=self.papier.get(), n=int(self.copies.get()), marge=round(self.marge.get(), 1),
            espace=round(self.espace.get(), 1), taille_fixe=fixe, orientation=self.orient.get().lower(),
            grille=grille, echelle=self.echelle.get() / 100, align=self.align.get(), reperes=self.reperes.get(),
        )

    def retouches(self):
        return dict(lum=self.lum.get(), contr=self.contr.get(), sat=self.sat.get(), nette=self.nette.get(),
                    auto=self.auto.get(), nb=self.nb.get())

    def cadre(self):
        return (self.cadre_x.get() / 100, self.cadre_y.get() / 100, self.zoom.get() / 100)

    def fond(self, complet=False):
        """Paramètres de remplacement du fond, ou None si désactivé / pas de masque."""
        mask = self.mask_full if complet else self.mask_small
        if not self.fond_on.get() or mask is None:
            return None
        return dict(mask=mask, couleur=bg.hex_rgb(self.fond_hex.get()), adoucir=self.fond_adoucir.get(),
                    contracter=self.fond_contracter.get())

    # ------------------------------------------------------------------ image
    def ouvrir_image(self):
        chemin = filedialog.askopenfilename(
            title="Choisir une photo", initialdir=self.dernier_dossier,
            filetypes=[("Images", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff"), ("Tous les fichiers", "*.*")])
        if chemin:
            self.charger(chemin)

    def sur_depot(self, chemins):
        for p in chemins:
            if Path(p).suffix.lower() in EXT_IMAGES:
                self.charger(p)
                return
        kit.toast(self, "Ce fichier n'est pas une image reconnue (JPG, PNG, WEBP, BMP, TIFF).", "warn")

    def coller(self, silencieux=False):
        try:
            from PIL import ImageGrab
            data = ImageGrab.grabclipboard()
        except Exception:  # noqa
            data = None
        if isinstance(data, list) and data:
            self.sur_depot([str(x) for x in data])
            return
        if isinstance(data, Image.Image):
            fd, p = tempfile.mkstemp(prefix="planche_collee_", suffix=".png")
            os.close(fd)
            data.save(p)
            self.charger(p, nom="Image collée")
            return
        if not silencieux:
            kit.toast(self, "Aucune image dans le presse-papiers. Copiez d'abord une image.", "info")

    def charger(self, chemin, nom=None):
        try:
            img = charger_image(chemin)
        except Exception as e:
            kit.error(self, "Erreur", f"Impossible d'ouvrir l'image :\n{e}")
            return
        self.raw, self.nom, self.chemin = img, nom and "image_collee" or Path(chemin).stem, chemin
        self.dernier_dossier = str(Path(chemin).parent) if not nom else self.dernier_dossier
        self.mask_full = self.mask_small = None
        self.fond_on.set(False)
        self.lbl_fond.configure(text="Aucun masque. Cliquez sur « Détecter la personne ».", text_color=kit.MUTED)
        self.vide.place_forget()
        self.title(f"Planche photos - {nom or Path(chemin).name}")
        self.lbl_nom.configure(text=nom or Path(chemin).name)
        self.lbl_dims.configure(text=f"{img.width} × {img.height} px", text_color=kit.MUTED)
        self.maj_petite()
        self.visites.add(0)
        self.planifier()
        if self.etape == 0 and self.fond_voulu:
            self.aller(1)
        self.maj_navigation()
        if self.fond_voulu:
            self.fond_on.set(True)  # lance la détection

    def maj_petite(self):
        self.raw_small = self.raw.copy()
        self.raw_small.thumbnail((1000, 1000), Image.LANCZOS)
        if self.mask_full is not None:
            self.mask_small = self.mask_full.resize(self.raw_small.size, Image.LANCZOS)

    def pivoter(self):
        if self.raw is None:
            kit.toast(self, "Ouvrez d'abord une photo.", "info")
            return
        self.raw = self.raw.rotate(-90, expand=True)
        if self.mask_full is not None:
            self.mask_full = self.mask_full.rotate(-90, expand=True)
        self.lbl_dims.configure(text=f"{self.raw.width} × {self.raw.height} px")
        self.maj_petite()
        self.planifier()

    def reinitialiser(self):
        for v in (self.lum, self.contr, self.sat, self.nette):
            v.set(0)
        self.auto.set(False)
        self.nb.set(False)

    def ocr_word(self):
        if ouvrir_fenetre_ocr is None:
            kit.error(self, "OCR", "Module ocr_ui.py introuvable (placez-le à côté de ce programme).")
            return
        ouvrir_fenetre_ocr(self, self.chemin)

    # ------------------------------------------------------------------ fond
    def choisir_fond(self, hx, active=True):
        self.fond_hex.set(hx)
        self.swatch.configure(fg_color=hx)
        if active and self.raw is not None and not self.fond_on.get():
            self.fond_on.set(True)

    def couleur_perso(self):
        c = colorchooser.askcolor(color=self.fond_hex.get(), parent=self, title="Couleur du fond")
        if c and c[1]:
            self.choisir_fond(c[1].upper())

    def maj_methode(self):
        uni = METHODES.get(self.fond_methode.get()) == "uni"
        try:
            if uni:
                self.row_tol.pack(fill="x")
            else:
                self.row_tol.pack_forget()
        except Exception:  # noqa
            pass

    def sur_fond_on(self):
        if self.fond_on.get() and self.raw is not None and self.mask_full is None:
            self.lancer_detection()
        self.swatch.configure(fg_color=self.fond_hex.get())

    def lancer_detection(self):
        if self.raw is None:
            kit.toast(self, "Ouvrez d'abord une photo.", "info")
            return
        methode = METHODES[self.fond_methode.get()]
        raw, tol = self.raw, int(self.fond_tol.get())
        self.btn_detect.configure(state="disabled", text="Détection en cours…")
        self.prog.pack(fill="x", pady=(8, 0))
        self.prog.start()
        self.lbl_fond.configure(text="Analyse de l'image… (le premier lancement de l'IA télécharge le modèle, "
                                     "~170 Mo, une seule fois)", text_color=kit.MUTED)

        def work():
            try:
                m, nom = bg.calculer_masque(raw, methode, tol, log=lambda s: self.q.put(("fondlog", s)))
                self.q.put(("mask", raw, m, nom))
            except Exception as e:
                self.q.put(("fonderr", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def planifier_tolerance(self):
        if METHODES.get(self.fond_methode.get()) != "uni" or self.mask_full is None:
            return
        if self._job_tol is not None:
            self.after_cancel(self._job_tol)
        self._job_tol = self.after(250, self.recalcul_uni)

    def recalcul_uni(self):
        self._job_tol = None
        if self.raw_small is None:
            return
        try:
            m = bg.masque_fond_uni(self.raw_small, int(self.fond_tol.get()))
            self.mask_small = m
            self.mask_full = m.resize(self.raw.size, Image.LANCZOS)
            self.planifier()
        except Exception as e:
            self.lbl_fond.configure(text=f"Erreur : {e}", text_color=kit.ERR)

    def poll(self):
        try:
            while True:
                kind, *a = self.q.get_nowait()
                if kind == "fondlog":
                    self.lbl_fond.configure(text=a[0], text_color=kit.MUTED)
                else:
                    self.prog.stop()
                    self.prog.pack_forget()
                    self.btn_detect.configure(state="normal", text="Détecter la personne")
                    if kind == "mask":
                        raw, m, nom = a
                        if raw is self.raw:  # ignore si l'image a changé entre-temps
                            self.mask_full = m
                            self.mask_small = m.resize(self.raw_small.size, Image.LANCZOS)
                            self.lbl_fond.configure(text=f"Personne détectée : {nom}", text_color=kit.OK)
                            if not self.fond_on.get():
                                self.fond_on.set(True)
                            self.maj_navigation()
                            self.planifier()
                    else:
                        self.lbl_fond.configure(text=a[0], text_color=kit.ERR)
        except queue.Empty:
            pass
        self.after(120, self.poll)

    # ------------------------------------------------------------------ aperçu
    def planifier(self):
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(40, self.rafraichir)

    def afficher(self, img, infos, alerte=None):
        cv = self.canvas
        cv.delete("all")
        cw, ch = max(cv.winfo_width(), 50), max(cv.winfo_height(), 50)
        s = min((cw - 40) / img.width, (ch - 40) / img.height)
        vue = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.BILINEAR)
        self._tkimg = ImageTk.PhotoImage(vue)
        cx, cy = cw // 2, ch // 2
        ombre = "#000000" if ctk.get_appearance_mode() == "Dark" else "#AEB6C5"
        for k, off in enumerate((10, 7, 4)):
            cv.create_rectangle(cx - vue.width // 2 + off, cy - vue.height // 2 + off,
                                cx + vue.width // 2 + off, cy + vue.height // 2 + off,
                                fill=ombre, outline="", stipple="gray25" if k < 2 else "gray50")
        cv.create_image(cx, cy, image=self._tkimg)
        self.lbl_info.configure(text=infos)
        if alerte:
            self.chip_alerte.set(alerte, kit.WARN)
            self.chip_alerte.pack(side="right")
        else:
            self.chip_alerte.pack_forget()

    def maj_recap(self):
        if self.raw is None:
            self.lbl_recap.configure(text="Ouvrez une photo pour voir le récapitulatif.")
            return
        try:
            m = calcul_mise_en_page(self.raw.width / self.raw.height, self.params())
        except (tk.TclError, ValueError):
            return
        if m is None:
            self.lbl_recap.configure(text="Les photos ne rentrent pas : changez la taille, la marge ou l'espace.",
                                     text_color=kit.ERR)
            return
        fond = "oui, " + self.fond_hex.get() if self.fond() else "non"
        self.lbl_recap.configure(
            text=f"Papier : {self.papier.get()} ({'paysage' if m['paysage'] else 'portrait'})\n"
                 f"Copies : {self.copies.get()} (grille {m['cols']}×{m['lignes']})\n"
                 f"Photo : {m['w']:.1f} × {m['h']:.1f} mm\nFond remplacé : {fond}", text_color=kit.TEXT)

    def rafraichir(self):
        self._job = None
        if self.raw_small is None:
            return
        try:
            p = self.params()
        except (tk.TclError, ValueError):
            return  # saisie en cours / invalide
        if self.etape == 4:
            self.maj_recap()
        vue = self.vue.get()
        if vue == "Avant":
            self.afficher(self.raw_small, f"Image originale  •  {self.raw.width} × {self.raw.height} px")
            return
        if vue == "Masque":
            if self.mask_small is None:
                self.afficher(Image.new("RGB", (400, 400), "#222222"),
                              "Aucun masque : étape « Fond » puis « Détecter la personne »")
            else:
                self.afficher(self.mask_small.convert("RGB"),
                              "Masque de la personne  (blanc = conservé, noir = remplacé)")
            return
        fond, cadre = self.fond(), self.cadre()
        if vue == "Photo":
            m = calcul_mise_en_page(self.raw_small.width / self.raw_small.height, p)
            if m is None:
                self.afficher(Image.new("RGB", (400, 400), "#222222"), "Les photos ne rentrent pas.")
                return
            pw, ph = max(1, mm_px(m["w"], 200)), max(1, mm_px(m["h"], 200))
            photo = photo_finale(self.raw_small, self.retouches(), pw, ph, p["taille_fixe"], fond, cadre)
            self.afficher(photo, f"Photo seule  •  {m['w']:.1f} × {m['h']:.1f} mm")
            return
        planche, infos = rendre_planche(self.raw_small, self.retouches(), p, 100, fond, cadre)
        if planche is None:
            self.afficher(Image.new("RGB", (400, 400), "#222222"), infos)
            return
        self.afficher(planche, infos,
                      "Marge < 3 mm : l'imprimante peut couper les bords" if p["marge"] < 3 else None)

    def apercu_detail(self):
        if self.raw is None:
            kit.toast(self, "Ouvrez d'abord une photo.", "info")
            return
        try:
            p = self.params()
        except (tk.TclError, ValueError):
            return
        m = calcul_mise_en_page(self.raw.width / self.raw.height, p)
        if m is None:
            return
        pw, ph = mm_px(m["w"], DPI), mm_px(m["h"], DPI)
        photo = photo_finale(self.raw, self.retouches(), pw, ph, p["taille_fixe"], self.fond(complet=True),
                             self.cadre())
        w, h = min(pw, 900), min(ph, 700)
        l, t = (pw - w) // 2, (ph - h) // 2
        crop = photo.crop((l, t, l + w, t + h))
        top = ctk.CTkToplevel(self)
        top.title("Aperçu détaillé - 300 dpi (zone centrale)")
        top.configure(fg_color=kit.BG)
        top.after(150, lambda: (top.lift(), top.focus_force()))
        ctk.CTkLabel(top, text="", image=ctk.CTkImage(light_image=crop, dark_image=crop, size=crop.size)).pack(
            padx=14, pady=14)

    # ------------------------------------------------------------------ export / impression
    def exporter(self, chemin, papier=None):
        p = self.params()
        if papier:
            p["papier"] = papier
        planche, infos = rendre_planche(self.raw, self.retouches(), p, DPI, self.fond(complet=True), self.cadre())
        if planche is None:
            return False, infos
        try:
            if str(chemin).lower().endswith(".png"):
                planche.save(chemin, dpi=(DPI, DPI))
            else:
                planche.save(chemin, "PDF", resolution=DPI)
        except OSError as e:
            return False, f"Impossible d'écrire le fichier :\n{e}\n(fermez-le s'il est ouvert ailleurs)"
        return True, infos

    def verifier(self):
        if self.raw is None:
            kit.toast(self, "Ouvrez d'abord une photo.", "info")
            return False
        try:
            self.params()
        except (tk.TclError, ValueError):
            kit.error(self, "Réglages", "Une valeur de la mise en page est invalide (étape Page).")
            return False
        if self.fond_on.get() and self.mask_full is None:
            kit.warn(self, "Fond", "Le fond n'a pas encore été détecté : étape « Fond », puis « Détecter la personne ».")
            return False
        return True

    def enregistrer(self):
        if not self.verifier():
            return
        chemin = filedialog.asksaveasfilename(
            defaultextension=".pdf", initialdir=self.dernier_dossier,
            initialfile=f"{self.nom}_{self.copies.get()}x_{self.papier.get()}",
            filetypes=[("PDF", "*.pdf"), ("PNG", "*.png")])
        if not chemin:
            return
        ok, msg = self.exporter(chemin)
        if ok:
            self.dernier_dossier = str(Path(chemin).parent)
            kit.toast(self, f"Enregistré : {Path(chemin).name}", "ok", ms=7000,
                      action=lambda: kit.open_file(chemin), action_text="Ouvrir")
        else:
            kit.error(self, "Enregistrement", msg)

    def exporter_tous(self):
        if not self.verifier():
            return
        dossier = filedialog.askdirectory(title="Dossier de sortie", initialdir=self.dernier_dossier)
        if not dossier:
            return
        resume = []
        for f in PAPIERS:
            base = Path(dossier) / f"{self.nom}_{self.copies.get()}x_{f}"
            ok, msg = self.exporter(f"{base}.pdf", papier=f)
            if ok:
                self.exporter(f"{base}.png", papier=f)
            resume.append(f"{f} : {'OK' if ok else 'impossible'}")
        self.dernier_dossier = dossier
        kit.ok(self, "Export terminé", "\n".join(resume))

    def imprimer(self):
        if not self.verifier():
            return
        fd, chemin = tempfile.mkstemp(prefix="planche_impression_", suffix=".pdf")
        os.close(fd)
        ok, msg = self.exporter(chemin)
        if not ok:
            kit.error(self, "Impression", msg)
            return
        kit.info(self, "Impression",
                 "Le PDF va s'ouvrir. Dans la fenêtre d'impression :\n"
                 "- imprimante : EPSON L3250\n"
                 f"- papier : {self.papier.get()}\n"
                 "- échelle : 100 % / taille réelle (PAS « ajuster à la page »)\n"
                 "- qualité : Photo")
        try:
            kit.open_file(chemin)
        except Exception as e:
            kit.error(self, "Impression", f"Impossible d'ouvrir le PDF :\n{chemin}\n{e}")


if __name__ == "__main__":
    App().mainloop()
