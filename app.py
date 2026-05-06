from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote, urlparse
import json
import re
import os

app = Flask(__name__)

PROVIDERS = [
    "trustpilot.com", "yotpo.com", "reviews.io", 
    "stamped.io", "okendo.io", "bazaarvoice.com", "judge.me",
    "powerreviews.com", "reevoo.com", "feefo.com"
]

def detect_platform(html):
    if not html: return "Sconosciuta"
    html_lower = html.lower()
    if "cdn.shopify.com" in html_lower or "window.shopify" in html_lower or "shopify.theme" in html_lower: return "Shopify"
    if "woocommerce" in html_lower or "wp-content/plugins/woocommerce" in html_lower: return "WooCommerce"
    if "text/x-magento-init" in html_lower: return "Magento"
    if "prestashop" in html_lower: return "PrestaShop"
    if "cdn11.bigcommerce.com" in html_lower: return "BigCommerce"
    if "demandware.store" in html_lower or "salesforce" in html_lower: return "Salesforce Commerce Cloud"
    return "Sconosciuta"

def detect(html):
    if not html: return False
    soup = BeautifulSoup(html, "html.parser")
    html_lower = html.lower()

    # Ricerca bruta per domini dei provider
    if any(provider in html_lower for provider in PROVIDERS): return True
    
    # Firme specifiche (anche se Lazy Loaded, i link JS spesso ci sono)
    if "bazaarvoice.com/deployments" in html_lower or "bv.js" in html_lower or "bazaarvoice" in html_lower: return True
    if "widget.trustpilot.com" in html_lower or "trustpilot-widget" in html_lower: return True
    if "cdn-stamped-io" in html_lower or "stamped-summary" in html_lower: return True
    if "yotpo.com/js" in html_lower or "yotpo-bottomline" in html_lower: return True
    if "shopify-product-reviews" in html_lower or "spr-container" in html_lower: return True

    # Parsing JSON-LD generico
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            json_str = script.string.strip() if script.string else ""
            if not json_str: continue
            data = json.loads(json_str)
            items = data if isinstance(data, list) else [data]
            for item in items:
                item_str = json.dumps(item).lower()
                if '"@type": "aggregaterating"' in item_str or '"@type": "review"' in item_str: return True
        except Exception:
            pass 

    # Attributi Schema.org
    if soup.find(attrs={"itemprop": re.compile(r"aggregateRating|review", re.I)}): return True
    
    # Classi CSS classiche
    review_classes = re.compile(r"(bv-rating|yotpo-bottomline|jdgm-widget|spr-badge|stamped-summary|trustpilot-widget|rating-summary)", re.I)
    for tag in soup.find_all(['div', 'span', 'section']):
        classes = " ".join(tag.get('class', [])).lower() if isinstance(tag.get('class'), list) else tag.get('class', '')
        if review_classes.search(str(classes)) or review_classes.search(str(tag.get('id', ''))): return True

    return False

def extract_trustpilot_data(page, base_url, api_key):
    try:
        domain = urlparse(base_url).netloc.replace('www.', '')
        if not domain: return "N/A", "0"
        
        tp_url = f"https://it.trustpilot.com/review/{domain}"
        # Rimosso 'render=true' di proposito. Vogliamo HTML grezzo con i dati Next.js nascosti.
        bypass_tp = f"http://api.scraperapi.com/?api_key={api_key}&url={quote(tp_url)}&country_code=it"
        
        page.goto(bypass_tp, timeout=45000, wait_until="domcontentloaded")
        tp_html = page.content()
        soup = BeautifulSoup(tp_html, "html.parser")
        
        score = "N/A"
        reviews_count = "0"
        
        # 1. Hack Definitivo: Database Next.js (Infallibile se l'azienda esiste)
        next_data = soup.find("script", id="__NEXT_DATA__")
        if next_data:
            try:
                data = json.loads(next_data.string)
                business_unit = data.get('props', {}).get('pageProps', {}).get('businessUnit', {})
                if business_unit:
                    score = str(business_unit.get('trustScore', "N/A"))
                    reviews_count = str(business_unit.get('numberOfReviews', "0"))
                    if score != "N/A": return score, reviews_count
            except: pass
        
        # 2. Fallback: JSON-LD Standard
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                json_text = script.string if script.string else ""
                if "aggregateRating" in json_text:
                    data = json.loads(json_text)
                    if isinstance(data, list): data = data[0]
                    if data.get("@type") in ["LocalBusiness", "Organization"] and "aggregateRating" in data:
                        score = str(data["aggregateRating"].get("ratingValue", "N/A"))
                        reviews_count = str(data["aggregateRating"].get("reviewCount", "0"))
                        return score, reviews_count
            except: pass
                
        return score, reviews_count
    except Exception as e:
        return "N/A", "0"

def check_ecommerce_optimized(base_url):
    combined_html = ""
    error_log = ""
    API_KEY = "60730861602c4b7fb98ec93607035e7d" # Manteniamo la chiave qui per i test attuali
    
    if not base_url.startswith('http'): base_url = 'https://' + base_url

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-blink-features=AutomationControlled']
        )
        try:
            context = browser.new_context(viewport={"width": 1920, "height": 1080}, locale="it-IT")
            page = context.new_page()
            
            # --- 1. HOMEPAGE ---
            try:
                bypass_url = f"http://api.scraperapi.com/?api_key={API_KEY}&url={quote(base_url)}&render=true&premium=true&country_code=it"
                page.goto(bypass_url, timeout=90000, wait_until="domcontentloaded")
                homepage_html = page.content()
                combined_html += homepage_html
                
                # Usa BeautifulSoup per estrarre i link, è più affidabile su ScraperAPI
                soup_home = BeautifulSoup(homepage_html, "html.parser")
                raw_links = [a.get('href') for a in soup_home.find_all('a', href=True)]
                
                keywords = ['/product/', '/products/', '/prodotto/', '/p/', '.html']
                valid_links = []
                for href in raw_links:
                    h_lower = href.lower()
                    if any(k in h_lower for k in keywords) and not href.startswith('#'):
                        full_link = urljoin(base_url, href)
                        if full_link != base_url and full_link not in valid_links:
                            valid_links.append(full_link)
                
                valid_links = valid_links[:2] # Prende i primi due prodotti reali
                
            except Exception as e:
                error_log += f"[Errore Homepage: {str(e)}] "
                valid_links = []
                
            # --- 2. PAGINE PRODOTTO ---
            for p_url in valid_links:
                try:
                    p_bypass_url = f"http://api.scraperapi.com/?api_key={API_KEY}&url={quote(p_url)}&render=true&premium=true&country_code=it"
                    page.goto(p_bypass_url, timeout=90000, wait_until="domcontentloaded")
                    combined_html += page.content()
                except Exception as e:
                    error_log += f"[Timeout Prodotto: {p_url}] "
            
            # --- 3. ESTRAZIONE TRUSTPILOT ---
            tp_score, tp_reviews = extract_trustpilot_data(page, base_url, API_KEY)
                    
        except Exception as global_e:
             return False, "Sconosciuta", "N/A", "0", f"Errore Core Browser: {str(global_e)}"
        finally:
            browser.close()
            
    widget_presente = detect(combined_html)
    piattaforma = detect_platform(combined_html)
    
    return widget_presente, piattaforma, tp_score, tp_reviews, error_log

@app.route('/', methods=['GET'])
def home():
    return "✅ Server VIVO - Motore JSON-LD e TP __NEXT_DATA__ ATTIVATO."

@app.route('/api/check', methods=['POST'])
@app.route('/api/check/', methods=['POST'])
def api_single_check():
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({'error': 'URL mancante'}), 400
    
    url = data['url'].strip()
    
    try:
        has_widget, platform, tp_score, tp_reviews, log = check_ecommerce_optimized(url)
        response_data = {
            'widget_presente': has_widget,
            'piattaforma': platform,
            'trustpilot_score': tp_score,
            'trustpilot_reviews': tp_reviews
        }
        if log:
            response_data['internal_log'] = log.strip()
            
        return jsonify(response_data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(debug=False, host='0.0.0.0', port=port)
