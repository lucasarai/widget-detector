from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote
import json
import re
import traceback

app = Flask(__name__)

PROVIDERS = [
    "trustpilot.com", "yotpo.com", "reviews.io", 
    "stamped.io", "okendo.io", "bazaarvoice.com", "judge.me",
    "powerreviews.com", "reevoo.com", "feefo.com"
]

def detect_platform(html):
    if not html:
        return "Sconosciuta"
        
    html_lower = html.lower()
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
    if "demandware.store" in html_lower or "dw.acct" in html_lower or "salesforce" in html_lower:
        return "Salesforce Commerce Cloud"
        
    return "Sconosciuta"

def detect(html):
    if not html:
        return False

    soup = BeautifulSoup(html, "html.parser")
    html_lower = html.lower()

    if any(provider in html_lower for provider in PROVIDERS):
        return True
    
    if "shopify-product-reviews" in html_lower or "spr-container" in html_lower:
        return True

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            json_str = script.string.strip() if script.string else ""
            if not json_str: continue
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

    review_classes = re.compile(
        r"(bv-rating|yotpo-bottomline|jdgm-widget|spr-badge|stamped-summary|pr-snippet|trustpilot-widget|review-stars|star-rating|product-reviews|rating-summary)", 
        re.I
    )
    for tag in soup.find_all(['div', 'span', 'section']):
        classes = tag.get('class', [])
        if not isinstance(classes, list): classes = [classes]
        class_string = " ".join(classes).lower()
        id_string = tag.get('id', '').lower()
        if review_classes.search(class_string) or review_classes.search(id_string):
            return True

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
    error_log = ""
    API_KEY = "60730861602c4b7fb98ec93607035e7d"
    
    with sync_playwright() as p:
        # RIMOSSO IL PROXY DI RETE CHE CAUSAVA IL BLOCCO IP. Bypassiamo via URL.
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox', 
                '--disable-setuid-sandbox', 
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled'
            ]
        )
        try:
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="it-IT"
            )
            page = context.new_page()
            
            try:
                # CREAZIONE DELL'URL BYPASS
                encoded_base_url = quote(base_url)
                # Chiediamo a ScraperAPI di fare il lavoro sporco con render=true
                bypass_url = f"http://api.scraperapi.com/?api_key={API_KEY}&url={encoded_base_url}&render=true&country_code=it"
                
                page.goto(bypass_url, timeout=60000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000) 
                
                page_title = page.title()
                homepage_html = page.content()
                combined_html += homepage_html
                
                # ESTRAZIONE LINK A PROVA DI BOMBA (Prendiamo il parametro href grezzo)
                raw_links = page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('a')).map(a => a.getAttribute('href')).filter(h => h);
                }""")
                
                keywords = ['/product/', '/products/', '/prodotto/', '/prodotti/', '/p/', '/item/', '/sku/', '-p-']
                exclude = ['/cart', '/login', '/account', '/checkout', '/wishlist', '/category/', '/brand/', '/privacy', '/terms', '/faq']
                
                valid_links = []
                for href in raw_links:
                    href_lower = href.lower()
                    if any(ex in href_lower for ex in exclude): continue
                    if href_lower == base_url.lower() or href_lower == '/': continue
                    
                    if any(k in href_lower for k in keywords) or (href_lower.endswith('.html') and len(href) > 20):
                        # Ricostruiamo l'URL originale di Sephora, unendo la base corretta all'href
                        valid_links.append(urljoin(base_url, href))
                
                product_links = list(set(valid_links))[:2]
                
                if not product_links:
                    try:
                        visible_text = page.evaluate("document.body.innerText.substring(0, 150)")
                        visible_text = visible_text.replace('\n', ' ').strip()
                    except:
                        visible_text = "Impossibile leggere il testo"
                        
                    error_log += f" [Nessun Prodotto Trovato: Titolo='{page_title}' | Testo='{visible_text}'] "
                    
            except Exception as e:
                error_log = f"Errore navigazione Bypass API: {str(e)}"
                return False, "Sconosciuta", error_log
                
            for p_url in product_links:
                try:
                    encoded_p_url = quote(p_url)
                    p_bypass_url = f"http://api.scraperapi.com/?api_key={API_KEY}&url={encoded_p_url}&render=true&country_code=it"
                    
                    page.goto(p_bypass_url, timeout=60000, wait_until="domcontentloaded")
                    page.wait_for_timeout(4000) 
                    combined_html += page.content()
                except Exception as e:
                    error_log += f" | Timeout su prodotto ({p_url})"
                    
        except Exception as global_e:
             return False, "Sconosciuta", f"Errore Core: {str(global_e)}"
        finally:
            browser.close()
            
    widget_presente = detect(combined_html)
    piattaforma = detect_platform(combined_html)
    
    return widget_presente, piattaforma, error_log

@app.route('/', methods=['GET'])
def home():
    return "✅ Server VIVO e API BYPASS ATTIVATO."

@app.route('/api/check', methods=['POST'])
@app.route('/api/check/', methods=['POST'])
def api_single_check():
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({'error': 'URL mancante'}), 400
    
    url = data['url'].strip()
    if not url.startswith('http://') and not url.startswith('https://'):
        url = 'https://' + url
    
    try:
        has_widget, platform, log = check_ecommerce_optimized(url)
        response_data = {
            'widget_presente': has_widget,
            'piattaforma': platform
        }
        if log:
            response_data['internal_log'] = log.strip()
            
        return jsonify(response_data)
    except Exception as e:
        return jsonify({'widget_presente': False, 'piattaforma': 'Sconosciuta', 'error': str(e)}), 500

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 8000))
    app.run(debug=False, host='0.0.0.0', port=port)
