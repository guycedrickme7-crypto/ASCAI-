#!/usr/bin/env python3
"""
Met à jour prix-officiels.json à partir de la publication hebdomadaire de l'OCPV
(Office d'aide à la Commercialisation des Produits Vivriers, Ministère du Commerce).

- Cherche sur https://www.ocpv-ci.com/ le lien du dernier PDF « Prix à la consommation ».
- Lit le tableau du District d'Abidjan (prix mini / maxi / moyen, en FCFA par kg).
- N'écrit le fichier que si le PDF a changé et que la lecture est cohérente.
  En cas de doute, l'ancien fichier est conservé (code de sortie 1, rien n'est publié).
"""
import hashlib, io, json, os, re, sys, unicodedata, urllib.parse, urllib.request
from datetime import datetime, timezone

import pdfplumber

ACCUEIL = "https://www.ocpv-ci.com/"
SORTIE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "prix-officiels.json")
MARCHE = "District d'Abidjan"
MOIS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
        "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12}
NOMS_MOIS = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
             "septembre", "octobre", "novembre", "décembre"]

# (motif sur le libellé normalisé, nom retenu dans prix-officiels.json)
PRODUITS = [
    (r"^mais", "Maïs grain"), (r"^mil$", "Mil"), (r"^riz importe", "Riz importé"),
    (r"^riz local", "Riz local blanchi"), (r"^banane plantain", "Banane plantain"),
    (r"^igname assawa", "Igname assawa"), (r"^igname kponan", "Igname kponan"),
    (r"^manioc", "Manioc doux"), (r"^pomme de terre", "Pomme de terre"),
    (r"^aubergine n.?drowa", "Aubergine N'drowa"), (r"^aubergine violette", "Aubergine violette"),
    (r"^carotte", "Carotte"), (r"^chou", "Chou vert"), (r"^gombo", "Gombo"),
    (r"^oignon.*(violet|galmi)", "Oignon violet de Galmi"), (r"^oignon.*hollande", "Oignon Hollande"),
    (r"^piment", "Piment frais"), (r"^tomate", "Tomate SODEFEL"),
    (r"^arachide", "Arachide décortiquée"), (r"^graine de palme", "Graine de palme"),
]
SECTIONS = {"cereales", "feculents", "legumes", "oleagineux", "speculations"}


def log(*a):
    print(*a, file=sys.stderr)


def normaliser(t):
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[\"'’‘“”`´]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def telecharger(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ASCAI prix bot)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def trouver_pdf():
    html = telecharger(ACCUEIL).decode("utf-8", "ignore")
    liens = re.findall(r'href=["\']([^"\']*prix_consomation[^"\']*\.pdf)["\']', html, re.I)
    if not liens:
        raise RuntimeError("Lien du PDF « prix à la consommation » introuvable sur la page d'accueil de l'OCPV.")
    return urllib.parse.urljoin(ACCUEIL, liens[0].replace(" ", "%20"))


def lire_semaine(texte, url):
    t = normaliser(texte)
    m = re.search(r"du (\d{1,2})(?:er)? ?([a-z]+)? au (\d{1,2})(?:er)? ([a-z]+) (\d{4})", t)
    if m and m.group(4) in MOIS and (not m.group(2) or m.group(2) in MOIS):
        j1, m1, j2, m2, an = int(m.group(1)), m.group(2), int(m.group(3)), m.group(4), int(m.group(5))
        mois2 = MOIS[m2]; mois1 = MOIS[m1] if m1 else mois2
    else:
        f = re.search(r"(\d{2})_(\d{2})_AU_+(\d{2})_(\d{2})_(\d{4})", url, re.I)
        if not f:
            raise RuntimeError("Semaine du relevé introuvable.")
        j1, mois1, j2, mois2, an = int(f[1]), int(f[2]), int(f[3]), int(f[4]), int(f[5])
    an1 = an - 1 if mois1 > mois2 else an
    d1, d2 = datetime(an1, mois1, j1), datetime(an, mois2, j2)
    if not 5 <= (d2 - d1).days <= 8:
        raise RuntimeError(f"Période incohérente : {d1:%d/%m/%Y} au {d2:%d/%m/%Y}")
    jour = lambda d: "1er" if d.day == 1 else str(d.day)
    label = f"{jour(d1)} {NOMS_MOIS[d1.month]} au {jour(d2)} {NOMS_MOIS[d2.month]} {d2.year}" if d1.month != d2.month \
        else f"{jour(d1)} au {jour(d2)} {NOMS_MOIS[d2.month]} {d2.year}"
    return d1.date().isoformat(), d2.date().isoformat(), label


def lire_abidjan(pdf_octets):
    """Retourne ({produit: [mini, moyen, maxi]}, texte_page) pour le District d'Abidjan."""
    with pdfplumber.open(io.BytesIO(pdf_octets)) as pdf:
        for page in pdf.pages:
            texte = page.extract_text() or ""
            if "ABIDJAN" not in texte.upper():
                continue
            mots = page.extract_words(keep_blank_chars=False, use_text_flow=False)
            premier = [m for m in mots if normaliser(m["text"]).startswith(("cereales", "mais"))]
            if not premier:
                continue
            haut_donnees = min(m["top"] for m in premier) - 1
            # colonnes : mots « mini / maxi / moy » de l'en-tête, de gauche à droite
            entetes = sorted([m for m in mots if m["top"] < haut_donnees and normaliser(m["text"]) in ("mini", "maxi", "moy")],
                             key=lambda m: m["x0"])
            if len(entetes) != 18:
                raise RuntimeError(f"En-têtes de colonnes inattendus ({len(entetes)} au lieu de 18).")
            centres = [(m["x0"] + m["x1"]) / 2 for m in entetes]
            ordre = [normaliser(m["text"]) for m in entetes]
            if ordre[3:6] != ["mini", "maxi", "moy"]:
                raise RuntimeError("Colonnes du District d'Abidjan non reconnues.")
            demi = (centres[1] - centres[0]) / 2
            limite_gauche = centres[0] - demi * 1.05
            lignes, cour = [], None
            for m in sorted([m for m in mots if m["top"] >= haut_donnees], key=lambda m: (m["top"], m["x0"])):
                if cour is None or abs(m["top"] - cour["top"]) > 3:
                    cour = {"top": m["top"], "mots": []}; lignes.append(cour)
                cour["mots"].append(m)
            resultat, attente = {}, ""
            for l in lignes:
                libelle = " ".join(m["text"] for m in sorted(l["mots"], key=lambda m: m["x0"])
                                   if (m["x0"] + m["x1"]) / 2 < limite_gauche)
                nombres = {}
                for m in l["mots"]:
                    c = (m["x0"] + m["x1"]) / 2
                    if c >= limite_gauche and re.fullmatch(r"\d{2,5}", m["text"]):
                        i = min(range(18), key=lambda k: abs(centres[k] - c))
                        if abs(centres[i] - c) <= demi * 1.1:
                            nombres[i] = int(m["text"])
                n = normaliser(libelle)
                if not nombres:
                    if n and n not in SECTIONS and not n.startswith(("unite", "synthese", "prix")):
                        attente = n
                    continue
                if not n:
                    n = attente
                elif attente and not any(re.search(p, n) for p, _ in PRODUITS):
                    n = f"{attente} {n}"
                attente = ""
                nom = next((nm for p, nm in PRODUITS if re.search(p, n)), None)
                if not nom or nom in resultat:
                    continue
                if all(i in nombres for i in (3, 4, 5)):
                    mini, maxi, moy = nombres[3], nombres[4], nombres[5]
                    if 10 <= mini <= moy <= maxi <= 20000:
                        resultat[nom] = [mini, moy, maxi]
                    else:
                        log(f"Valeurs incohérentes ignorées pour {nom} : {mini}/{moy}/{maxi}")
            return resultat, texte
    raise RuntimeError("Tableau du District d'Abidjan introuvable dans le PDF.")


def main():
    ancien = {}
    if os.path.exists(SORTIE):
        try:
            ancien = json.load(open(SORTIE, encoding="utf-8"))
        except Exception:
            ancien = {}
    url = trouver_pdf()
    pdf = telecharger(url)
    empreinte = hashlib.sha256(pdf).hexdigest()
    if ancien.get("sha256") == empreinte:
        log("Aucun changement : même document OCPV.")
        return 0
    prix, texte = lire_abidjan(pdf)
    if len(prix) < 8:
        raise RuntimeError(f"Lecture trop partielle ({len(prix)} produits) : mise à jour annulée.")
    debut, fin, label = lire_semaine(texte, url)
    if ancien.get("fin") and fin < ancien["fin"]:
        raise RuntimeError(f"Le document ({fin}) est plus ancien que celui déjà publié ({ancien['fin']}).")
    data = {
        "source": "OCPV – Ministère du Commerce, de l'Industrie et de l'Artisanat",
        "marche": MARCHE, "unite": "FCFA/kg (prix à la consommation)", "ordre": ["mini", "moyen", "maxi"],
        "semaine": label, "debut": debut, "fin": fin, "url": url, "sha256": empreinte,
        "maj": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "prix": prix,
    }
    with open(SORTIE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2); f.write("\n")
    log(f"Prix OCPV mis à jour : semaine du {label}, {len(prix)} produits.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("ÉCHEC :", e)
        sys.exit(1)
