#!/usr/bin/env python3
"""
veille_ia_updater.py
────────────────────
Script de mise à jour automatique de la veille IA.

Fonctions :
  1. Scrape les sites des créateurs de contenu IA suivis (Gabzer, Superproductif, Mathieu Ibanez)
  2. Interroge l'API Anthropic (Claude) pour synthétiser les nouveautés de la semaine
  3. Met à jour le fichier news.json avec les nouvelles informations par outil
  4. Envoie un e-mail récapitulatif hebdomadaire au(x) destinataire(s)

Sources de veille intégrées :
  - Gabzer (Gabriel Sagnet) → gabzer.fr  | 1M+ abonnés, astuces IA & tech
  - Superproductif (Jérémy Guillo & Brice Trophardy) → newsletter.superproductif.fr
  - Mathieu Ibanez → web.mathieuibanez.com | expert IA créative (Midjourney, vidéo)
  + Veille Claude IA sur les outils référencés dans le tableau

Installation :
  pip install anthropic requests beautifulsoup4

Configuration :
  Remplis les variables dans la section CONFIG ci-dessous,
  ou exporte-les comme variables d'environnement.

Cron (exemple — tous les lundis à 8h00) :
  0 8 * * 1 /usr/bin/python3 /chemin/vers/veille_ia_updater.py >> /var/log/veille_ia.log 2>&1
"""

import os
import json
import smtplib
import logging
import time
import re
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ── Install dependencies on first run ───────────────────────────────────────
for pkg in [("anthropic", "anthropic"), ("requests", "requests"), ("bs4", "beautifulsoup4")]:
    try:
        __import__(pkg[0])
    except ImportError:
        import subprocess, sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg[1]])

import anthropic
import requests
from bs4 import BeautifulSoup


# ════════════════════════════════════════════════════════════════════════════
#  CONFIG — à personnaliser
# ════════════════════════════════════════════════════════════════════════════
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "sk-ant-VOTRE_CLE_ICI")

# Chemin vers le fichier news.json (même dossier que index.html)
NEWS_JSON_PATH = Path(__file__).parent / "news.json"

# ── Configuration e-mail ─────────────────────────────────────────────────────
SMTP_HOST     = os.getenv("SMTP_HOST",     "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER",     "votre.email@gmail.com")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "votre_mot_de_passe_application")

# Expéditeur et destinataires
EMAIL_FROM    = os.getenv("EMAIL_FROM", "Veille IA Pédagogique <votre.email@gmail.com>")
EMAIL_TO      = os.getenv("EMAIL_TO",   "vous@exemple.fr,collegue@exemple.fr").split(",")
EMAIL_SUBJECT = "🗞 Veille IA — Nouveautés de la semaine"

# ── Liste des outils à surveiller ───────────────────────────────────────────
TOOLS_TO_WATCH = [
    "ChatGPT", "Claude", "Gemini", "Le Chat (Mistral)",
    "Midjourney", "DALL·E / GPT Image", "Adobe Firefly", "Canva AI", "Ideogram AI",
    "Suno AI", "Udio", "ElevenLabs", "Soundraw",
    "Runway Gen-4.5", "Sora (OpenAI)", "HeyGen", "Veo 3 (Google)", "Descript",
    "Notion AI", "Grammarly", "Copy.ai",
    "GitHub Copilot", "Cursor", "Replit AI", "Lovable",
    "Gamma", "Beautiful.ai", "Napkin AI",
    "Perplexity AI", "NotebookLM", "Consensus",
    "D-ID", "Murf AI",
    "Framer AI",
]

# Nombre maximum d'actualités conservées par outil dans le JSON
MAX_NEWS_PER_TOOL = 3

# ── Sources créateurs à surveiller ──────────────────────────────────────────
# Pour chaque créateur : nom affiché, URL à scraper, sélecteurs CSS des titres/articles
CREATOR_SOURCES = [
    {
        "name":    "Gabzer (Gabriel Sagnet)",
        "handle":  "gabzer.mp4",
        "url":     "https://gabzer.fr/",
        "selectors": ["h2", "h3", ".article-title", ".post-title", "article h2", ".entry-title"],
        "description": "Vidéaste et créateur de contenu — astuces IA & tech, 1M+ abonnés",
    },
    {
        "name":    "Superproductif (Jérémy Guillo & Brice Trophardy)",
        "handle":  "superproductif",
        "url":     "https://newsletter.superproductif.fr/",
        "selectors": ["h2", "h3", ".post-title", "article h2", ".entry-title", "a[href*='newsletter']"],
        "description": "Newsletter IA hebdomadaire — astuces ChatGPT & productivité, 45k abonnés",
    },
    {
        "name":    "Mathieu Ibanez",
        "handle":  "mathieuibanez",
        "url":     "https://web.mathieuibanez.com/",
        "selectors": ["h2", "h3", ".article-title", ".post-title", "article h2", ".blog-title"],
        "description": "Expert IA créative — Midjourney, génération vidéo, bibliothèque Ultima",
    },
]

# Délai entre les requêtes scraping (secondes) — pour éviter d'être bloqué
SCRAPE_DELAY = 2

# ════════════════════════════════════════════════════════════════════════════
#  LOGGING
# ════════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
#  1. SCRAPER LES SITES DES CRÉATEURS
# ════════════════════════════════════════════════════════════════════════════

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}


def scrape_creator(source: dict) -> list[str]:
    """
    Scrape la page d'un créateur et retourne une liste de titres/extraits récents.
    Retourne [] en cas d'échec.
    """
    try:
        log.info(f"Scraping : {source['name']} ({source['url']})")
        resp = requests.get(source["url"], headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        titles = []
        seen = set()

        for selector in source["selectors"]:
            for el in soup.select(selector):
                text = el.get_text(strip=True)
                # Filtre : titres entre 15 et 200 caractères, sans doublons
                if 15 < len(text) < 200 and text not in seen:
                    # Exclure les éléments de navigation courants
                    skip_words = ["menu", "navigation", "accueil", "home", "contact",
                                  "à propos", "about", "login", "connexion", "s'inscrire"]
                    if not any(w in text.lower() for w in skip_words):
                        titles.append(text)
                        seen.add(text)
                if len(titles) >= 10:
                    break
            if len(titles) >= 10:
                break

        log.info(f"  → {len(titles)} titres trouvés sur {source['name']}")
        return titles[:10]

    except requests.exceptions.HTTPError as e:
        log.warning(f"  → HTTP {e.response.status_code} sur {source['url']}")
        return []
    except Exception as e:
        log.warning(f"  → Erreur scraping {source['name']} : {e}")
        return []


def scrape_all_creators() -> dict:
    """
    Scrape tous les créateurs configurés.
    Retourne { "nom_créateur": ["titre1", "titre2", ...] }
    """
    results = {}
    for source in CREATOR_SOURCES:
        titles = scrape_creator(source)
        results[source["name"]] = {
            "handle": source["handle"],
            "description": source["description"],
            "url": source["url"],
            "titles": titles,
        }
        time.sleep(SCRAPE_DELAY)
    return results


# ════════════════════════════════════════════════════════════════════════════
#  2. RÉCUPÉRER LES NOUVEAUTÉS VIA CLAUDE
# ════════════════════════════════════════════════════════════════════════════
def fetch_news_from_claude(tools: list[str], creator_data: dict = None) -> dict:
    """
    Interroge Claude pour obtenir les dernières nouveautés sur chaque outil.
    Retourne un dict { "NomOutil": [{"date": "...", "text": "..."}] }
    """
    today = datetime.now().strftime("%d %B %Y")
    tool_list = "\n".join(f"- {t}" for t in tools)

    # Construire le bloc "sources créateurs" si disponible
    creator_block = ""
    if creator_data:
        creator_block = "\n\n## Contenus récents scrapés sur les sites des créateurs suivis\n"
        creator_block += "Ces titres/extraits ont été collectés automatiquement cette semaine :\n\n"
        for creator_name, data in creator_data.items():
            creator_block += f"### {creator_name} ({data['url']})\n"
            creator_block += f"*{data['description']}*\n"
            if data["titles"]:
                for t in data["titles"]:
                    creator_block += f"- {t}\n"
            else:
                creator_block += "- (Aucun titre récupéré — site peut-être non accessible)\n"
            creator_block += "\n"

    prompt = f"""Tu es un expert en veille technologique sur l'intelligence artificielle.

Aujourd'hui nous sommes le {today}.

Voici la liste des outils IA que nous suivons pour un tableau de bord pédagogique destiné
à des étudiants en Licence/Bachelor :

{tool_list}
{creator_block}
## Ta mission

Pour CHAQUE outil de la liste, trouve s'il y a eu une nouveauté, annonce,
mise à jour ou évolution importante AU COURS DES 7 DERNIERS JOURS.

**IMPORTANT — Utilise les contenus scrapés ci-dessus :**
Si un titre/extrait du contenu scraché mentionne un outil de la liste (ex : "Canva lance...",
"ChatGPT peut maintenant...", "Midjourney v8..."), utilise cette information comme actualité
pour l'outil concerné. Cite la source entre parenthèses ex: (via Gabzer / via Superproductif).

Règles importantes :
- Si tu n'as pas d'information récente fiable sur un outil, retourne une liste vide [].
- Sois factuel et concis (2-3 phrases max par actualité).
- Le texte doit être en français, accessible à des étudiants.
- Inclure uniquement les vrais changements récents (nouvelles fonctionnalités, modèles,
  changements de prix, partenariats majeurs, lancements).
- Si l'info vient d'un créateur scraché, indique-le clairement avec "(via NomCréateur)".

Réponds UNIQUEMENT avec un objet JSON valide (sans markdown, sans code fence), au format :
{{
  "ChatGPT": [
    {{"date": "15 mars 2026", "text": "Description de la nouveauté... (via Superproductif)"}}
  ],
  "Midjourney": [],
  "Suno AI": [
    {{"date": "12 mars 2026", "text": "..."}}
  ]
}}

Inclure TOUS les outils de la liste dans le JSON, même ceux sans actualité (liste vide []).
"""

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    log.info("Interrogation de Claude pour les nouveautés IA…")

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()

    # Nettoyage au cas où Claude ajoute des backticks malgré l'instruction
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    try:
        result = json.loads(raw)
        log.info(f"Réponse Claude parsée — {len(result)} outils traités.")
        return result
    except json.JSONDecodeError as e:
        log.error(f"Erreur de parsing JSON : {e}\nRéponse brute : {raw[:500]}")
        return {}


# ════════════════════════════════════════════════════════════════════════════
#  3. METTRE À JOUR news.json
# ════════════════════════════════════════════════════════════════════════════
def update_news_json(new_news: dict) -> dict:
    """
    Charge le news.json existant, fusionne les nouvelles actualités
    (dédupliquées) et sauvegarde.
    Retourne le diff (nouvelles entrées uniquement) pour l'e-mail.
    """
    today_str = datetime.now().strftime("%d %B %Y")

    # Charger l'existant
    if NEWS_JSON_PATH.exists():
        with open(NEWS_JSON_PATH, encoding="utf-8") as f:
            existing = json.load(f)
    else:
        existing = {"lastUpdate": today_str, "news": {}}

    old_news = existing.get("news", {})
    diff = {}   # ce qui est vraiment nouveau

    for tool, items in new_news.items():
        if not items:
            continue

        prev_texts = {n["text"] for n in old_news.get(tool, [])}
        truly_new  = [n for n in items if n["text"] not in prev_texts]

        if truly_new:
            diff[tool] = truly_new
            combined = truly_new + old_news.get(tool, [])
            old_news[tool] = combined[:MAX_NEWS_PER_TOOL]

    existing["news"]       = old_news
    existing["lastUpdate"] = today_str

    with open(NEWS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    log.info(f"news.json mis à jour — {len(diff)} outil(s) avec de vraies nouveautés.")
    return diff


# ════════════════════════════════════════════════════════════════════════════
#  4. ENVOYER L'E-MAIL RÉCAPITULATIF
# ════════════════════════════════════════════════════════════════════════════
def build_email_html(diff: dict, creator_data: dict = None) -> str:
    today_str = datetime.now().strftime("%d %B %Y")

    if not diff:
        body_content = """
        <p style="color:#555; font-size:15px; line-height:1.7;">
          Aucune nouveauté significative détectée cette semaine pour les outils référencés
          dans votre tableau de bord. Le tableau reste à jour avec les informations précédentes.
        </p>
        """
    else:
        cards = ""
        for tool, items in diff.items():
            for item in items:
                cards += f"""
                <div style="background:#fff; border:1px solid #e0e0d8; border-left:4px solid #1a4fad;
                            border-radius:4px; padding:14px 16px; margin-bottom:12px;">
                  <div style="font-size:11px; color:#1a4fad; font-weight:700; text-transform:uppercase;
                              letter-spacing:0.08em; margin-bottom:6px;">{tool} &nbsp;·&nbsp; {item.get('date','')}</div>
                  <div style="font-size:14px; color:#1a1a18; line-height:1.6;">{item['text']}</div>
                </div>
                """
        body_content = cards

    # Section "sources créateurs" dans l'e-mail
    creator_section = ""
    if creator_data:
        accessible = {k: v for k, v in creator_data.items() if v["titles"]}
        blocked    = {k: v for k, v in creator_data.items() if not v["titles"]}
        if accessible or blocked:
            rows = ""
            for name, data in accessible.items():
                rows += f"""
                <tr>
                  <td style="padding:8px 0;border-bottom:1px solid #e8e8e0;vertical-align:top;">
                    <div style="font-weight:600;font-size:13px;color:#1a1a18;">
                      {name}
                      <a href="{data['url']}" style="font-weight:400;font-size:11px;color:#1a4fad;margin-left:6px;">
                        {data['url']}
                      </a>
                    </div>
                    <div style="font-size:11px;color:#888;margin-top:2px;">{data['description']}</div>
                    <div style="font-size:11px;color:#22a060;margin-top:3px;">
                      ✓ {len(data['titles'])} titre(s) récupéré(s) · utilisés pour enrichir les actualités ci-dessus
                    </div>
                  </td>
                </tr>"""
            for name, data in blocked.items():
                rows += f"""
                <tr>
                  <td style="padding:8px 0;border-bottom:1px solid #e8e8e0;vertical-align:top;">
                    <div style="font-weight:600;font-size:13px;color:#888;">
                      {name}
                      <a href="{data['url']}" style="font-weight:400;font-size:11px;color:#aaa;margin-left:6px;">
                        {data['url']}
                      </a>
                    </div>
                    <div style="font-size:11px;color:#aaa;margin-top:2px;">{data['description']}</div>
                    <div style="font-size:11px;color:#c0392b;margin-top:3px;">
                      ✗ Site non accessible cette semaine (bloqué ou hors ligne)
                    </div>
                  </td>
                </tr>"""

            creator_section = f"""
        <tr>
          <td style="padding:0 32px 24px;">
            <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;
                        color:#888;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #e8e8e0;">
              Sources créateurs surveillées cette semaine
            </div>
            <table width="100%" cellpadding="0" cellspacing="0">{rows}</table>
          </td>
        </tr>"""
<html lang="fr">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f4f4f0;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f0;padding:40px 20px;">
    <tr><td>
      <table width="640" align="center" cellpadding="0" cellspacing="0"
             style="max-width:640px;background:#ffffff;border:1px solid #e0e0d8;border-radius:4px;overflow:hidden;">

        <!-- Header -->
        <tr>
          <td style="background:#1a1a18;padding:28px 32px;">
            <div style="font-size:13px;color:#8a8a80;letter-spacing:0.1em;text-transform:uppercase;margin-bottom:6px;">
              Veille IA Pédagogique
            </div>
            <div style="font-size:22px;color:#ffffff;font-weight:700;letter-spacing:-0.01em;">
              Nouveautés de la semaine
            </div>
            <div style="font-size:12px;color:#6a6a60;margin-top:6px;">{today_str}</div>
          </td>
        </tr>

        <!-- Intro -->
        <tr>
          <td style="padding:24px 32px 8px;">
            <p style="font-size:14px;color:#555;line-height:1.7;margin:0;">
              Voici le récapitulatif automatique des nouveautés IA détectées cette semaine
              pour les outils référencés dans votre tableau de bord pédagogique.
              {f"<strong>{len(diff)} outil(s)</strong> ont des mises à jour." if diff else ""}
            </p>
          </td>
        </tr>

        <!-- Actualités -->
        <tr>
          <td style="padding:16px 32px 24px;">
            {body_content}
          </td>
        </tr>

        <!-- CTA -->
        {creator_section}
        <tr>
          <td style="padding:0 32px 32px;">
            <div style="background:#eef2fc;border:1px solid #c0d0f0;border-radius:4px;padding:16px;text-align:center;">
              <div style="font-size:13px;color:#1a4fad;margin-bottom:10px;">
                Consultez le tableau de bord mis à jour en ligne
              </div>
              <a href="https://VOTRE_URL_ICI" style="display:inline-block;background:#1a1a18;color:#fff;
                 font-size:13px;font-weight:600;padding:10px 20px;border-radius:4px;text-decoration:none;">
                Ouvrir le tableau →
              </a>
            </div>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#f4f4f0;padding:16px 32px;border-top:1px solid #e0e0d8;">
            <p style="font-size:11px;color:#9a9a90;margin:0;line-height:1.6;">
              Ce mail est envoyé automatiquement chaque lundi matin par le script veille_ia_updater.py.<br>
              Sources : Claude AI (Anthropic) · Mise à jour hebdomadaire automatisée.
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def send_email(diff: dict, creator_data: dict = None):
    """Envoie l'e-mail récapitulatif via SMTP."""
    html_body = build_email_html(diff, creator_data)
    count = sum(len(v) for v in diff.values())
    subject = f"{EMAIL_SUBJECT} — {count} nouveauté(s)" if diff else f"{EMAIL_SUBJECT} — Aucune nouveauté"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_FROM
    msg["To"]      = ", ".join(EMAIL_TO)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, EMAIL_TO, msg.as_string())
        log.info(f"E-mail envoyé à : {', '.join(EMAIL_TO)}")
    except Exception as e:
        log.error(f"Erreur lors de l'envoi de l'e-mail : {e}")


# ════════════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    log.info("═" * 60)
    log.info("Démarrage de la mise à jour de la veille IA")
    log.info("═" * 60)

    # 1. Scraper les sites des créateurs suivis
    log.info("\n── Étape 1 : Scraping des créateurs ──")
    creator_data = scrape_all_creators()
    accessible = sum(1 for v in creator_data.values() if v["titles"])
    log.info(f"{accessible}/{len(creator_data)} créateurs scrapés avec succès.")

    # 2. Récupérer les nouveautés via Claude (en lui passant les données scrapées)
    log.info("\n── Étape 2 : Analyse IA (Claude) ──")
    new_news = fetch_news_from_claude(TOOLS_TO_WATCH, creator_data)

    if not new_news:
        log.warning("Aucune donnée reçue de Claude — arrêt du script.")
    else:
        # 3. Mettre à jour news.json
        log.info("\n── Étape 3 : Mise à jour de news.json ──")
        diff = update_news_json(new_news)

        # 4. Envoyer l'e-mail (avec résumé des sources créateurs)
        log.info("\n── Étape 4 : Envoi de l'e-mail ──")
        send_email(diff, creator_data)

    log.info("\n" + "═" * 60)
    log.info("Mise à jour terminée.")
    log.info("═" * 60)
