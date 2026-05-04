from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from urllib.parse import urljoin
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
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox', 
                '--disable-setuid-sandbox', 
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled', 
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process'
            ]
        )
        try:
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="it-IT"
            )
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page = context.new_page()
            
            try:
                page.goto(base_url, timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000) 
                
                # Spara al banner dei cookie
                try:
                    page.evaluate("""
                        const buttons = Array.from(document.querySelectorAll('button, a, div[role="button"]'));
                        const acceptBtn = buttons.find(b => b.innerText.match(/accetta|accept|agree|consenti/i));
                        if(acceptBtn) acceptBtn.click();
                    """)
                    page.wait_for_timeout(1000)
                except: pass
                
                # Scroll verso il basso
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight/2)")
                    page.wait_for_timeout(2000)
                except: pass
                
                page_title = page.title()
                homepage_html = page.content()
                combined_html += homepage_html
                
                # ESTRAZIONE LINK NATIVA JS (Bypassa i limiti di Python/BeautifulSoup)
                raw_links = page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('a')).map(a => a.href).filter(h => h);
                }""")
                
                keywords = ['/product/', '/products/', '/prodotto/', '/prodotti/', '/p/', '/item/', '/sku/', '-p-']
                exclude = ['/cart', '/login', '/account', '/checkout', '/wishlist', '/category/', '/brand/', '/privacy', '/terms', '/faq']
                
                valid_links = []
                for href in raw_links:
                    href_lower = href.lower()
                    if not href.startswith('http'): continue
                    if any(ex in href_lower for ex in exclude): continue
                    if base_url.rstrip('/') == href.rstrip('/'): continue
                    
                    if any(k in href_lower for k in keywords) or (href_lower.endswith('.html') and len(href) > 40):
                        valid_links.append(href)
                
                # Rimuovi duplicati e prendi i primi 2
                product_links = list(set(valid_links))[:2]
                
                if not product_links:
                    # IL SONAR: Estraiamo le prime 150 lettere visibili a schermo per capire COSA stiamo guardando
                    try:
                        visible_text = page.evaluate("document.body.innerText.substring(0, 150)")
                        visible_text = visible_text.replace('\n', ' ').strip()
                    except:
                        visible_text = "Impossibile leggere il testo"
                        
                    error_log += f" [WALL DETECTED: Titolo='{page_title}' | Testo Visibile='{visible_text}'] "
                    
            except Exception as e:
                error_log = f"Errore navigazione base: {str(e)}"
                return False, "Sconosciuta", error_log
                
            for p_url in product_links:
                try:
                    page.goto(p_url, timeout=30000, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000) 
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight/2)")
                    page.wait_for_timeout(2000)
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
    return "✅ Server VIVO."

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
