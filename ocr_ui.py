#!/usr/bin/env python3
"""
ocr_ui.py - interface "Image vers Word (OCR)" : ajoutez des images, choisissez le mode, créez le Word.
Lancement autonome : python ocr_ui.py      (ou via le bouton « Texte vers Word » de planche_photos_gui.py)
"""

import os
import queue
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
from PIL import Image, ImageOps

import ui_kit as kit
from ocr_word import (
    PROVIDERS,
    load_config,
    save_config,
    process_images,
    self_test,
    tesseract_info,
    rapid_engine,
    CONFIG_PATH,
)

EXT_IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
IMG_TYPES = [
    ("Images", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff"),
    ("Tous les fichiers", "*.*"),
]
MODE_AUTO, MODE_LOCAL = "Automatique", "Hors ligne"
GLYPHES = {"→": ">", "✔": "OK", "✘": "ERREUR", "✗": "x", "⚠": "Attention :"}  # absents de Roboto Mono


def _taille(p):
    try:
        n = Path(p).stat().st_size
    except OSError:
        return ""
    return f"{n / 1048576:.1f} Mo" if n >= 1048576 else f"{max(1, n // 1024)} Ko"


def _court(nom, n=30):
    return nom if len(nom) <= n else nom[: n // 2 - 1] + "..." + nom[-(n // 2 - 2):]


def _vignette(path):
    try:
        im = Image.open(path)
        im.draft("RGB", (160, 160))
        im = ImageOps.exif_transpose(im).convert("RGB")
        im = ImageOps.fit(im, (88, 88), Image.LANCZOS)
        return ctk.CTkImage(light_image=im, dark_image=im, size=(44, 44))
    except Exception:  # noqa
        return None


# =============================================================================
class SettingsWindow(ctk.CTkToplevel):
    def __init__(self, parent, cfg):
        super().__init__(parent)
        self.title("Paramètres de l'IA et de l'OCR")
        self.geometry("660x780")
        self.minsize(620, 600)
        self.configure(fg_color=kit.PANEL)
        self.cfg, self.q, self.entries = cfg, queue.Queue(), {}
        self.after(150, lambda: (self.lift(), self.focus_force()))

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=22, pady=(18, 0))
        kit.label(head, "Paramètres", 18, True).pack(anchor="w")
        kit.label(head, "L'IA lit vos images en priorité ; l'OCR local prend le relais en cas de quota, d'erreur "
                        "ou sans connexion.", 11, muted=True, wraplength=590).pack(anchor="w", pady=(2, 8))

        # barre du bas (créée avant le contenu pour garder sa place)
        bas = ctk.CTkFrame(self, fg_color="transparent")
        bas.pack(side="bottom", fill="x", padx=18, pady=(4, 14))
        kit.label(bas, f"Clés enregistrées en clair dans : {CONFIG_PATH}", 10, muted=True, wraplength=600).pack(
            anchor="w", pady=(0, 6))
        kit.ghost_button(bas, "Tester les moteurs", self.tester, icon_name="check", width=170).pack(side="left")
        kit.primary_button(bas, "Enregistrer", self.enregistrer, width=140, height=36).pack(side="right")

        sc = ctk.CTkScrollableFrame(self, fg_color="transparent")
        sc.pack(fill="both", expand=True, padx=8)

        card = kit.Card(sc, "Clés d'accès IA", "Une seule clé suffit. Gemini est gratuit et recommandé.",
                        bg=kit.PANEL, icon_name="gear", wrap=560)
        card.pack(fill="x", padx=10, pady=4)
        for k, lab, secret, lien in (
            ("gemini_key", "Google Gemini (gratuit)", True, "https://aistudio.google.com/apikey"),
            ("xai_key", "xAI Grok (optionnel, payant)", True, None),
            ("openrouter_key", "OpenRouter (modèles gratuits)", True, "https://openrouter.ai/keys"),
        ):
            self._champ(card.body, k, lab, secret, lien)
        self.v_show = ctk.BooleanVar(value=False)
        kit.switch(card.body, "Afficher les clés", self.v_show, command=self._toggle).pack(anchor="w", pady=(2, 0))

        adv = kit.Collapsible(sc, "Options avancées")
        adv.pack(fill="x", padx=10, pady=4)
        card2 = kit.Card(adv.body, bg=kit.PANEL, wrap=560)
        card2.pack(fill="x")
        self._champ(card2.body, "ordre", "Ordre des moteurs IA (séparés par des virgules)", False)
        self._champ(card2.body, "langues_tesseract", "Langues Tesseract (ex : fra+eng)", False)
        self._champ(card2.body, "tesseract_path", "Chemin de tesseract.exe (vide = automatique)", False)

        self.res = ctk.CTkFrame(sc, fg_color=kit.CARD, corner_radius=12, border_width=1, border_color=kit.BORDER)
        self.res.pack(fill="x", padx=10, pady=8)
        self.lbl_res = kit.label(self.res, "Cliquez sur « Tester les moteurs » pour vérifier vos clés "
                                           "(aucun quota consommé).", 11, muted=True, wraplength=560)
        self.lbl_res.pack(fill="x", padx=14, pady=12)

    def _champ(self, parent, key, label, secret, lien=None):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(6, 2))
        kit.section_label(row, label).pack(side="left")
        if lien:
            b = ctk.CTkButton(row, text="Obtenir une clé", width=110, height=22, corner_radius=6,
                              fg_color="transparent", hover_color=kit.BORDER, text_color=kit.ACCENT,
                              font=kit.font(10, True), command=lambda: webbrowser.open(lien))
            b.pack(side="right")
        e = kit.entry(parent, show="•" if secret else "", height=34)
        e.insert(0, str(self.cfg.get(key, "")))
        e.pack(fill="x", pady=(0, 4))
        self.entries[key] = (e, secret)

    def _toggle(self):
        for e, secret in self.entries.values():
            if secret:
                e.configure(show="" if self.v_show.get() else "•")

    def _collect(self):
        c = dict(self.cfg)
        for k, (e, _) in self.entries.items():
            c[k] = e.get().strip()
        return c

    def enregistrer(self):
        self.cfg.update(self._collect())
        if save_config(self.cfg):
            self.destroy()
        else:
            kit.error(self, "Paramètres", "Impossible d'écrire le fichier de configuration.")

    def tester(self):
        for w in self.res.winfo_children():
            w.destroy()
        kit.label(self.res, "Test en cours...", 11, muted=True).pack(fill="x", padx=14, pady=12)
        cfg = self._collect()

        def work():
            try:
                self.q.put(self_test(cfg))
            except Exception as e:  # noqa
                self.q.put([f"✘ Erreur du test : {e}"])

        threading.Thread(target=work, daemon=True).start()
        self.after(200, self._poll)

    def _poll(self):
        try:
            lignes = self.q.get_nowait()
        except queue.Empty:
            if self.winfo_exists():
                self.after(200, self._poll)
            return
        if not self.winfo_exists():
            return
        for w in self.res.winfo_children():
            w.destroy()
        for l in lignes:
            ico, col = (("check", kit.OK) if l.startswith("✔") else ("close", kit.ERR) if l.startswith("✘")
                        else ("ring", kit.NEUTRAL))
            row = ctk.CTkFrame(self.res, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=4)
            ctk.CTkLabel(row, text="", image=kit.icon(ico, 16, col), width=24).pack(side="left", anchor="n")
            kit.label(row, l[1:].strip(), 11, wraplength=500).pack(side="left", fill="x", expand=True)


# =============================================================================
class OCRWindow(ctk.CTkToplevel):
    ETATS = {  # état -> (icône, couleur)
        "attente": ("ring", kit.NEUTRAL),
        "cours": ("dot", kit.ACCENT),
        "ok": ("check", kit.OK),
        "warn": ("warn", kit.WARN),
        "echec": ("close", kit.ERR),
    }

    def __init__(self, parent, fichier=None):
        super().__init__(parent)
        self.title("Image vers Word (OCR)")
        self.geometry("1020x800")
        self.minsize(900, 680)
        self.configure(fg_color=kit.BG)
        self.cfg = load_config()
        self.q, self.stop, self.files = queue.Queue(), threading.Event(), []
        self.thumbs, self.sortie, self.occupe = {}, None, False
        self.v_mode = ctk.StringVar(value=MODE_LOCAL if self.cfg.get("mode_local_seul") else MODE_AUTO)
        self.v_verif = ctk.BooleanVar(value=bool(self.cfg.get("verif_croisee")))
        self.after(150, lambda: (self.lift(), self.focus_force()))
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # --- en-tête
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 6))
        ctk.CTkLabel(head, text="", width=42, height=42, corner_radius=11, fg_color=kit.ACCENT,
                     image=kit.icon("doc", 22, "#FFFFFF")).pack(side="left")
        tt = ctk.CTkFrame(head, fg_color="transparent")
        tt.pack(side="left", padx=12)
        kit.label(tt, "Image vers Word", 20, True).pack(anchor="w")
        kit.label(tt, "Extrait le texte de vos images et le range dans un document Word.", 11, muted=True).pack(
            anchor="w")
        kit.icon_button(head, "gear", self.parametres, "Paramètres, clés d'accès IA et test").pack(side="right")

        # --- corps : deux colonnes
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=18, pady=4)
        body.columnconfigure(0, weight=3, uniform="c")
        body.columnconfigure(1, weight=2, uniform="c")
        body.rowconfigure(0, weight=1)

        left = kit.Card(body, "1.  Ajoutez vos images", "L'ordre de la liste est l'ordre des pages du Word.",
                        bg=kit.BG, icon_name="image", wrap=520)
        left.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.dz = kit.DropZone(left.body, "Glissez vos images ici", "JPG, PNG, WEBP, BMP, TIFF  •  ou Ctrl+V pour coller",
                               self.ajouter, button_text="Choisir des images…", compact=True)
        self.listwrap = ctk.CTkFrame(left.body, fg_color="transparent")
        self.liste = ctk.CTkScrollableFrame(self.listwrap, fg_color=kit.PANEL, corner_radius=10)
        self.liste.pack(fill="both", expand=True, pady=(0, 8))
        bar = ctk.CTkFrame(self.listwrap, fg_color="transparent")
        bar.pack(fill="x")
        kit.ghost_button(bar, "Ajouter", self.ajouter, icon_name="plus", width=100).pack(side="left")
        kit.ghost_button(bar, "Coller", lambda: self.coller(), icon_name="paste", width=100).pack(
            side="left", padx=6)
        self.btn_vider = kit.danger_button(bar, "Tout retirer", self.vider, icon_name="trash", width=130)
        self.btn_vider.pack(side="right")
        self.lbl_nb = kit.label(bar, "", 11, muted=True)
        self.lbl_nb.pack(side="right", padx=10)

        right = ctk.CTkFrame(body, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=6, pady=6)
        mode = kit.Card(right, "2.  Choisissez le mode", bg=kit.BG, icon_name="sliders", wrap=330)
        mode.pack(fill="x")
        kit.segmented(mode.body, [MODE_AUTO, MODE_LOCAL], self.v_mode, command=lambda v: self.maj_mode(),
                      height=34).pack(fill="x")
        self.bn_mode = kit.Banner(mode.body, "", "info", wrap=270)
        self.bn_mode.pack(fill="x", pady=(10, 4))
        kit.switch(mode.body, "Vérification croisée IA / OCR local", self.v_verif,
                   tooltip="Compare le texte de l'IA avec l'OCR local et signale les écarts importants").pack(
            anchor="w", pady=(6, 0))
        moteurs = kit.Card(right, "Moteurs disponibles", "Du plus puissant au repli.", bg=kit.BG, icon_name="gear",
                           wrap=330)
        moteurs.pack(fill="x", pady=(10, 0))
        self.chip_cloud, self.chip_tess, self.chip_rapid = (kit.Chip(moteurs.body, "") for _ in range(3))
        for c in (self.chip_cloud, self.chip_tess, self.chip_rapid):
            c.pack(anchor="w", pady=3)
        kit.ghost_button(moteurs.body, "Paramètres et test…", self.parametres, icon_name="gear").pack(
            fill="x", pady=(10, 0))

        # --- action
        act = ctk.CTkFrame(self, fg_color="transparent")
        act.grid(row=2, column=0, sticky="ew", padx=24, pady=(8, 0))
        act.columnconfigure(2, weight=1)
        kit.label(act, "3.", 14, True).grid(row=0, column=0, padx=(0, 8))
        self.btn_go = kit.primary_button(act, "Créer le document Word", self.lancer, icon_name="doc", height=46,
                                         width=270, tooltip="Choisit où enregistrer puis lance la conversion")
        self.btn_go.grid(row=0, column=1)
        self.bar = ctk.CTkProgressBar(act, height=10, progress_color=kit.ACCENT, fg_color=kit.BORDER)
        self.bar.set(0)
        self.bar.grid(row=0, column=2, sticky="ew", padx=14)
        self.btn_stop = kit.danger_button(act, "Annuler", self.annuler, height=46, width=100, state="disabled")
        self.btn_stop.grid(row=0, column=3)
        self.lbl_etat = kit.label(act, "Ajoutez au moins une image pour commencer.", 11, muted=True)
        self.lbl_etat.grid(row=1, column=1, columnspan=3, sticky="w", pady=(6, 0))

        # --- résultat (visible après la conversion)
        self.res = ctk.CTkFrame(self, corner_radius=12, fg_color=kit.CARD, border_width=1, border_color=kit.BORDER)
        self.res.grid(row=3, column=0, sticky="ew", padx=24, pady=(10, 0))
        self.res.grid_remove()
        self.lbl_res = kit.label(self.res, "", 12, wraplength=620)
        self.lbl_res.pack(side="left", padx=14, pady=12, fill="x", expand=True)
        kit.icon_button(self.res, "close", self.res.grid_remove, "Masquer", size=26, icon_size=12).pack(
            side="right", padx=(0, 8))
        self.btn_dossier = kit.ghost_button(self.res, "Dossier", self.ouvrir_dossier, icon_name="folder", width=110)
        self.btn_dossier.pack(side="right", padx=4, pady=10)
        self.btn_ouvrir = kit.primary_button(self.res, "Ouvrir le Word", self.ouvrir_word, icon_name="doc",
                                             width=160, height=34)
        self.btn_ouvrir.pack(side="right", padx=4, pady=10)

        # --- journal repliable
        self.journal = kit.Collapsible(self, "Détails du traitement")
        self.journal.grid(row=4, column=0, sticky="ew", padx=20, pady=(6, 14))
        self.log = ctk.CTkTextbox(self.journal.body, height=150, corner_radius=12, fg_color=kit.PANEL,
                                  border_width=1, border_color=kit.BORDER, font=kit.font(11),
                                  text_color=kit.TEXT, state="disabled")
        self.log.pack(fill="x")

        for w in (self, self.dz, self.liste):
            kit.enable_drop(w, self.sur_depot)
        self.bind("<Control-v>", lambda e: self.coller(silencieux=True))
        self.bind("<Control-o>", lambda e: self.ajouter())

        if fichier:
            self.files.append(dict(path=fichier, etat="attente", note=""))
        self.render_files()
        self.maj_etat()
        self.maj_mode()

    # ---- fichiers
    def render_files(self):
        for w in self.liste.winfo_children():
            w.destroy()
        if not self.files:
            self.listwrap.pack_forget()
            self.dz.pack(fill="both", expand=True)
            self.maj_action()
            return
        self.dz.pack_forget()
        self.listwrap.pack(fill="both", expand=True)
        n = len(self.files)
        self.lbl_nb.configure(text=f"{n} image{'s' if n > 1 else ''}")
        for i, f in enumerate(self.files):
            p = f["path"]
            r = ctk.CTkFrame(self.liste, fg_color=kit.CARD, corner_radius=10)
            r.pack(fill="x", pady=3, padx=2)
            if p not in self.thumbs:
                self.thumbs[p] = _vignette(p)
            th = self.thumbs[p]
            ctk.CTkLabel(r, text="" if th else str(i + 1), image=th, width=44, height=44, corner_radius=8,
                         fg_color=kit.BORDER, text_color=kit.MUTED).pack(side="left", padx=(8, 10), pady=8)
            t = ctk.CTkFrame(r, fg_color="transparent")
            t.pack(side="left", fill="x", expand=True)
            kit.label(t, _court(Path(p).name), 12, True).pack(anchor="w")
            note = f["note"] or f"Page {i + 1}  •  {_taille(p)}"
            kit.label(t, note, 10, muted=True, wraplength=230).pack(anchor="w")
            if not self.occupe:
                for sym, cmd, tp in (("close", lambda i=i: self.retirer(i), "Retirer"),
                                     ("down", lambda i=i: self.deplacer(i, 1), "Descendre"),
                                     ("up", lambda i=i: self.deplacer(i, -1), "Monter")):
                    kit.icon_button(r, sym, cmd, tp, size=28, icon_size=14).pack(side="right", padx=1)
            ic, col = self.ETATS[f["etat"]]
            ctk.CTkLabel(r, text="", image=kit.icon(ic, 18, col), width=26).pack(side="right", padx=(0, 6))
        self.maj_action()

    def maj_action(self):
        if self.occupe:
            return
        if self.files:
            self.lbl_etat.configure(text="Prêt : cliquez sur « Créer le document Word ».", text_color=kit.MUTED)
        else:
            self.lbl_etat.configure(text="Ajoutez au moins une image pour commencer.", text_color=kit.MUTED)

    def ajouter(self):
        chemins = filedialog.askopenfilenames(parent=self, title="Choisir des images", filetypes=IMG_TYPES)
        self.ajouter_chemins(chemins)

    def sur_depot(self, chemins):
        self.ajouter_chemins(chemins, signaler=True)

    def ajouter_chemins(self, chemins, signaler=False):
        if self.occupe:
            return
        bons = [p for p in chemins if Path(p).suffix.lower() in EXT_IMAGES]
        for p in bons:
            self.files.append(dict(path=p, etat="attente", note=""))
        if signaler and chemins and len(bons) < len(chemins):
            kit.toast(self, f"{len(chemins) - len(bons)} fichier(s) ignoré(s) : ce ne sont pas des images.", "warn")
        self.res.grid_remove()
        self.render_files()

    def coller(self, silencieux=False):
        if self.occupe:
            return
        try:
            from PIL import ImageGrab
            data = ImageGrab.grabclipboard()
        except Exception:  # noqa
            data = None
        if isinstance(data, list) and data:
            self.ajouter_chemins([str(x) for x in data], signaler=True)
        elif isinstance(data, Image.Image):
            fd, p = tempfile.mkstemp(prefix="ocr_collee_", suffix=".png")
            os.close(fd)
            data.save(p)
            self.ajouter_chemins([p])
        elif not silencieux:
            kit.toast(self, "Aucune image dans le presse-papiers. Copiez d'abord une image.", "info")

    def retirer(self, i):
        del self.files[i]
        self.render_files()

    def deplacer(self, i, d):
        j = i + d
        if 0 <= j < len(self.files):
            self.files[i], self.files[j] = self.files[j], self.files[i]
            self.render_files()

    def vider(self):
        if self.files and kit.ask(self, "Tout retirer ?", "La liste d'images sera vidée (vos fichiers ne sont pas "
                                                          "supprimés).", yes="Tout retirer", no="Annuler", danger=True):
            self.files.clear()
            self.render_files()

    # ---- état des moteurs et du mode
    def maj_etat(self):
        n = sum(1 for k in PROVIDERS if str(self.cfg.get(k + "_key", "")).strip())
        if n:
            self.chip_cloud.set(f"IA en ligne : {n} fournisseur(s)", kit.OK)
        else:
            self.chip_cloud.set("IA en ligne : aucune clé", kit.NEUTRAL)
        t = tesseract_info(self.cfg)
        if t and "fra" in t[1]:
            self.chip_tess.set("Tesseract (français) prêt", kit.OK)
        elif t:
            self.chip_tess.set("Tesseract sans le français", kit.WARN)
        else:
            self.chip_tess.set("Tesseract absent", kit.ERR)
        self.chip_rapid.set("RapidOCR prêt" if rapid_engine() else "RapidOCR absent",
                            kit.OK if rapid_engine() else kit.NEUTRAL)
        self.maj_mode()

    def maj_mode(self):
        if not hasattr(self, "bn_mode"):
            return
        n = sum(1 for k in PROVIDERS if str(self.cfg.get(k + "_key", "")).strip())
        if self.v_mode.get() == MODE_LOCAL:
            self.bn_mode.set("Rien ne quitte votre ordinateur. Idéal pour les documents sensibles. "
                             "Le texte est lu sans mise en forme des tableaux.", "ok")
        elif n:
            self.bn_mode.set("L'IA lit d'abord vos images. En cas de quota, d'erreur ou sans connexion, "
                             "l'OCR local prend le relais automatiquement.", "info")
        else:
            self.bn_mode.set("Aucune clé IA : l'OCR local sera utilisé. Ajoutez une clé gratuite (Gemini) dans "
                             "les paramètres pour une meilleure précision.", "warn")

    def parametres(self):
        w = SettingsWindow(self, self.cfg)
        self.wait_window(w)
        self.maj_etat()

    # ---- traitement
    def ecrire(self, s):
        for a, b in GLYPHES.items():
            s = s.replace(a, b)
        self.log.configure(state="normal")
        self.log.insert("end", s + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def annuler(self):
        self.stop.set()
        self.lbl_etat.configure(text="Annulation en cours…", text_color=kit.WARN)

    def lancer(self):
        if not self.files:
            kit.toast(self, "Ajoutez d'abord au moins une image.", "info")
            return
        out = filedialog.asksaveasfilename(
            parent=self, defaultextension=".docx", title="Enregistrer le document Word",
            initialfile=Path(self.files[0]["path"]).stem + ".docx", filetypes=[("Document Word", "*.docx")])
        if not out:
            return
        self.cfg["mode_local_seul"] = self.v_mode.get() == MODE_LOCAL
        self.cfg["verif_croisee"] = self.v_verif.get()
        save_config(self.cfg)
        cfg, chemins = dict(self.cfg), [f["path"] for f in self.files]
        for f in self.files:
            f["etat"], f["note"] = "attente", ""
        self.sortie = out
        self.stop.clear()
        self.occupe = True
        self.btn_go.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.res.grid_remove()
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.bar.set(0)
        self.lbl_etat.configure(text="Traitement en cours…", text_color=kit.ACCENT)
        self.render_files()

        def work():
            try:
                rep = process_images(
                    chemins, out, cfg, log=lambda m: self.q.put(("log", m)),
                    step=lambda i, n: self.q.put(("step", i, n)), stop=self.stop)
                self.q.put(("done", rep))
            except Exception as e:  # noqa
                self.q.put(("error", str(e)))

        threading.Thread(target=work, daemon=True).start()
        self.after(100, self.poll)

    def poll(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                kind, *a = self.q.get_nowait()
                if kind == "log":
                    self.ecrire(a[0])
                elif kind == "step":
                    i, n = a
                    self.bar.set(i / max(1, n))
                    if i < n:
                        for k, f in enumerate(self.files):
                            if k < i and f["etat"] == "cours":
                                f["etat"] = "ok"
                            elif k == i:
                                f["etat"] = "cours"
                        self.lbl_etat.configure(text=f"Image {i + 1} sur {n}…", text_color=kit.ACCENT)
                        self.render_files()
                else:
                    self.fin(kind, a[0])
                    return
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def fin(self, kind, data):
        self.occupe = False
        self.btn_go.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        if kind == "error":
            for f in self.files:
                if f["etat"] == "cours":
                    f["etat"] = "attente"
            self.render_files()
            self.lbl_etat.configure(text="Conversion interrompue.", text_color=kit.ERR)
            kit.error(self, "OCR", data)
            return
        rep = data
        self.bar.set(1)
        for f, (nom, moteur, warns) in zip(self.files, rep["results"]):
            if moteur == "ÉCHEC":
                f["etat"], f["note"] = "echec", (warns[0] if warns else "échec")[:70]
            else:
                f["etat"] = "warn" if warns else "ok"
                f["note"] = moteur[:46] + (f"  •  {len(warns)} avertissement(s)" if warns else "")
        self.render_files()
        nb_warn = sum(1 for f in self.files if f["etat"] == "warn")
        msg = f"{rep['ok']} image(s) convertie(s)"
        if rep["fail"]:
            msg += f", {rep['fail']} échec(s)"
        if nb_warn:
            msg += f"  •  {nb_warn} à relire (voir « Détails »)"
        self.lbl_res.configure(text=msg + f"\n{Path(rep['out']).name}")
        self.res.grid()
        self.lbl_etat.configure(text="Terminé.", text_color=kit.OK if not rep["fail"] else kit.WARN)
        if rep["ok"]:
            try:
                kit.open_file(rep["out"])
            except Exception:  # noqa
                pass

    def ouvrir_word(self):
        if self.sortie:
            try:
                kit.open_file(self.sortie)
            except Exception as e:  # noqa
                kit.error(self, "OCR", f"Impossible d'ouvrir le fichier :\n{e}")

    def ouvrir_dossier(self):
        if self.sortie:
            try:
                kit.open_folder(self.sortie)
            except Exception as e:  # noqa
                kit.error(self, "OCR", f"Impossible d'ouvrir le dossier :\n{e}")


def ouvrir_fenetre_ocr(parent, fichier=None):
    return OCRWindow(parent, fichier)


if __name__ == "__main__":
    kit.setup("dark")
    root = kit.Root()
    root.withdraw()
    w = OCRWindow(root, sys.argv[1] if len(sys.argv) > 1 else None)
    w.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
