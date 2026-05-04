from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote
import json
import re
import os
import traceback

app = Flask(__name__)

PROVIDERS = ["trustpilot.com", "yotpo.com", "reviews.io", "stamped.io", "okendo.io", "bazaarvoice.com", "judge.me", "powerreviews.com"]

def detect_platform(html):
    if not html: return "Sconosciuta"
    h = html.lower()
    if "cdn.shopify.com" in h or "window.shopify" in h: return "Shopify"
    if "woocommerce" in h: return "WooCommerce"
    if "demandware.store" in h or "salesforce" in h: return "Salesforce Commerce Cloud"
    return "Sconosciuta"

def detect(html):
    if not html: return False
    h = html.lower()
    if any(p in h for p in PROVIDERS): return True
    if "aggregaterating" in h or "review" in h: return True
    return False

def check_ecommerce_optimized(base_url):
    combined_html = ""
    error_log = ""
    # Pescaggio sicuro della chiave dalle variabili d'ambiente di Railway
    api_key = os.environ.get("SCRAPER_API_KEY")
    
    if not api_key:
        return False, "Sconosciuta", "ERRORE: SCRAPER_API_KEY non configurata su Railway"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 1920, "height": 1080})
            page = context.new_page()
            
            # --- FASE 1: HOMEPAGE CON PROXY PREMIUM ---
            # Aggiungiamo premium=true per forzare l'uso di IP residenziali puliti
            bypass_url = f"http://api.scraperapi.com/?api_key={api_key}&url={quote(base_url)}&render=true&premium=true&country_code=it"
            
            try:
                page.goto(bypass_url, timeout=90000, wait_until="domcontentloaded")
                page.wait_for_timeout(5000) 
                homepage_html = page.content()
                combined_html += homepage_html
                
                # Estrazione link prodotti
                raw_links = page.evaluate("() => Array.from(document.querySelectorAll('a')).map(a => a.getAttribute('href')).filter(h => h)")
                keywords = ['/product/', '/products/', '/p/', '/item/', '.html']
                valid_links = [urljoin(base_url, h) for h in raw_links if any(k in h.lower() for k in keywords) and len(h) > 20][:2]
                
                if not valid_links:
                    error_log = f"Nessun prodotto trovato. Titolo: {page.title()}"
            except Exception as e:
                return False, "Sconosciuta", f"Errore Bypass Premium: {str(e)}"

            # --- FASE 2: PRODOTTI ---
            for p_url in valid_links:
                try:
                    p_bypass = f"http://api.scraperapi.com/?api_key={api_key}&url={quote(p_url)}&render=true&premium=true&country_code=it"
                    page.goto(p_bypass, timeout=90000)
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight/2)")
                    page.wait_for_timeout(3000)
                    combined_html += page.content()
                except: continue
                    
        finally:
            browser.close()
            
    return detect(combined_html), detect_platform(combined_html), error_log

@app.route('/api/check', methods=['POST'])
def api_single_check():
    data = request.get_json()
    url = data.get('url', '').strip()
    if not url: return jsonify({'error': 'URL missing'}), 400
    if not url.startswith('http'): url = 'https://' + url
    
    try:
        has_widget, platform, log = check_ecommerce_optimized(url)
        return jsonify({'widget_presente': has_widget, 'piattaforma': platform, 'internal_log': log})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))
