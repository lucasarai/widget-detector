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

def extract_products(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    links = []
    
    # Keyword classiche (incluso pattern Sephora e Salesforce)
    keywords = ['/product/', '/products/', '/prodotto/', '/prodotti/', '/p/', '/item/', '/sku/', '-p-']
    
    # Pagine da escludere assolutamente (per evitare di finire nel carrello o privacy)
    exclude = ['/cart', '/login', '/account', '/contact', '/checkout', '/wishlist', '/category/', '/brand/', '/privacy', '/terms', '/faq', '/store-locator', '/resi', '/categorie/']
    
    valid_anchors = []
    
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        href_lower = href.lower()
        
        # Ignora ancore interne o script
        if href.startswith('#') or href.startswith('javascript:') or href.startswith('mailto:'):
            continue
            
        if any(ex in href_lower for ex in exclude):
            continue
            
        full_url = urljoin(base_url, href)
        
        # Ignora i link che rimandano alla homepage stessa
        if full_url.rstrip('/') == base_url.rstrip('/'):
            continue
            
        valid_anchors.append(full_url)
        
        # 1. Match Classico (Parole chiave)
        if any(k in href_lower for k in keywords):
            links.append(full_url)
            continue
            
        # 2. Match Salesforce Commerce Cloud (Molti link finiscono in .html nudo e crudo)
        if href_lower.endswith('.html') and len(href) > 20:
            links.append(full_url)
            
    # 3. L'HACKER FALLBACK: La Teoria del Link Lungo
    # Se i dev hanno nascosto tutto, ordiniamo i link per lunghezza. 
    # I prodotti (es. /sauvage-eau-de-parfum-100ml-uomo-1234) sono sempre i più lunghi!
    if not links and valid_anchors:
        valid_anchors = list(set(valid_anchors))
        valid_anchors.sort(key=len, reverse=True)
        links = valid_anchors[:5] # Peschiamo dai 5 link più lunghi

    unique_links = list(set(links))
    return unique_links[:2] # Ne restituiamo 2 per i test

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
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="it-IT",
                timezone_id="Europe/Rome"
            )
            
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page = context.new_page()
            
            # --- FASE 1: HOMEPAGE E RISVEGLIO PRODOTTI ---
            try:
                page.goto(base_url, timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(2000) 
                
                # Spara al banner dei cookie
                try:
                    page.evaluate("""
                        const buttons = Array.from(document.querySelectorAll('button, a, div[role="button"]'));
                        const acceptBtn = buttons.find(b => b.innerText.match(/accetta|accept|agree|consenti|acconsento|tout accepter/i));
                        if(acceptBtn) acceptBtn.click();
                    """)
                except:
                    pass
                
                # LO SCROLL UMANO: Scorre dinamicamente giù per risvegliare i caroselli di Sephora
                try:
                    page.evaluate("""
                        () => new Promise((resolve) => {
                            let totalHeight = 0;
                            let distance = 600;
                            let timer = setInterval(() => {
                                window.scrollBy(0, distance);
                                totalHeight += distance;
                                if(totalHeight >= document.body.scrollHeight || totalHeight > 6000){
                                    clearInterval(timer);
                                    window.scrollTo(0, 0);
                                    resolve();
                                }
                            }, 250);
                        })
                    """)
                except:
                    page.wait_for_timeout(2000)
                
                homepage_html = page.content()
                combined_html += homepage_html
            except Exception as e:
                error_log = f"Errore Homepage ({base_url}): {str(e)}"
                return False, "Sconosciuta", error_log
                
            # --- FASE 2: CACCIA AI PRODOTTI ---
            product_links = extract_products(homepage_html, base_url)
            
            if not product_links:
                error_log += " [ATTENZIONE: Nessun link prodotto trovato in Homepage!] "
            
            # --- FASE 3: ANALISI DEI PRODOTTI TROVATI ---
            for p_url in product_links:
                try:
                    page.goto(p_url, timeout=30000, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000) # Aspettiamo che il widget Bazaarvoice di Sephora venga iniettato
                    
                    # Scroll per risvegliare il widget delle recensioni a fondo pagina
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight/2)")
                    page.wait_for_timeout(2000)
                    
                    combined_html += page.content()
                except Exception as e:
                    error_log += f" | Errore Prodotto ({p_url}): {str(e)}"
                    
        except Exception as global_e:
             error_log = f"Errore Fatale Browser: {str(global_e)}"
             return False, "Sconosciuta", error_log
        finally:
            browser.close()
            
    widget_presente = detect(combined_html)
    piattaforma = detect_platform(combined_html)
    
    return widget_presente, piattaforma, error_log

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
        has_widget, platform, log = check_ecommerce_optimized(url)
        response_data = {
            'widget_presente': has_widget,
            'piattaforma': platform
        }
        if log:
            response_data['internal_log'] = log.strip()
            
        return jsonify(response_data)
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
