from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import json

app = Flask(__name__)

PROVIDERS = [
    "trustpilot.com", "yotpo.com", "reviews.io", 
    "stamped.io", "okendo.io", "bazaarvoice.com", "judge.me"
]

def extract_products(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if "/products/" in href or "/prodotto/" in href:
            links.append(urljoin(base_url, href))
    return list(set(links))[:2] 

def detect(html):
    if not html:
        return False
        
    soup = BeautifulSoup(html, "html.parser")
    
    for tag in soup.find_all(["script", "iframe"], src=True):
        src = tag.get("src", "").lower()
        if any(p in src for p in PROVIDERS):
            return True
    
    html_lower = html.lower()
    if "shopify-product-reviews" in html_lower or "spr-container" in html_lower:
        return True
    
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if "rating" in json.dumps(data).lower():
                return True
        except:
            pass
    
    return False

# --- NUOVA FUNZIONE: PLATFORM DETECTION ---
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
            
    # Ora restituiamo DUE dati
    widget_presente = detect(combined_html)
    piattaforma = detect_platform(combined_html)
    
    return widget_presente, piattaforma

@app.route('/', methods=['GET'])
def home():
    return "✅ Il server Python è VIVO e funzionante! Il motore Anti-Bot è pronto."

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
        # Estraiamo i due valori dalla funzione
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
