from flask import Flask, render_template, request, jsonify, send_file
import asyncio
import re
import json
import csv
import io
from datetime import datetime, timedelta
from urllib.parse import urlparse, urljoin, quote_plus, parse_qs, unquote
import random
import logging
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from bs4 import BeautifulSoup
import aiohttp
from collections import defaultdict

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

scraped_data = []

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
]

BLOCKED_DOMAINS = {
    'facebook.com', 'fb.com', 'fbcdn.net', 'instagram.com', 'messenger.com', 'fb.me',
    'twitter.com', 'x.com', 'linkedin.com', 'reddit.com', 'youtube.com', 'youtu.be', 
    'tiktok.com', 'pinterest.com', 'bit.ly', 't.co', 'goo.gl', 'ow.ly', 'tinyurl.com',
    'support.', 'help.', 'transparency.', 'policies.', 'terms.', 'privacy.', 
    'legal.', 'cookie.', 'reddithelp.com', 'metastatus.com'
}

def get_random_user_agent():
    return random.choice(USER_AGENTS)

def decode_fb_redirect(href):
    """Decodifica redirect di Facebook (l.facebook.com)"""
    try:
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        for key in ('u', 'url', 'href'):
            if key in qs and qs[key]:
                return unquote(qs[key][0])
    except Exception:
        pass
    return href

def is_valid_lead_url(url):
    """Verifica che sia un URL valido per un lead aziendale ITALIANO"""
    if not url or not url.startswith('http'):
        return False
    
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        path = parsed.path.lower()
        
        # Blocca domini social e utility (logica dal tuo script)
        if any(blocked in domain for blocked in BLOCKED_DOMAINS):
            return False
        
        # Blocca path di utility
        blocked_paths = ['/login', '/signin', '/signup', '/register', '/auth', 
                        '/terms', '/privacy', '/cookie', '/legal', '/support',
                        '/help', '/faq', '/about-us/contact', '/contact-us/support']
        if any(blocked in path for blocked in blocked_paths):
            return False
        
        # Deve avere un dominio valido
        if '.' not in domain or domain.startswith('localhost'):
            return False
        
        # ✅ FILTRO ITALIA: Domini .it o path italiani
        is_italian = (
            domain.endswith('.it') or
            '/it/' in path or
            '/italia/' in path or
            any(city in domain for city in ['roma', 'milano', 'torino', 'napoli', 'firenze', 'bologna', 'venezia', 'palermo'])
        )
        
        if not is_italian:
            return False
            
        return True
    except:
        return False

def extract_emails(text):
    pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    emails = re.findall(pattern, text)
    return [e for e in set(emails) if not any(x in e.lower() for x in ['example.com', 'test.com', 'domain.com'])]

def extract_phones(text):
    patterns = [
        r'\+39[\s-]?\d{2,3}[\s-]?\d{3,4}[\s-]?\d{3,4}',  # Italiano +39
        r'\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,4}[-.\s]?\d{1,9}',
        r'\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b',
    ]
    phones = []
    for pattern in patterns:
        found = re.findall(pattern, text)
        phones.extend([p for p in found if len(re.sub(r'\D', '', p)) >= 9])
    return list(set(phones))[:5]

def detect_cms(html_content):
    """Rileva CMS/piattaforma (logica dal tuo script migliorata)"""
    if not html_content:
        return "Unknown"
    
    html = html_content.lower()
    
    # Generator meta tag
    match = re.search(r'<meta\s+name=["\']generator["\']\s+content=["\']([^"\']+)["\']', html)
    if match:
        gen = match.group(1).lower()
        if "wordpress" in gen: return "WordPress"
        elif "shopify" in gen: return "Shopify"
        elif "wix" in gen: return "Wix"
        elif "lovable" in gen: return "Lovable"
        elif "framer" in gen: return "Framer"
        elif "squarespace" in gen: return "Squarespace"
        else: return match.group(1).strip()[:50]
    
    # Pattern comuni
    if "wp-content" in html or "wp-includes" in html: return "WordPress"
    if "cdn.shopify.com" in html or "myshopify.com" in html: return "Shopify"
    if "wixstatic.com" in html: return "Wix"
    if "squarespace.com" in html: return "Squarespace"
    if "joomla" in html: return "Joomla"
    if "drupal" in html: return "Drupal"
    if "lovable.app" in html or "lovable.dev" in html: return "Lovable"
    if "framerusercontent.com" in html or "framer.website" in html: return "Framer"
    if "webflow" in html: return "Webflow"
    if "<html" in html and "</html>" in html: return "Custom/HTML"
    
    return "Unknown"

def calculate_copy_quality(text):
    if not text or len(text) < 50:
        return 0
    
    score = 0
    text_lower = text.lower()
    
    if 100 < len(text) < 2000:
        score += 2
    
    cta_words = ['acquista', 'compra', 'prova', 'inizia', 'iscriviti', 'scarica', 'scopri', 'contatta', 'richiedi', 'prenota']
    if sum(1 for word in cta_words if word in text_lower) >= 2:
        score += 3
    
    benefit_words = ['risparmia', 'aumenta', 'migliora', 'ottimizza', 'facile', 'veloce', 'professionale', 'garantito']
    if sum(1 for word in benefit_words if word in text_lower) >= 2:
        score += 3
    
    urgency_words = ['ora', 'oggi', 'limitato', 'offerta', 'scadenza']
    if any(word in text_lower for word in urgency_words):
        score += 2
    
    return min(score, 10)

def extract_date_from_ad(text):
    """Estrai data pubblicazione annuncio (logica dal tuo script)"""
    try:
        match = re.search(r"Data di inizio della pubblicazione:\s*(\d{1,2})\s([a-z]{3})\s(\d{4})", text, re.IGNORECASE)
        if not match:
            return None
        giorno = int(match.group(1))
        mese_str = match.group(2).lower()
        anno = int(match.group(3))
        mesi = {"gen":1,"feb":2,"mar":3,"apr":4,"mag":5,"giu":6,"lug":7,"ago":8,"set":9,"ott":10,"nov":11,"dic":12}
        mese = mesi.get(mese_str)
        if not mese:
            return None
        return datetime(anno, mese, giorno).date()
    except:
        return None

async def analyze_landing_page(url, session):
    try:
        logger.info(f"Analyzing: {url}")
        
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20), 
                              headers={'User-Agent': get_random_user_agent()},
                              allow_redirects=True) as response:
            if response.status != 200:
                return None
            
            html = await response.text()
            soup = BeautifulSoup(html, 'html.parser')
            
            text = soup.get_text(separator=' ', strip=True)
            
            emails = extract_emails(text)
            phones = extract_phones(text)
            cms = detect_cms(html)
            
            contact_links = []
            for link in soup.find_all('a', href=True):
                href = link['href'].lower()
                link_text = link.get_text().lower()
                if any(kw in href or kw in link_text for kw in ['contatt', 'chi-siamo', 'about']):
                    full_url = urljoin(url, link['href'])
                    if is_valid_lead_url(full_url):
                        contact_links.append(full_url)
            
            has_form = bool(soup.find('form'))
            has_schema = bool(soup.find('script', type='application/ld+json'))
            
            cta_elements = soup.find_all(['button', 'a'], class_=re.compile(r'cta|button|btn', re.I))
            cta_elements += soup.find_all(['a', 'button'], string=re.compile(r'contatt|acquista|prenota|richiedi', re.I))
            has_cta = len(cta_elements) > 0
            
            copy_quality = calculate_copy_quality(text[:3000])
            
            score = 0
            if emails: score += 4
            if phones: score += 3
            if contact_links: score += 2
            if has_form: score += 1
            
            return {
                'url': url,
                'emails': emails[:5],
                'phones': phones[:5],
                'contact_links': contact_links[:3],
                'cms': cms,
                'has_form': has_form,
                'has_schema': has_schema,
                'has_cta': has_cta,
                'copy_quality_score': copy_quality,
                'lead_score': min(score, 10)
            }
    except Exception as e:
        logger.error(f"Error analyzing {url}: {str(e)}")
        return None

async def scrape_meta_ads_advanced(query, max_results=100):
    """Scrape Meta Ads con logica avanzata dal tuo script"""
    logger.info(f"🎯 Scraping Meta Ads Library (Advanced) for: {query}")
    results = []
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=get_random_user_agent(),
                viewport={'width': 1920, 'height': 1080},
                locale='it-IT'
            )
            page = await context.new_page()
            
            # URL con ricerca esatta frase (come nel tuo script)
            search_url = f"https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=IT&q={quote_plus(query)}&search_type=keyword_unordered"
            
            logger.info(f"📍 Navigating to: {search_url}")
            await page.goto(search_url, timeout=45000, wait_until='domcontentloaded')
            await page.wait_for_timeout(5000)
            
            external_urls = set()
            empty_scrolls = 0
            max_empty_scrolls = 5
            oggi = datetime.today().date()
            ieri = oggi - timedelta(days=1)
            
            # Scroll intelligente con stop su annunci vecchi
            for scroll_num in range(20):  # Max 20 scroll
                logger.info(f"📜 Scroll {scroll_num + 1}...")
                
                # Trova annunci
                content = await page.content()
                soup = BeautifulSoup(content, 'html.parser')
                
                # Trova container annunci (selector dal tuo script)
                annunci = soup.find_all('div', class_='xh8yej3')
                
                before_count = len(external_urls)
                stop_scraping = False
                
                for annuncio in annunci:
                    # Estrai data pubblicazione
                    data_pub = extract_date_from_ad(annuncio.get_text())
                    
                    if data_pub and data_pub < ieri:
                        logger.info(f"⏹️ Annuncio del {data_pub}, più vecchio di ieri. Stop.")
                        stop_scraping = True
                        break
                    
                    # Estrai tutti i link
                    links = annuncio.find_all('a', href=True)
                    for link in links:
                        href = link.get('href', '')
                        
                        # Decodifica redirect Facebook
                        if 'l.facebook.com' in href or 'l.php' in href:
                            href = decode_fb_redirect(href)
                        
                        if is_valid_lead_url(href):
                            external_urls.add(href)
                
                if stop_scraping:
                    break
                
                after_count = len(external_urls)
                new_count = after_count - before_count
                
                logger.info(f"✅ Totale: {after_count} lead (+{new_count} nuovi)")
                
                if after_count == before_count:
                    empty_scrolls += 1
                else:
                    empty_scrolls = 0
                
                if empty_scrolls >= max_empty_scrolls:
                    logger.info("🏁 Nessun nuovo lead, fine scroll")
                    break
                
                if len(external_urls) >= max_results:
                    logger.info(f"🎯 Raggiunto limite {max_results} lead")
                    break
                
                # Scroll
                await page.evaluate('window.scrollBy(0, 800)')
                await page.wait_for_timeout(2000)
            
            await browser.close()
            
            # Converti in formato results
            for url in list(external_urls)[:max_results]:
                results.append({
                    'platform': 'Meta Ads Italy',
                    'url': url,
                    'query': query,
                    'found_at': datetime.now().isoformat()
                })
            
    except Exception as e:
        logger.error(f"Meta Ads error: {str(e)}")
    
    logger.info(f"🎉 Meta Ads: {len(results)} lead italiani trovati")
    return results

async def scrape_google_italy(query, max_results=50):
    """Scrape Google.it per aziende italiane"""
    logger.info(f"🔍 Google Search Italy for: {query}")
    results = []
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=get_random_user_agent(),
                locale='it-IT'
            )
            page = await context.new_page()
            
            commercial_query = f"{query} azienda servizio italia site:.it"
            search_url = f"https://www.google.it/search?q={quote_plus(commercial_query)}&num=50&hl=it&gl=it"
            
            await page.goto(search_url, timeout=30000)
            await page.wait_for_timeout(3000)
            
            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')
            
            seen_domains = defaultdict(int)
            search_results = soup.find_all('div', class_='g')
            
            for result in search_results:
                link_elem = result.find('a', href=True)
                if link_elem:
                    href = link_elem['href']
                    
                    if href.startswith('/url?q='):
                        href = href.split('/url?q=')[1].split('&')[0]
                    
                    if is_valid_lead_url(href):
                        domain = urlparse(href).netloc
                        
                        if seen_domains[domain] < 3:
                            results.append({
                                'platform': 'Google Italy',
                                'url': href,
                                'query': query,
                                'found_at': datetime.now().isoformat()
                            })
                            seen_domains[domain] += 1
                
                if len(results) >= max_results:
                    break
            
            await browser.close()
            
    except Exception as e:
        logger.error(f"Google error: {str(e)}")
    
    logger.info(f"🔍 Google: {len(results)} lead trovati")
    return results

async def scrape_all_platforms(query):
    """Scrape con priorità Meta Ads (logica avanzata)"""
    
    # Meta Ads è il più performante, aumentiamo limite
    tasks = [
        scrape_meta_ads_advanced(query, max_results=100),
        scrape_google_italy(query, max_results=50)
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    all_leads = []
    for platform_results in results:
        if isinstance(platform_results, list):
            all_leads.extend(platform_results)
        else:
            logger.error(f"Platform error: {platform_results}")
    
    # Rimuovi duplicati
    seen_urls = set()
    unique_leads = []
    for lead in all_leads:
        if lead['url'] not in seen_urls:
            seen_urls.add(lead['url'])
            unique_leads.append(lead)
    
    logger.info(f"📊 Unique leads: {len(unique_leads)}")
    
    # Analisi landing pages
    async with aiohttp.ClientSession() as session:
        analysis_tasks = [analyze_landing_page(lead['url'], session) for lead in unique_leads]
        analyses = await asyncio.gather(*analysis_tasks, return_exceptions=True)
        
        enriched_leads = []
        for lead, analysis in zip(unique_leads, analyses):
            if isinstance(analysis, dict) and analysis:
                lead.update(analysis)
                enriched_leads.append(lead)
    
    # Ordina per score
    enriched_leads.sort(key=lambda x: x.get('lead_score', 0), reverse=True)
    
    # Filtra quality leads
    quality_leads = [l for l in enriched_leads if l.get('lead_score', 0) > 0]
    
    logger.info(f"✨ Final quality leads: {len(quality_leads)}")
    return quality_leads

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/scrape', methods=['POST'])
def scrape():
    global scraped_data
    
    try:
        data = request.get_json()
        query = data.get('query', '').strip()
        
        if not query:
            return jsonify({'error': 'Query richiesta'}), 400
        
        logger.info(f"🚀 Starting advanced scrape for: {query}")
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        results = loop.run_until_complete(scrape_all_platforms(query))
        loop.close()
        
        scraped_data = results
        
        return jsonify({
            'success': True,
            'count': len(results),
            'leads': results
        })
        
    except Exception as e:
        logger.error(f"Scrape error: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/export/csv')
def export_csv():
    global scraped_data
    
    if not scraped_data:
        return jsonify({'error': 'Nessun dato da esportare'}), 400
    
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        'platform', 'url', 'query', 'found_at', 'cms', 'emails', 'phones',
        'contact_links', 'has_form', 'has_schema', 'has_cta',
        'copy_quality_score', 'lead_score'
    ])
    
    writer.writeheader()
    for row in scraped_data:
        row_copy = row.copy()
        row_copy['emails'] = '; '.join(row_copy.get('emails', []))
        row_copy['phones'] = '; '.join(row_copy.get('phones', []))
        row_copy['contact_links'] = '; '.join(row_copy.get('contact_links', []))
        writer.writerow(row_copy)
    
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'leads_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    )

@app.route('/export/json')
def export_json():
    global scraped_data
    
    if not scraped_data:
        return jsonify({'error': 'Nessun dato da esportare'}), 400
    
    return send_file(
        io.BytesIO(json.dumps(scraped_data, indent=2).encode('utf-8')),
        mimetype='application/json',
        as_attachment=True,
        download_name=f'leads_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    )

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)