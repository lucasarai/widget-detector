from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import json

app = Flask(__name__)

PROVIDERS = [
    "trustpilot.com", "yotpo.com", "reviews.io", 
    "stamped.io", "okendo.io", "bazaarvoice.com", "judge.me"
]

def fetch_rendered(url):
    html = ""
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
            page.goto(url, timeout=15000)
            page.wait_for_timeout(2000) 
            html = page.content()
        except Exception as e:
            print(f"Errore caricamento {url}: {str(e)}")
        finally:
            browser.close()
            
    return html

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

def check(url):
    homepage = fetch_rendered(url)
    if not homepage:
        return False
        
    product_links = extract_products(homepage, url)
    combined = homepage
    
    for p in product_links:
        combined += fetch_rendered(p)
    
    return detect(combined)

# --- LA SPIA LUMINOSA (Verifica se il server è online dal browser) ---
@app.route('/', methods=['GET'])
def home():
    return "✅ Il server Python è VIVO e funzionante! Il motore Anti-Bot è pronto."

# --- API PER n8n (Accetta sia con che senza slash finale) ---
@app.route('/api/check', methods=['POST'])
@app.route('/api/check/', methods=['POST'])
def api_single_check():
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({'widget_presente': False, 'error': 'URL mancante'}), 400
    
    url = data['url'].strip()
    if not url.startswith('http://') and not url.startswith('https://'):
        url = 'https://' + url
    
    try:
        result = check(url)
        return jsonify({
            'widget_presente': bool(result)
        })
    except Exception as e:
        return jsonify({
            'widget_presente': False,
            'error': str(e)
        }), 500

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 8000))
    app.run(debug=False, host='0.0.0.0', port=port)
