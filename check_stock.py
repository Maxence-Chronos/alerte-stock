#!/usr/bin/env python3
"""
Surveillance de stock Play-in -> alerte WhatsApp via CallMeBot.
Lancé automatiquement par GitHub Actions (voir .github/workflows/surveillance.yml).
"""
import gzip
import html
import http.cookiejar
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

# ---------------------------------------------------------------
# PRODUITS À SURVEILLER : ajoute ou retire des blocs ici.
# "site" = "playin" ou "shopify" (boutiques dont l'adresse contient /products/)
# "mot_cle" = un mot du nom du produit, pour vérifier que la page
# s'est bien chargée (et pas une page de blocage).
# ---------------------------------------------------------------
PRODUITS = [
    {
        "nom": "Play-in - UPC Noctali",
        "site": "playin",
        "url": "https://www.play-in.com/fr/produit/659645/coffret-collection-ultra-premium-pokemon-30-ans-soiree-noctali-fr",
        "mot_cle": "Noctali",
    },
    {
        "nom": "Play-in - UPC Mentali",
        "site": "playin",
        "url": "https://www.play-in.com/fr/produit/659644/coffret-collection-ultra-premium-pokemon-30-ans-journee-mentali-fr",
        "mot_cle": "Mentali",
    },
    {
        "nom": "Hikaru - UPC Mentali",
        "site": "shopify",
        "url": "https://hikarudistribution.com/products/upc-mentali-collection-ultra-premium-30eme-anniversaire-francais",
        "mot_cle": "Mentali",
    },
    {
        "nom": "Hikaru - UPC Noctali",
        "site": "shopify",
        "url": "https://hikarudistribution.com/products/upc-noctali-collection-ultra-premium-30eme-anniversaire-francais",
        "mot_cle": "Noctali",
    },
    {
        "nom": "VCOLLECT - UPC Noctali",
        "site": "shopify",
        "url": "https://vcollect.fr/products/upc-noctali-30e-anniversaire-francais",
        "mot_cle": "Noctali",
    },
    {
        "nom": "VCOLLECT - UPC Mentali",
        "site": "shopify",
        "url": "https://vcollect.fr/products/upc-mentali-30e-anniversaire-francais",
        "mot_cle": "Mentali",
    },
    {
        "nom": "DestockTCG - UPC Noctali",
        "site": "destocktcg",
        "url": "https://www.destocktcg.fr/product/30e-anniversaire-coffret-ultra-premium-soiree-noctali-ex-pokemon-fr-1761",
        "mot_cle": "Noctali",
    },
    {
        "nom": "DestockTCG - UPC Mentali",
        "site": "destocktcg",
        "url": "https://www.destocktcg.fr/product/30e-anniversaire-coffret-ultra-premium-journee-mentali-ex-pokemon-fr-1760",
        "mot_cle": "Mentali",
    },
]

# Mention qui signifie « pas encore commandable », selon la boutique
REGLES = {
    # Play-in : « Rupture temporaire en livraison »
    "playin": re.compile(r"(rupture|épuisé)[^.]{0,40}?(livraison|stock)", re.IGNORECASE),
    # DestockTCG : tant que le prix n'est pas affiché, les précommandes sont fermées
    "destocktcg": re.compile(r"prix à venir", re.IGNORECASE),
}

ERREURS_AVANT_ALERTE = 3  # ~15 min de pages illisibles avant de te prévenir
FICHIER_ETAT = "etat.json"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Connection": "keep-alive",
}

# Session qui conserve les cookies, comme un vrai navigateur
SESSION = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def cle(p):
    """Identifiant unique d'un produit (une même page peut avoir 2 variantes)."""
    return p["url"] + ("#" + p["variante"] if p.get("variante") else "")


def texte_visible(page_html):
    """Garde uniquement le texte affiché (sans scripts ni balises)."""
    page_html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", page_html)
    texte = re.sub(r"(?s)<[^>]+>", " ", page_html)
    return re.sub(r"\s+", " ", html.unescape(texte))


def telecharger(url):
    for essai in range(2):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with SESSION.open(req, timeout=30) as r:
                brut = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    brut = gzip.GzipFile(fileobj=io.BytesIO(brut)).read()
            return brut.decode("utf-8", errors="replace")
        except Exception as e:
            print(f"  Essai {essai + 1} échoué : {e}")
            time.sleep(5)
    return None


def statut_shopify(p):
    """Boutiques Shopify : lit la fiche produit en JSON (champ « available »)."""
    brut = telecharger(p["url"].split("?")[0].rstrip("/") + ".js")
    if brut is None:
        return "erreur"
    try:
        data = json.loads(brut)
    except ValueError:
        print("  Réponse illisible (blocage anti-bot ?)")
        return "erreur"
    if p["mot_cle"].lower() not in str(data.get("title", "")).lower():
        print("  Produit introuvable dans la réponse")
        return "erreur"
    if p.get("variante"):
        for v in data.get("variants", []):
            if p["variante"].lower() in str(v.get("title", "")).lower():
                print(f"  variante {v.get('title')} : available = {v.get('available')}")
                return "dispo" if v.get("available") else "rupture"
        print("  Variante introuvable dans la réponse")
        return "erreur"
    print(f"  available = {data.get('available')}")
    return "dispo" if data.get("available") else "rupture"


def statut_produit(p):
    """Renvoie 'dispo', 'rupture' ou 'erreur'."""
    if p.get("site") == "shopify":
        return statut_shopify(p)
    page = telecharger(p["url"])
    if page is None:
        return "erreur"
    texte = texte_visible(page)
    if p["mot_cle"].lower() not in texte.lower():
        print("  Page chargée mais produit introuvable (blocage anti-bot ?)")
        return "erreur"
    regle = REGLES.get(p.get("site"), REGLES["playin"])
    m = regle.search(texte)
    if m:
        print(f"  Mention trouvée : « {m.group(0)} »")
        return "rupture"
    print("  Aucune mention de rupture trouvée")
    return "dispo"


def envoyer_ntfy(titre, message, lien=None):
    """Notification instantanée sur le téléphone (sujet dans le secret NTFY_TOPIC)."""
    sujet = os.environ.get("NTFY_TOPIC", "").strip()
    if not sujet:
        return
    entetes = {"Title": titre.encode("utf-8"), "Priority": "urgent", "Tags": "rotating_light"}
    if lien:
        entetes["Click"] = lien
    req = urllib.request.Request(f"https://ntfy.sh/{sujet}",
                                 data=message.encode("utf-8"), headers=entetes)
    try:
        urllib.request.urlopen(req, timeout=15)
        print("Notification ntfy envoyée.")
    except Exception as e:
        print(f"Notification ntfy ratée : {e}")


def envoyer_whatsapp(message):
    phone = os.environ.get("CALLMEBOT_PHONE", "").strip()
    apikey = os.environ.get("CALLMEBOT_APIKEY", "").strip()
    if not phone or not apikey:
        print("Secrets CALLMEBOT_PHONE / CALLMEBOT_APIKEY manquants.")
        return False
    url = "https://api.callmebot.com/whatsapp.php?" + urllib.parse.urlencode(
        {"phone": phone, "text": message, "apikey": apikey})
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            reponse = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"Échec d'envoi WhatsApp : {e}")
        return False
    if "queued" not in reponse.lower():
        print(f"Réponse inattendue de CallMeBot : {reponse[:300]}")
        return False
    print("Message WhatsApp envoyé.")
    return True


def main():
    test = os.environ.get("TEST") == "1"
    try:
        with open(FICHIER_ETAT, encoding="utf-8") as f:
            etat = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        etat = {}

    messages, resume, nouveau_dispo = [], [], False

    for p in PRODUITS:
        print(f"Vérification : {p['nom']}")
        statut = statut_produit(p)
        print(f"  -> {statut}")
        e = etat.get(cle(p), {"statut": None, "erreurs": 0, "alerte_erreur": False})

        if statut == "erreur":
            e["erreurs"] += 1
            if e["erreurs"] >= ERREURS_AVANT_ALERTE and not e["alerte_erreur"]:
                messages.append(f"⚠️ Le bot n'arrive plus à lire la page de {p['nom']} "
                                f"(blocage possible). Il continue d'essayer.\n{p['url']}")
                e["alerte_erreur"] = True
        else:
            if e["alerte_erreur"]:
                messages.append(f"✅ Le bot relit à nouveau la page de {p['nom']}.")
            e["erreurs"], e["alerte_erreur"] = 0, False
            if statut == "dispo" and e["statut"] != "dispo":
                envoyer_ntfy(f"DISPO : {p['nom']}", "Fonce commander !", p["url"])
                messages.append(f"🟢 DISPO : {p['nom']}\nFonce : {p['url']}")
                nouveau_dispo = True
            elif statut == "rupture" and e["statut"] == "dispo":
                messages.append(f"🔴 De nouveau en rupture : {p['nom']}")
            e["statut"] = statut

        etat[cle(p)] = e
        icone = {"dispo": "🟢 dispo", "rupture": "🔴 rupture", "erreur": "⚠️ page illisible"}[statut]
        resume.append(f"{p['nom']} : {icone}")

    if test:
        messages.insert(0, "🧪 Test du bot de surveillance\n" + "\n".join(resume))
        envoyer_ntfy("Test du bot GitHub", "\n".join(resume))

    with open(FICHIER_ETAT, "w", encoding="utf-8") as f:
        json.dump(etat, f, ensure_ascii=False, indent=2)

    envoi_ok = envoyer_whatsapp("\n\n".join(messages)) if messages else True

    # Un « échec » volontaire déclenche l'email automatique de GitHub :
    # double alerte quand un produit devient dispo, et filet de sécurité
    # si le message WhatsApp ne passe pas.
    if nouveau_dispo or not envoi_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
