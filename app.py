from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote, urlparse
import urllib.request
import json
import re
import os

app = Flask(__name__)

# Configurazione ScraperAPI (Proxy Mode)
SCRAPER_API_KEY = "60730861602c4b7fb98ec93607035e7d"
PROXY_SERVER = f"http://scraperapi:{SCRAPER_API_KEY}@proxy-server.scraperapi.com:8001"

def get_trustpilot_data(domain, api_key):
    """Cerca su Trustpilot usando il motore di ricerca interno per evitare errori di dominio"""
    try:
        # Pulizia dominio
        clean_domain = domain.replace('www.', '')
        search_url = f"https://it.trustpilot.com/search?query={clean_domain}"
        # Chiamata proxy diretta (senza browser per velocità)
        proxy_url = f"http://api.scraperapi.com/?api_key={api_key}&url={quote(search_url)}&country_code=it"
        
        req = urllib.request.Request(proxy_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as response:
            # Trustpilot spesso fa un redirect 302 se trova l'azienda esatta
            final_url = response.geturl()
            html = response.read().decode('utf-8')
            
        soup = BeautifulSoup(html, "html.parser")
        score, reviews = "N/A", "0"

        # Estrazione dal DB Next.js
        next_data = soup.find("script", id="__NEXT_DATA__")
        if next_data:
            data = json.loads(next_data.string)
            props = data.get('props', {}).get('pageProps', {})
            # Se siamo nella pagina dei risultati di ricerca
            business_units = props.get('businessUnits', [])
            if business_units:
                unit = business_units[0]
                score = str(unit.get('trustScore', "N/A"))
                reviews = str(unit.get('numberOfReviews', "0"))
            # Se siamo finiti direttamente nella pagina azienda via redirect
            else:
                unit = props.get('businessUnit', {})
                score = str(unit.get('trustScore', "N/A"))
                reviews = str(unit.get('numberOfReviews', "0"))
                
        return score, reviews
    except:
        return "N/A", "0"

def check_site_advanced(target_url):
    detected_platform = "Sconosciuta"
    detected_providers = set()
    page_title = "N/A"
    logs = []

    with sync_playwright() as p:
        # Lanciamo il browser con ScraperAPI come PROXY
        browser = p.chromium.launch(headless=True)
        
        # Creiamo un contesto con Stealth e Proxy
        context = browser.new_context(
            proxy={"server": PROXY_SERVER},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            ignore_https_errors=True
        )
        
        page = context.new_page()
        stealth_sync(page) # Applica le tecniche di evasione anti-bot

        # --- NETWORK INTERCEPTION ---
        def intercept_network(response):
            url = response.url.lower()
            # Identificazione Piattaforme via Headers/URL
            nonlocal detected_platform
            if "shopify" in url or "x-shopify-stage" in response.headers:
                detected_platform = "Shopify"
            elif "dwac_" in url or "dwsid" in url:
                detected_platform = "Salesforce Commerce Cloud"
            
            # Identificazione Widget via Network Calls
            if "bazaarvoice.com" in url or "bv.js" in url:
                detected_providers.add("Bazaarvoice")
            elif "yotpo.com" in url:
                detected_providers.add("Yotpo")
            elif "trustpilot.com/widget" in url:
                detected_providers.add("Trustpilot")
            elif "judgeme" in url:
                detected_providers.add("Judge.me")

        page.on("response", intercept_network)

        try:
            # Navigazione Homepage
            page.goto(target_url, wait_until="networkidle", timeout=60000)
            page_title = page.title()
            
            # Controllo Piattaforma via Cookie (SFCC Detection)
            cookies = context.cookies()
            if any("dwac_" in c['name'] or "dwsid" in c['name'] for c in cookies):
                detected_platform = "Salesforce Commerce Cloud"
            
            # Cerchiamo un link prodotto per validazione profonda
            html = page.content()
            soup = BeautifulSoup(html, "html.parser")
            links = [urljoin(target_url, a.get('href')) for a in soup.find_all('a', href=True)]
            product_links = [l for l in links if any(k in l.lower() for k in ['/p/', '/prodotto/', '/product/'])][:1]

            if product_links:
                # Navigazione Prodotto + Comportamento Umano
                page.goto(product_links[0], wait_until="networkidle", timeout=60000)
                # Scroll per triggerare i widget pigri (Lazy Loading)
                page.mouse.wheel(0, 2000)
                page.wait_for_timeout(3000)
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(2000)

        except Exception as e:
            logs.append(f"Errore: {str(e)}")
        finally:
            browser.close()

    # Logica finale
    has_widget = len(detected_providers) > 0
    domain = urlparse(target_url).netloc
    tp_score, tp_reviews = get_trustpilot_data(domain, SCRAPER_API_KEY)

    return {
        "titolo_pagina": page_title,
        "piattaforma": detected_platform,
        "widget_presente": has_widget,
        "providers_trovati": list(detected_providers),
        "trustpilot_score": tp_score,
        "trustpilot_reviews": tp_reviews,
        "internal_log": " ".join(logs)
    }

@app.route('/api/check', methods=['POST'])
def api_check():
    data = request.get_json()
    url = data.get('url', '').strip()
    if not url.startswith('http'): url = 'https://' + url
    
    try:
        result = check_site_advanced(url)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
