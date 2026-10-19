#!/usr/bin/env python3
"""
bg_remove.py - détection du sujet (masque) et remplacement du fond d'une photo.

Trois méthodes, avec repli automatique :
  "auto"    : IA locale rembg (modèle téléchargé 1 fois, ensuite 100 % hors ligne)
              -> si indisponible (pas installé / pas de modèle / erreur) : GrabCut
  "grabcut" : OpenCV GrabCut, sans IA, hors ligne
  "uni"     : fond uni (mur, drap) détecté par couleur, très rapide

Le masque est une image 'L' (255 = sujet, 0 = fond), à la taille de l'image fournie.
"""

from PIL import Image, ImageFilter

REMBG_MODELS = ["u2net_human_seg", "isnet-general-use", "u2net"]
_sessions = {}
WORK_SIDE = 1400  # résolution de travail max (le masque est ensuite remis à l'échelle)

COULEURS_FOND = {  # couleurs usuelles pour photos d'identité / documents
    "Blanc": "#FFFFFF",
    "Gris clair": "#E6E6E6",
    "Bleu ciel": "#BFD7EE",
    "Bleu": "#3E7CC2",
    "Rouge": "#C0392B",
    "Vert": "#4C9F70",
}


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def rgb_hex(c):
    return "#%02X%02X%02X" % tuple(int(x) for x in c[:3])


def disponibilites():
    """-> dict(rembg=bool, opencv=bool) : ce qui est installé."""
    d = {}
    for nom, mod in (("rembg", "rembg"), ("opencv", "cv2")):
        try:
            __import__(mod)
            d[nom] = True
        except Exception:
            d[nom] = False
    return d


def _reduire(img):
    """Image réduite pour le calcul (le masque sera agrandi après)."""
    if max(img.size) <= WORK_SIDE:
        return img.convert("RGB")
    s = WORK_SIDE / max(img.size)
    return img.convert("RGB").resize(
        (max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS
    )


# ---------------------------------------------------------------- rembg (IA locale)
def _masque_rembg(img, log):
    from rembg import new_session, remove

    derniere = None
    for nom in REMBG_MODELS:
        try:
            if nom not in _sessions:
                log(f"Chargement du modèle {nom} (téléchargement unique si absent)...")
                _sessions[nom] = new_session(nom)
            m = remove(img, session=_sessions[nom], only_mask=True)
            return m.convert("L"), f"IA rembg ({nom})"
        except Exception as e:  # modèle absent hors ligne, mémoire, etc.
            derniere = e
            _sessions.pop(nom, None)
            log(f"  modèle {nom} indisponible : {str(e)[:90]}")
    raise RuntimeError(f"rembg inutilisable : {derniere}")


# ---------------------------------------------------------------- GrabCut (sans IA)
def _masque_grabcut(img, log):
    import numpy as np
    import cv2

    h_img = 640
    s = min(1.0, h_img / max(img.size))
    small = img.resize(
        (max(8, int(img.width * s)), max(8, int(img.height * s))), Image.LANCZOS
    )
    a = cv2.cvtColor(np.array(small), cv2.COLOR_RGB2BGR)
    h, w = a.shape[:2]
    m = np.full((h, w), cv2.GC_PR_BGD, np.uint8)
    b = max(2, int(0.03 * min(h, w)))
    m[:b, :] = m[-b:, :] = m[:, :b] = m[:, -b:] = cv2.GC_BGD
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = w / 2, h * 0.52
    e1 = ((xx - cx) / (w * 0.40)) ** 2 + (
        (yy - cy) / (h * 0.46)
    ) ** 2 < 1  # probablement sujet
    e2 = ((xx - cx) / (w * 0.18)) ** 2 + (
        (yy - cy * 0.85) / (h * 0.26)
    ) ** 2 < 1  # sûrement sujet
    m[e1] = cv2.GC_PR_FGD
    m[e2] = cv2.GC_FGD
    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)
    cv2.grabCut(a, m, None, bgd, fgd, 6, cv2.GC_INIT_WITH_MASK)
    res = np.where((m == cv2.GC_FGD) | (m == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(
        res
    )  # garde la plus grande zone
    if n > 1:
        k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        res = np.where(lab == k, 255, 0).astype(np.uint8)
    res = cv2.morphologyEx(res, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    return Image.fromarray(res).resize(img.size, Image.LANCZOS), "GrabCut (sans IA)"


# ---------------------------------------------------------------- fond uni
def masque_fond_uni(img, tolerance=30):
    """Fond détecté par la couleur des bords, relié au bord de l'image."""
    import numpy as np
    import cv2

    s = min(1.0, 700 / max(img.size))
    small = img.convert("RGB").resize(
        (max(8, int(img.width * s)), max(8, int(img.height * s))), Image.LANCZOS
    )
    lab = cv2.cvtColor(np.array(small), cv2.COLOR_RGB2LAB).astype(np.float32)
    h, w = lab.shape[:2]
    b = max(3, int(0.04 * min(h, w)))
    ring = np.concatenate(
        [
            lab[:b].reshape(-1, 3),
            lab[-b:].reshape(-1, 3),
            lab[:, :b].reshape(-1, 3),
            lab[:, -b:].reshape(-1, 3),
        ]
    )
    ref = np.median(ring, axis=0)
    dist = np.linalg.norm(lab - ref, axis=2)
    cand = (dist < float(tolerance)).astype(np.uint8)
    n, labels = cv2.connectedComponents(cand, connectivity=4)
    bord = np.unique(
        np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])
    )
    bg = np.isin(labels, [x for x in bord if x != 0]) & (cand == 1)
    res = np.where(bg, 0, 255).astype(np.uint8)
    res = cv2.morphologyEx(res, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    res = cv2.GaussianBlur(res, (0, 0), 1.2)
    return Image.fromarray(res).resize(img.size, Image.LANCZOS)


# ---------------------------------------------------------------- API
def calculer_masque(img, methode="auto", tolerance=30, log=print):
    """-> (masque 'L' à la taille de img, description du moteur utilisé)."""
    work = _reduire(img)
    try:
        if methode == "uni":
            m, nom = masque_fond_uni(work, tolerance), "Fond uni (couleur)"
        elif methode == "grabcut":
            m, nom = _masque_grabcut(work, log)
        else:
            try:
                m, nom = _masque_rembg(work, log)
            except Exception as e:
                log(f"IA indisponible ({str(e)[:100]}) -> repli sur GrabCut")
                m, nom = _masque_grabcut(work, log)
                nom += " [repli]"
    except ImportError as e:
        raise RuntimeError(
            "Module manquant : "
            + str(e)
            + "\nInstallez : pip install opencv-python numpy rembg onnxruntime"
        )
    if m.size != img.size:
        m = m.resize(img.size, Image.LANCZOS)
    return m, nom


def composer_fond(photo, masque, couleur, adoucir=0.0, contracter=0.0):
    """Remplace le fond de `photo` (RGB) par `couleur` (r,g,b) grâce au masque.
    adoucir : flou des bords ; contracter : >0 rogne le sujet (supprime les liserés), <0 l'élargit.
    Les rayons sont proportionnels à la taille de la photo (aperçu = export)."""
    k = max(photo.size) / 1000.0
    m = (
        masque
        if masque.size == photo.size
        else masque.resize(photo.size, Image.LANCZOS)
    )
    n = int(round(abs(contracter) * k))
    if n:
        f = ImageFilter.MinFilter if contracter > 0 else ImageFilter.MaxFilter
        m = m.filter(f(2 * n + 1))
    if adoucir > 0:
        m = m.filter(ImageFilter.GaussianBlur(adoucir * k * 0.5))
    fond = Image.new("RGB", photo.size, tuple(couleur))
    return Image.composite(photo.convert("RGB"), fond, m)
