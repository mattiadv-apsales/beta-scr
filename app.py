from flask import Flask, render_template, request, jsonify, send_file
import asyncio
import re
import json
import csv
import io
import os
from datetime import datetime
from urllib.parse import urlparse, urljoin, quote_plus, parse_qs, unquote
import random
import logging
from playwright.async_api import async_playwright
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
]

def get_random_user_agent():
    return random.choice(USER_AGENTS)

def decode_fb_redirect(href):
    """Decodifica redirect Facebook"""
    try:
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        for key in ('u', 'url', 'href'):
            if key in qs and qs[key]:
                return unquote(qs[key][0])
    except:
        pass
    return href

def is_valid_lead_url(url):
    """FILTRO STRICT - blocca social, forms, utility"""
    if not url or not url.startswith('http'):
        return False
    
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        path = parsed.path.lower()
        url_lower = url.lower()
        
        # BLOCCO ESTESO
        blocked_domains = [
            # Social Network
            'facebook.com', 'fb.com', 'fb.me', 'fbcdn.net',
            'instagram.com', 'cdninstagram.com',
            'twitter.com', 'x.com', 't.co',
            'youtube.com', 'youtu.be',
            'tiktok.com', 'linkedin.com/in/',
            'pinterest.com', 'reddit.com',
            
            # Form Builders
            'forms.gle', 'google.com/forms', 'docs.google.com/forms',
            'typeform.com', 'surveymonkey.com', 'jotform.com',
            
            # URL Shorteners
            'bit.ly', 'goo.gl', 'ow.ly', 'tinyurl.com',
            
            # Meta Support
            'metastatus.com', 'transparency.fb.com',
            'support.', 'help.', 'policies.', 'terms.', 
            'privacy.', 'legal.', 'cookie.',
        ]
        
        if any(blocked in domain or blocked in url_lower for blocked in blocked_domains):
            return False
        
        blocked_paths = ['/login', '/signin', '/register', '/auth', '/signup']
        if any(blocked in path for blocked in blocked_paths):
            return False
        
        if '.' not in domain or domain.startswith('localhost'):
            return False
        
        return True
    except:
        return False

def extract_emails(text):
    pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    emails = re.findall(pattern, text)
    valid = [e for e in set(emails) if not any(x in e.lower() for x in ['example.com', 'test.com', 'domain.com'])]
    return valid[:5]

def extract_phones(text):
    patterns = [
        r'\+39[\s-]?\d{2,3}[\s-]?\d{3,4}[\s-]?\d{3,4}',
        r'\b0\d{1,3}[\s-]?\d{3,4}[\s-]?\d{3,4}\b',
        r'\b3\d{2}[\s-]?\d{3}[\s-]?\d{3,4}\b',
    ]
    phones = []
    for pattern in patterns:
        phones.extend([p for p in re.findall(pattern, text) if len(re.sub(r'\D', '', p)) >= 9])
    return list(set(phones))[:5]

def analyze_sentiment_tone(text):
    if not text or len(text) < 50:
        return {'sentiment_score': 0, 'tone': 'unknown', 'professionalism': 0, 'persuasiveness': 0}
    
    text_lower = text.lower()
    
    positive = ['eccellente', 'ottimo', 'migliore', 'garantito', 'qualità', 'professionale']
    negative = ['problema', 'difficoltà', 'errore']
    
    pos = sum(1 for w in positive if w in text_lower)
    neg = sum(1 for w in negative if w in text_lower)
    
    tone = 'commercial' if any(w in text_lower for w in ['acquista', 'offerta', 'sconto']) else 'neutral'
    
    return {
        'sentiment_score': min((pos - neg + 5), 10),
        'tone': tone,
        'professionalism': min(sum(1 for w in ['esperienza', 'team'] if w in text_lower) * 2, 10),
        'persuasiveness': min(sum(1 for w in ['prova', 'scopri', 'gratis'] if w in text_lower) * 2, 10)
    }

def calculate_copy_quality(text):
    if not text or len(text) < 50:
        return 0
    score = 2 if 100 < len(text) < 2000 else 0
    text_lower = text.lower()
    score += 3 if sum(1 for w in ['acquista', 'prova'] if w in text_lower) >= 2 else 0
    score += 2 if any(w in text_lower for w in ['ora', 'limitato']) else 0
    return min(score, 10)

async def analyze_landing_page(url, session):
    """Analisi landing page COMPLETA ma veloce"""
    try:
        logger.info(f"🔍 Analyzing: {url}")
        
        async with session.get(
            url, 
            timeout=aiohttp.ClientTimeout(total=8),
            headers={'User-Agent': get_random_user_agent()},
            allow_redirects=True
        ) as response:
            if response.status != 200:
                return None
            
            html = await response.text()
            soup = BeautifulSoup(html, 'html.parser')
            text = soup.get_text(separator=' ', strip=True)
            
            emails = extract_emails(text)
            phones = extract_phones(text)
            
            contact_links = []
            for link in soup.find_all('a', href=True)[:100]:
                href = link['href'].lower()
                link_text = link.get_text().lower()
                
                if any(kw in href or kw in link_text for kw in ['contatt', 'chi-siamo', 'about']):
                    full_url = urljoin(url, link['href'])
                    if is_valid_lead_url(full_url):
                        contact_links.append(full_url)
            
            has_form = bool(soup.find('form'))
            has_schema = bool(soup.find('script', type='application/ld+json'))
            
            cta_elements = soup.find_all(['button', 'a'], class_=re.compile(r'cta|button|btn', re.I))
            cta_elements += soup.find_all(['a', 'button'], string=re.compile(r'contatt|acquista|prenota', re.I))
            has_cta = len(cta_elements) > 0
            
            copy_quality = calculate_copy_quality(text[:2000])
            sentiment = analyze_sentiment_tone(text[:2000])
            
            score = 0
            if emails: score += 3
            if phones: score += 3
            if contact_links: score += 2
            if has_form: score += 2
            if has_cta: score += 1
            if copy_quality >= 5: score += 1
            
            return {
                'emails': emails,
                'phones': phones,
                'contact_links': contact_links[:3],
                'has_form': has_form,
                'has_schema': has_schema,
                'has_cta': has_cta,
                'copy_quality_score': copy_quality,
                'sentiment_score': sentiment['sentiment_score'],
                'tone': sentiment['tone'],
                'professionalism': sentiment['professionalism'],
                'persuasiveness': sentiment['persuasiveness'],
                'lead_score': min(score, 10)
            }
    except:
        return None

async def scrape_meta_ads_advanced(query, max_results=20):
    """Meta Ads - 6 SCROLL + traccia fonte ads"""
    logger.info(f"🎯 META ADS: '{query}'")
    results = []
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=get_random_user_agent(),
                viewport={'width': 1920, 'height': 1080}
            )
            page = await context.new_page()
            
            search_url = (
                f"https://www.facebook.com/ads/library/"
                f"?active_status=active&ad_type=all&country=IT"
                f"&q={quote_plus(query)}&search_type=keyword_unordered"
            )
            
            await page.goto(search_url, timeout=40000, wait_until='domcontentloaded')
            await page.wait_for_timeout(3000)
            
            # Traccia URL + fonte ads
            leads_with_source = {}  # {url: ad_id}
            
            for scroll in range(6):
                logger.info(f"📜 Meta {scroll+1}/6")
                
                content = await page.content()
                soup = BeautifulSoup(content, 'html.parser')
                
                # Trova annunci con contenitore
                ad_containers = soup.find_all('div', class_=re.compile(r'x1yztbdb|xh8yej3'))
                
                for container in ad_containers:
                    # Cerca ID annuncio (per tracciare fonte)
                    ad_id = None
                    ad_link = container.find('a', href=re.compile(r'ad_id='))
                    if ad_link:
                        match = re.search(r'ad_id=(\d+)', ad_link.get('href', ''))
                        if match:
                            ad_id = match.group(1)
                    
                    # Estrai link esterni
                    links = container.find_all('a', href=True)
                    for link in links:
                        href = link.get('href', '')
                        
                        if 'l.facebook.com' in href or 'l.php' in href:
                            href = decode_fb_redirect(href)
                        
                        if href.startswith('http') and is_valid_lead_url(href):
                            # Associa URL a fonte ads
                            if href not in leads_with_source:
                                ad_source = f"https://www.facebook.com/ads/library/?id={ad_id}" if ad_id else None
                                leads_with_source[href] = ad_source
                                logger.info(f"✅ Meta lead: {href}")
                
                # Estrai anche link generici (fallback)
                for link in soup.find_all('a', href=True):
                    href = link.get('href', '')
                    
                    if 'l.facebook.com' in href or 'l.php' in href:
                        href = decode_fb_redirect(href)
                    
                    if href.startswith('http') and is_valid_lead_url(href):
                        if href not in leads_with_source:
                            leads_with_source[href] = None
                            logger.info(f"✅ Meta lead (generic): {href}")
                
                if len(leads_with_source) >= max_results:
                    break
                
                await page.evaluate('window.scrollBy(0, 1200)')
                await page.wait_for_timeout(1500)
            
            await browser.close()
            
            for url, ad_source in list(leads_with_source.items())[:max_results]:
                results.append({
                    'platform': 'Meta Ads Italy',
                    'url': url,
                    'query': query,
                    'found_at': datetime.now().isoformat(),
                    'ad_source': ad_source  # LINK AD
                })
    
    except Exception as e:
        logger.error(f"❌ Meta: {str(e)}")
    
    logger.info(f"✅ META: {len(results)} lead")
    return results

async def scrape_google_italy(query, max_results=15):
    """Google - 2 PAGINE per più risultati"""
    logger.info(f"🔍 GOOGLE: '{query}'")
    results = []
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=get_random_user_agent(),
                locale='it-IT'
            )
            page = await context.new_page()
            
            seen = set()
            
            # 2 PAGINE
            for page_num in range(2):
                search_query = f"{query} italia azienda servizio"
                start = page_num * 10
                url = f"https://www.google.it/search?q={quote_plus(search_query)}&num=20&start={start}&hl=it&gl=it"
                
                try:
                    await page.goto(url, timeout=25000, wait_until='domcontentloaded')
                    await page.wait_for_timeout(2000)
                    
                    await page.evaluate('window.scrollBy(0, 1000)')
                    await page.wait_for_timeout(1500)
                    
                    content = await page.content()
                    soup = BeautifulSoup(content, 'html.parser')
                    
                    search_results = soup.find_all('div', class_='g')
                    
                    for result in search_results:
                        link_elem = result.find('a', href=True)
                        if link_elem:
                            href = link_elem['href']
                            
                            if href.startswith('/url?q='):
                                href = href.split('/url?q=')[1].split('&')[0]
                            
                            if is_valid_lead_url(href):
                                domain = urlparse(href).netloc
                                if domain not in seen:
                                    seen.add(domain)
                                    results.append({
                                        'platform': 'Google Italy',
                                        'url': href,
                                        'query': query,
                                        'found_at': datetime.now().isoformat(),
                                        'ad_source': None
                                    })
                                    logger.info(f"✅ Google lead: {href}")
                        
                        if len(results) >= max_results:
                            break
                    
                    if len(results) >= max_results:
                        break
                
                except Exception as e:
                    logger.warning(f"⚠️ Google page {page_num + 1} error: {str(e)}")
            
            await browser.close()
    
    except Exception as e:
        logger.error(f"❌ Google: {str(e)}")
    
    logger.info(f"✅ GOOGLE: {len(results)} lead")
    return results

async def scrape_all_platforms(query):
    """Orchestrazione - TARGET 30-35 LEAD"""
    logger.info(f"🚀 START: '{query}'")
    
    tasks = [
        scrape_meta_ads_advanced(query, 20),
        scrape_google_italy(query, 15)
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    all_leads = []
    for platform_results in results:
        if isinstance(platform_results, list):
            all_leads.extend(platform_results)
    
    # Deduplica
    seen_urls = set()
    unique_leads = []
    for lead in all_leads:
        if lead['url'] not in seen_urls:
            seen_urls.add(lead['url'])
            unique_leads.append(lead)
    
    logger.info(f"📊 Lead unici: {len(unique_leads)}")
    
    # Analisi in batch da 10
    async with aiohttp.ClientSession() as session:
        final_leads = []
        
        for i in range(0, len(unique_leads), 10):
            batch = unique_leads[i:i+10]
            analyses = await asyncio.gather(
                *[analyze_landing_page(lead['url'], session) for lead in batch],
                return_exceptions=True
            )
            
            for lead, analysis in zip(batch, analyses):
                if isinstance(analysis, dict) and analysis:
                    lead.update(analysis)
                else:
                    lead.update({
                        'emails': [], 'phones': [], 'contact_links': [],
                        'has_form': False, 'has_schema': False, 'has_cta': False,
                        'copy_quality_score': 0, 'sentiment_score': 0,
                        'tone': 'unknown', 'professionalism': 0,
                        'persuasiveness': 0, 'lead_score': 0
                    })
                final_leads.append(lead)
    
    final_leads.sort(key=lambda x: x.get('lead_score', 0), reverse=True)
    
    logger.info(f"✨ FINAL: {len(final_leads)} lead")
    return final_leads

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
        
        logger.info(f"🚀 API: '{query}'")
        
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            results = loop.run_until_complete(scrape_all_platforms(query))
        finally:
            loop.close()
        
        scraped_data = results
        
        return jsonify({
            'success': True,
            'count': len(results),
            'leads': results
        })
        
    except Exception as e:
        logger.error(f"❌ ERROR: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'count': 0,
            'leads': []
        }), 500

@app.route('/export/csv')
def export_csv():
    global scraped_data
    
    if not scraped_data:
        return jsonify({'error': 'Nessun dato'}), 400
    
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        'platform', 'url', 'ad_source', 'query', 'found_at', 'emails', 'phones',
        'contact_links', 'has_form', 'has_schema', 'has_cta',
        'copy_quality_score', 'sentiment_score', 'tone', 
        'professionalism', 'persuasiveness', 'lead_score'
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
        download_name=f'leads_{datetime.now().strftime("%Y%m%d_%H%M")}.csv'
    )

@app.route('/export/json')
def export_json():
    global scraped_data
    
    if not scraped_data:
        return jsonify({'error': 'Nessun dato'}), 400
    
    return send_file(
        io.BytesIO(json.dumps(scraped_data, indent=2).encode('utf-8')),
        mimetype='application/json',
        as_attachment=True,
        download_name=f'leads_{datetime.now().strftime("%Y%m%d_%H%M")}.json'
    )

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)