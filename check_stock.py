#!/usr/bin/env python3
"""
Surveillance de stock Play-in -> alerte WhatsApp via CallMeBot.
Lancé automatiquement par GitHub Actions (voir .github/workflows/surveillance.yml).
"""
import html
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
    },    {
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
]

# Mention affichée par Play-in quand le produit n'est pas commandable
# en ligne (ex. « Rupture temporaire en livraison »).
RUPTURE = re.compile(r"(rupture|épuisé)[^.]{0,40}?(livraison|stock)", re.IGNORECASE)

ERREURS_AVANT_ALERTE = 3  # ~15 min de pages illisibles avant de te prévenir
FICHIER_ETAT = "etat.json"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/json",
    "Accept-Language": "fr-FR,fr;q=0.9",
}


def texte_visible(page_html):
    """Garde uniquement le texte affiché (sans scripts ni balises)."""
    page_html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", page_html)
    texte = re.sub(r"(?s)<[^>]+>", " ", page_html)
    return re.sub(r"\s+", " ", html.unescape(texte))


def telecharger(url):
    for essai in range(2):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", errors="replace")
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
    m = RUPTURE.search(texte)
    if m:
        print(f"  Mention trouvée : « {m.group(0)} »")
        return "rupture"
    print("  Aucune mention de rupture trouvée")
    return "dispo"


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
        e = etat.get(p["url"], {"statut": None, "erreurs": 0, "alerte_erreur": False})

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
                messages.append(f"🟢 DISPO : {p['nom']}\nFonce : {p['url']}")
                nouveau_dispo = True
            elif statut == "rupture" and e["statut"] == "dispo":
                messages.append(f"🔴 De nouveau en rupture : {p['nom']}")
            e["statut"] = statut

        etat[p["url"]] = e
        icone = {"dispo": "🟢 dispo", "rupture": "🔴 rupture", "erreur": "⚠️ page illisible"}[statut]
        resume.append(f"{p['nom']} : {icone}")

    if test:
        messages.insert(0, "🧪 Test du bot de surveillance\n" + "\n".join(resume))

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
