from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import json
import re

app = Flask(__name__)

PROVIDERS = [
    "trustpilot.com", "yotpo.com", "reviews.io", 
    "stamped.io", "okendo.io", "bazaarvoice.com", "judge.me",
    "powerreviews.com", "reevoo.com", "feefo.com"
]

def extract_products(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if "/products/" in href or "/prodotto/" in href:
            links.append(urljoin(base_url, href))
    return list(set(links))[:2] 

def detect_platform(html):
    if not html:
        return "Sconosciuta"
        
    html_lower = html.lower()
    
    # Rilevamento tramite impronte digitali note (CDN, script, classi)
    if "cdn.shopify.com" in html_lower or "window.shopify" in html_lower or "shopify.theme" in html_lower:
        return "Shopify"
    if "wp-content/plugins/woocommerce" in html_lower or "woocommerce-cart" in html_lower or "var woocommerce_params" in html_lower:
        return "WooCommerce"
    if "text/x-magento-init" in html_lower or "mage.cookies" in html_lower or "skin/frontend/" in html_lower:
        return "Magento"
    if "var prestashop" in html_lower or 'content="prestashop"' in html_lower:
        return "PrestaShop"
    if "cdn11.bigcommerce.com" in html_lower:
        return "BigCommerce"
        
    return "Sconosciuta"

def detect(html):
    """
    Motore di detection universale per widget recensioni.
    Implementa Scraping Semantico, Microdati (JSON-LD) ed Euristiche Regex.
    """
    if not html:
        return False

    soup = BeautifulSoup(html, "html.parser")
    html_lower = html.lower()

    # --- LIVELLO 1: PROVIDER NOTI (Il controllo veloce) ---
    if any(provider in html_lower for provider in PROVIDERS):
        return True
    
    if "shopify-product-reviews" in html_lower or "spr-container" in html_lower:
        return True

    # --- LIVELLO 2: MICRODATI STRUTTURATI (Schema.org) ---
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            json_str = script.string.strip() if script.string else ""
            if not json_str:
                continue
                
            data = json.loads(json_str)
            
            items = data if isinstance(data, list) else [data]
                
            for item in items:
                item_str = json.dumps(item).lower()
                if '"@type": "aggregaterating"' in item_str or '"@type": "review"' in item_str:
                    return True
                if '"@type": "product"' in item_str and ('"aggregaterating"' in item_str or '"review"' in item_str):
                    return True
        except Exception:
            pass 

    if soup.find(attrs={"itemprop": re.compile(r"aggregateRating|review", re.I)}):
        return True
    if soup.find(attrs={"itemtype": re.compile(r"AggregateRating|Review", re.I)}):
        return True

    # --- LIVELLO 3: SCRAPING SEMANTICO (Classi CSS e ID) ---
    review_classes = re.compile(
        r"(bv-rating|yotpo-bottomline|jdgm-widget|spr-badge|stamped-summary|pr-snippet|trustpilot-widget|review-stars|star-rating|product-reviews|rating-summary)", 
        re.I
    )
    
    for tag in soup.find_all(['div', 'span', 'section']):
        classes = tag.get('class', [])
        if not isinstance(classes, list):
            classes = [classes]
            
        class_string = " ".join(classes).lower()
        id_string = tag.get('id', '').lower()
        
        if review_classes.search(class_string) or review_classes.search(id_string):
            return True

    # --- LIVELLO 4: PARSING TESTUALE INTELLIGENTE (Regex Euristiche) ---
    for script_or_style in soup(['script', 'style']):
        script_or_style.decompose()
        
    visible_text = soup.get_text(separator=' ', strip=True).lower()

    if re.search(r"\(?\b\d+\b\)?\s*(recensioni|recensione|reviews|review)\b", visible_text):
         return True
         
    if re.search(r"\b[0-5][.,][0-9]\s*(/|su)\s*5\b", visible_text):
         return True

    return False

def check_ecommerce_optimized(base_url):
    combined_html = ""
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
        )
        try:
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()
            
            try:
                page.goto(base_url, timeout=15000)
                page.wait_for_timeout(2000)
                homepage_html = page.content()
                combined_html += homepage_html
            except Exception as e:
                print(f"Errore homepage {base_url}: {str(e)}")
                return False, "Errore Navigazione" 
                
            product_links = extract_products(homepage_html, base_url)
            
            for p_url in product_links:
                try:
                    page.goto(p_url, timeout=15000)
                    page.wait_for_timeout(2000) 
                    combined_html += page.content()
                except Exception as e:
                    print(f"Errore prodotto {p_url}: {str(e)}")
                    
        finally:
            browser.close()
            
    widget_presente = detect(combined_html)
    piattaforma = detect_platform(combined_html)
    
    return widget_presente, piattaforma

# --- LA SPIA LUMINOSA (Verifica se il server è online dal browser) ---
@app.route('/', methods=['GET'])
def home():
    return "✅ Il server Python è VIVO e funzionante! Il motore Anti-Bot è pronto."

# --- API PER n8n ---
@app.route('/api/check', methods=['POST'])
@app.route('/api/check/', methods=['POST'])
def api_single_check():
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({'widget_presente': False, 'piattaforma': 'Sconosciuta', 'error': 'URL mancante'}), 400
    
    url = data['url'].strip()
    if not url.startswith('http://') and not url.startswith('https://'):
        url = 'https://' + url
    
    try:
        has_widget, platform = check_ecommerce_optimized(url)
        return jsonify({
            'widget_presente': has_widget,
            'piattaforma': platform
        })
    except Exception as e:
        return jsonify({
            'widget_presente': False,
            'piattaforma': 'Sconosciuta',
            'error': str(e)
        }), 500

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 8000))
    app.run(debug=False, host='0.0.0.0', port=port)
