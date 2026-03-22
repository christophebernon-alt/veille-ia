#!/usr/bin/env python3
"""
veille_ia_updater.py - Mise a jour automatique de la veille IA
"""

import os
import json
import smtplib
import logging
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

try:
    import anthropic
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "anthropic"])
    import anthropic

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "beautifulsoup4"])
    import requests
    from bs4 import BeautifulSoup

# ============================================================
#  CONFIG
# ============================================================
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "sk-ant-VOTRE_CLE_ICI")
NEWS_JSON_PATH    = Path(__file__).parent / "news.json"
SMTP_HOST         = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT         = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER         = os.getenv("SMTP_USER", "votre.email@gmail.com")
SMTP_PASSWORD     = os.getenv("SMTP_PASSWORD", "votre_mot_de_passe")
EMAIL_FROM        = os.getenv("EMAIL_FROM", "Veille IA <veille@exemple.fr>")
EMAIL_TO          = os.getenv("EMAIL_TO", "vous@exemple.fr").split(",")
EMAIL_SUBJECT     = "Veille IA - Nouveautes de la semaine"
MAX_NEWS_PER_TOOL = 3
SCRAPE_DELAY      = 2

TOOLS_TO_WATCH = [
    "ChatGPT", "Claude", "Gemini", "Le Chat Mistral",
    "Midjourney", "Adobe Firefly", "Canva AI", "Ideogram AI",
    "Suno AI", "Udio", "ElevenLabs", "Soundraw",
    "Runway", "Sora", "HeyGen", "Veo 3", "Descript",
    "Notion AI", "Grammarly", "Copy.ai",
    "GitHub Copilot", "Cursor", "Replit AI", "Lovable",
    "Gamma", "Beautiful.ai", "Napkin AI",
    "Perplexity AI", "NotebookLM", "Consensus",
    "D-ID", "Murf AI", "Framer AI",
]

CREATOR_SOURCES = [
    {
        "name": "Gabzer",
        "url": "https://gabzer.fr/",
        "selectors": ["h2", "h3", ".article-title", ".post-title"],
        "description": "Videastes tech et IA - 1M+ abonnes",
    },
    {
        "name": "Superproductif",
        "url": "https://newsletter.superproductif.fr/",
        "selectors": ["h2", "h3", ".post-title", ".entry-title"],
        "description": "Newsletter IA hebdomadaire - 45k abonnes",
    },
    {
        "name": "Mathieu Ibanez",
        "url": "https://web.mathieuibanez.com/",
        "selectors": ["h2", "h3", ".article-title", ".post-title"],
        "description": "Expert IA creative - Midjourney, video IA",
    },
]

# ============================================================
#  LOGGING
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}


# ============================================================
#  1. SCRAPING
# ============================================================
def scrape_creator(source):
    try:
        log.info("Scraping %s", source["url"])
        resp = requests.get(source["url"], headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        titles = []
        seen = set()
        skip = ["menu", "navigation", "accueil", "home", "contact", "login"]
        for selector in source["selectors"]:
            for el in soup.select(selector):
                text = el.get_text(strip=True)
                if 15 < len(text) < 200 and text not in seen:
                    if not any(w in text.lower() for w in skip):
                        titles.append(text)
                        seen.add(text)
                if len(titles) >= 10:
                    break
            if len(titles) >= 10:
                break
        log.info("  -> %d titres sur %s", len(titles), source["name"])
        return titles[:10]
    except Exception as e:
        log.warning("Erreur scraping %s : %s", source["name"], e)
        return []


def scrape_all_creators():
    results = {}
    for source in CREATOR_SOURCES:
        titles = scrape_creator(source)
        results[source["name"]] = {
            "url": source["url"],
            "description": source["description"],
            "titles": titles,
        }
        time.sleep(SCRAPE_DELAY)
    return results


# ============================================================
#  2. CLAUDE
# ============================================================
def fetch_news_from_claude(tools, creator_data=None):
    today = datetime.now().strftime("%d %B %Y")
    tool_list = "\n".join("- " + t for t in tools)

    creator_block = ""
    if creator_data:
        creator_block = "\n\nContenus scrapes cette semaine :\n"
        for name, data in creator_data.items():
            creator_block += "\n### " + name + "\n"
            if data["titles"]:
                for t in data["titles"]:
                    creator_block += "- " + t + "\n"
            else:
                creator_block += "- (Site non accessible)\n"

    prompt = (
        "Tu es un expert en veille technologique IA. Nous sommes le " + today + ".\n\n"
        "Outils a surveiller :\n" + tool_list + "\n"
        + creator_block + "\n\n"
        "Pour chaque outil, indique les nouveautes des 7 derniers jours.\n"
        "Si une info vient du contenu scrape, ajoute (via NomCreateur).\n"
        "Si pas d info recente, retourne liste vide.\n"
        "Reponds UNIQUEMENT avec du JSON valide sans markdown ni backticks :\n"
        "{\"ChatGPT\": [{\"date\": \"21 mars 2026\", \"text\": \"...\"}], \"Midjourney\": [], ...}\n"
        "Inclure tous les outils, meme ceux sans actualite."
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    log.info("Interrogation de Claude...")
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        result = json.loads(raw)
        log.info("Claude OK - %d outils traites", len(result))
        return result
    except json.JSONDecodeError as e:
        log.error("Erreur JSON : %s", e)
        return {}


# ============================================================
#  3. MISE A JOUR news.json
# ============================================================
def update_news_json(new_news):
    today_str = datetime.now().strftime("%d %B %Y")
    if NEWS_JSON_PATH.exists():
        with open(NEWS_JSON_PATH, encoding="utf-8") as f:
            existing = json.load(f)
    else:
        existing = {"lastUpdate": today_str, "news": {}}

    old_news = existing.get("news", {})
    diff = {}

    for tool, items in new_news.items():
        if not items:
            continue
        prev_texts = {n["text"] for n in old_news.get(tool, [])}
        truly_new = [n for n in items if n["text"] not in prev_texts]
        if truly_new:
            diff[tool] = truly_new
            combined = truly_new + old_news.get(tool, [])
            old_news[tool] = combined[:MAX_NEWS_PER_TOOL]

    existing["news"] = old_news
    existing["lastUpdate"] = today_str

    with open(NEWS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    log.info("news.json mis a jour - %d outil(s) avec nouveautes", len(diff))
    return diff


# ============================================================
#  4. EMAIL
# ============================================================
def send_email(diff, creator_data=None):
    today_str = datetime.now().strftime("%d %B %Y")

    if not diff:
        body = "<p style='font-family:Arial;color:#555'>Aucune nouveaute cette semaine.</p>"
    else:
        body = ""
        for tool, items in diff.items():
            for item in items:
                body += (
                    "<div style='font-family:Arial;border-left:4px solid #1a4fad;"
                    "padding:10px 14px;margin-bottom:12px;background:#f8f9ff'>"
                    "<strong style='color:#1a4fad'>" + tool + "</strong> "
                    "<span style='color:#999;font-size:11px'>" + item.get("date", "") + "</span><br>"
                    "<span style='color:#333;font-size:14px'>" + item["text"] + "</span>"
                    "</div>"
                )

    sources_html = ""
    if creator_data:
        parts = []
        for name, data in creator_data.items():
            status = str(len(data["titles"])) + " titres" if data["titles"] else "non accessible"
            parts.append(name + " (" + status + ")")
        sources_html = (
            "<hr style='border:1px solid #eee;margin:20px 0'>"
            "<p style='font-family:Arial;font-size:12px;color:#888'>Sources : "
            + " | ".join(parts) + "</p>"
        )

    html = (
        "<html><body style='background:#f4f4f0;padding:30px'>"
        "<div style='max-width:600px;margin:0 auto;background:#fff;"
        "border:1px solid #ddd;border-radius:6px;overflow:hidden'>"
        "<div style='background:#1a1a18;padding:24px'>"
        "<h2 style='color:#fff;margin:0;font-family:Arial'>Veille IA - Nouveautes</h2>"
        "<p style='color:#666;font-size:12px;margin:4px 0 0'>" + today_str + "</p>"
        "</div>"
        "<div style='padding:24px'>" + body + sources_html + "</div>"
        "<div style='background:#f4f4f0;padding:16px;border-top:1px solid #eee'>"
        "<p style='font-family:Arial;font-size:11px;color:#999;margin:0'>"
        "Email automatique hebdomadaire</p>"
        "</div></div></body></html>"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = EMAIL_SUBJECT
    msg["From"]    = EMAIL_FROM
    msg["To"]      = ", ".join(EMAIL_TO)
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, EMAIL_TO, msg.as_string())
        log.info("Email envoye a : %s", ", ".join(EMAIL_TO))
    except Exception as e:
        log.error("Erreur envoi email : %s", e)


# ============================================================
#  MAIN
# ============================================================
if __name__ == "__main__":
    log.info("Demarrage de la veille IA")

    log.info("Etape 1 : Scraping des createurs")
    creator_data = scrape_all_creators()

    log.info("Etape 2 : Analyse Claude")
    new_news = fetch_news_from_claude(TOOLS_TO_WATCH, creator_data)

    if not new_news:
        log.warning("Aucune donnee de Claude - arret.")
    else:
        log.info("Etape 3 : Mise a jour news.json")
        diff = update_news_json(new_news)

        log.info("Etape 4 : Envoi email")
        send_email(diff, creator_data)

    log.info("Termine.")
