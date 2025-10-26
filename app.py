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
    'twitter.com', 'x.com', 'reddit.com', 'youtube.com', 'youtu.be', 
    'tiktok.com', 'pinterest.com', 'bit.ly', 't.co', 'goo.gl', 'ow.ly', 'tinyurl.com',
    'support.', 'help.', 'transparency.', 'policies.', 'terms.', 'privacy.', 
    'legal.', 'cookie.', 'reddithelp.com', 'metastatus.com'
}

ITALIAN_CITIES = [
    'roma', 'milano', 'torino', 'napoli', 'firenze', 'bologna', 
    'venezia', 'palermo', 'genova', 'bari', 'catania', 'verona'
]

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
    """Verifica che sia un URL valido - FILTRO SOFT"""
    if not url or not url.startswith('http'):
        return False
    
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        path = parsed.path.lower()
        
        # Blocca SOLO social network principali
        blocked_social = ['facebook.com', 'fb.com', 'instagram.com', 'twitter.com', 
                         'x.com', 'youtube.com', 'tiktok.com', 'linkedin.com/in/']
        if any(blocked in domain for blocked in blocked_social):
            return False
        
        # Blocca SOLO path pericolosi
        blocked_paths = ['/login', '/signin', '/register', '/auth']
        if any(blocked in path for blocked in blocked_paths):
            return False
        
        # Deve avere un dominio valido
        if '.' not in domain or domain.startswith('localhost'):
            return False
        
        # ACCETTA TUTTO - nessun filtro geografico restrittivo
        return True
            
    except:
        return False

def extract_emails(text):
    """Estrai email valide"""
    pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    emails = re.findall(pattern, text)
    valid_emails = [
        e for e in set(emails) 
        if not any(x in e.lower() for x in ['example.com', 'test.com', 'domain.com', 'mail.com'])
    ]
    return valid_emails[:5]

def extract_phones(text):
    """Estrai numeri di telefono italiani e internazionali"""
    patterns = [
        r'\+39[\s-]?\d{2,3}[\s-]?\d{3,4}[\s-]?\d{3,4}',
        r'\b0\d{1,3}[\s-]?\d{3,4}[\s-]?\d{3,4}\b',
        r'\b3\d{2}[\s-]?\d{3}[\s-]?\d{3,4}\b',
        r'\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}',
    ]
    phones = []
    for pattern in patterns:
        found = re.findall(pattern, text)
        phones.extend([p for p in found if len(re.sub(r'\D', '', p)) >= 9])
    return list(set(phones))[:5]

def analyze_sentiment_tone(text):
    """Analisi sentiment e tone of voice (0-10 per categoria)"""
    if not text or len(text) < 50:
        return {
            'sentiment_score': 0,
            'tone': 'unknown',
            'professionalism': 0,
            'persuasiveness': 0
        }
    
    text_lower = text.lower()
    
    # SENTIMENT (positivo/negativo)
    positive_words = ['eccellente', 'ottimo', 'migliore', 'garantito', 'successo', 
                     'innovativo', 'leader', 'qualità', 'professionale', 'affidabile']
    negative_words = ['problema', 'difficoltà', 'errore', 'fallimento', 'negativo']
    
    pos_count = sum(1 for word in positive_words if word in text_lower)
    neg_count = sum(1 for word in negative_words if word in text_lower)
    
    sentiment_score = min((pos_count - neg_count + 5), 10)
    
    # TONE OF VOICE
    formal_indicators = ['inoltre', 'pertanto', 'qualora', 'mediante', 'gentile cliente']
    casual_indicators = ['ciao', 'hey', 'dai', 'fantastico', 'wow']
    commercial_indicators = ['acquista', 'offerta', 'sconto', 'risparmia', 'promo']
    
    formal_count = sum(1 for word in formal_indicators if word in text_lower)
    casual_count = sum(1 for word in casual_indicators if word in text_lower)
    commercial_count = sum(1 for word in commercial_indicators if word in text_lower)
    
    if commercial_count > formal_count and commercial_count > casual_count:
        tone = 'commercial'
    elif formal_count > casual_count:
        tone = 'formal'
    elif casual_count > 0:
        tone = 'casual'
    else:
        tone = 'neutral'
    
    # PROFESSIONALITÀ (0-10)
    prof_indicators = ['esperienza', 'competenza', 'certificato', 'qualificato', 
                      'professionale', 'team', 'consulenza']
    professionalism = min(sum(1 for word in prof_indicators if word in text_lower) * 2, 10)
    
    # PERSUASIVITÀ (0-10)
    persuasive_indicators = ['prova', 'scopri', 'richiedi', 'garantito', 'limitato', 
                            'esclusivo', 'gratis', 'ora']
    persuasiveness = min(sum(1 for word in persuasive_indicators if word in text_lower) * 1.5, 10)
    
    return {
        'sentiment_score': max(0, sentiment_score),
        'tone': tone,
        'professionalism': professionalism,
        'persuasiveness': int(persuasiveness)
    }

def calculate_copy_quality(text):
    """Valuta qualità del copy (0-10)"""
    if not text or len(text) < 50:
        return 0
    
    score = 0
    text_lower = text.lower()
    
    if 100 < len(text) < 2000:
        score += 2
    
    cta_words = ['acquista', 'compra', 'prova', 'inizia', 'iscriviti', 
                 'scarica', 'scopri', 'contatta', 'richiedi', 'prenota']
    cta_count = sum(1 for word in cta_words if word in text_lower)
    if cta_count >= 2:
        score += 3
    
    benefit_words = ['risparmia', 'aumenta', 'migliora', 'ottimizza', 
                    'facile', 'veloce', 'professionale', 'garantito', 'gratis']
    benefit_count = sum(1 for word in benefit_words if word in text_lower)
    if benefit_count >= 2:
        score += 3
    
    urgency_words = ['ora', 'oggi', 'limitato', 'offerta', 'scadenza', 'solo']
    if any(word in text_lower for word in urgency_words):
        score += 2
    
    return min(score, 10)

def extract_date_from_ad(text):
    """Estrai data pubblicazione annuncio Meta"""
    try:
        match = re.search(
            r"Data di inizio della pubblicazione:\s*(\d{1,2})\s([a-z]{3})\s(\d{4})", 
            text, re.IGNORECASE
        )
        if not match:
            return None
        
        giorno = int(match.group(1))
        mese_str = match.group(2).lower()
        anno = int(match.group(3))
        
        mesi = {
            "gen":1, "feb":2, "mar":3, "apr":4, "mag":5, "giu":6,
            "lug":7, "ago":8, "set":9, "ott":10, "nov":11, "dic":12
        }
        mese = mesi.get(mese_str)
        
        if not mese:
            return None
        
        return datetime(anno, mese, giorno).date()
    except:
        return None

async def analyze_landing_page(url, session):
    """Analizza landing page per estrarre dati di contatto, metriche e sentiment"""
    try:
        logger.info(f"🔍 Analyzing: {url}")
        
        async with session.get(
            url, 
            timeout=aiohttp.ClientTimeout(total=15), 
            headers={'User-Agent': get_random_user_agent()},
            allow_redirects=True
        ) as response:
            
            if response.status != 200:
                logger.warning(f"❌ Status {response.status} for {url}")
                return None
            
            html = await response.text()
            soup = BeautifulSoup(html, 'html.parser')
            
            text = soup.get_text(separator=' ', strip=True)
            
            # Contatti
            emails = extract_emails(text)
            phones = extract_phones(text)
            
            # Link contatto
            contact_links = []
            for link in soup.find_all('a', href=True):
                href = link['href'].lower()
                link_text = link.get_text().lower()
                
                if any(kw in href or kw in link_text for kw in ['contatt', 'chi-siamo', 'about', 'azienda']):
                    full_url = urljoin(url, link['href'])
                    if is_valid_lead_url(full_url):
                        contact_links.append(full_url)
            
            # Metriche
            has_form = bool(soup.find('form'))
            has_schema = bool(soup.find('script', type='application/ld+json'))
            
            cta_elements = soup.find_all(['button', 'a'], class_=re.compile(r'cta|button|btn', re.I))
            cta_elements += soup.find_all(
                ['a', 'button'], 
                string=re.compile(r'contatt|acquista|prenota|richiedi|scopri', re.I)
            )
            has_cta = len(cta_elements) > 0
            
            # Qualità copy
            copy_quality = calculate_copy_quality(text[:3000])
            
            # SENTIMENT & TONE ANALYSIS
            sentiment_data = analyze_sentiment_tone(text[:3000])
            
            # Lead score
            score = 0
            if emails: score += 4
            if phones: score += 3
            if contact_links: score += 2
            if has_form: score += 1
            
            return {
                'url': url,
                'emails': emails,
                'phones': phones,
                'contact_links': contact_links[:3],
                'has_form': has_form,
                'has_schema': has_schema,
                'has_cta': has_cta,
                'copy_quality_score': copy_quality,
                'sentiment_score': sentiment_data['sentiment_score'],
                'tone': sentiment_data['tone'],
                'professionalism': sentiment_data['professionalism'],
                'persuasiveness': sentiment_data['persuasiveness'],
                'lead_score': min(score, 10)
            }
            
    except asyncio.TimeoutError:
        logger.warning(f"⏱️ Timeout for {url}")
        return None
    except Exception as e:
        logger.error(f"❌ Error analyzing {url}: {str(e)}")
        return None

async def scrape_meta_ads_advanced(query, max_results=10):
    """Scrape Meta Ads Library - ULTIMI 3 GIORNI, MAX 3-4 SCROLL"""
    logger.info(f"🎯 META ADS: '{query}'")
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
            
            search_url = (
                f"https://www.facebook.com/ads/library/"
                f"?active_status=active&ad_type=all&country=IT"
                f"&q={quote_plus(query)}&search_type=keyword_unordered"
            )
            
            logger.info(f"📍 URL: {search_url}")
            await page.goto(search_url, timeout=45000, wait_until='domcontentloaded')
            await page.wait_for_timeout(4000)
            
            external_urls = set()
            oggi = datetime.today().date()
            limite_data = oggi - timedelta(days=3)  # ULTIMI 3 GIORNI
            
            # MAX 3-4 SCROLL
            for scroll_num in range(4):
                logger.info(f"📜 Scroll {scroll_num + 1}/4")
                
                content = await page.content()
                soup = BeautifulSoup(content, 'html.parser')
                
                annunci = soup.find_all('div', class_='xh8yej3')
                logger.info(f"📦 Annunci: {len(annunci)}")
                
                for annuncio in annunci:
                    # NON blocchiamo più per data - prendiamo tutti i link
                    links = annuncio.find_all('a', href=True)
                    for link in links:
                        href = link.get('href', '')
                        
                        if 'l.facebook.com' in href or 'l.php' in href:
                            href = decode_fb_redirect(href)
                        
                        if is_valid_lead_url(href):
                            external_urls.add(href)
                            logger.info(f"✅ Lead: {href}")
                
                if len(external_urls) >= max_results:
                    logger.info(f"🎯 Raggiunto limite {max_results}")
                    break
                
                await page.evaluate('window.scrollBy(0, 800)')
                await page.wait_for_timeout(2000)
            
            await browser.close()
            
            for url in list(external_urls)[:max_results]:
                results.append({
                    'platform': 'Meta Ads Italy',
                    'url': url,
                    'query': query,
                    'found_at': datetime.now().isoformat()
                })
            
    except Exception as e:
        logger.error(f"❌ Meta Ads: {str(e)}")
    
    logger.info(f"🎉 META: {len(results)} lead")
    return results

async def scrape_linkedin_italy(query, max_results=10):
    """Scrape LinkedIn aziende italiane"""
    logger.info(f"💼 LINKEDIN: '{query}'")
    results = []
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=get_random_user_agent(),
                locale='it-IT'
            )
            page = await context.new_page()
            
            # LinkedIn company search (public)
            search_query = f"{query} italia"
            search_url = f"https://www.linkedin.com/search/results/companies/?keywords={quote_plus(search_query)}&origin=GLOBAL_SEARCH_HEADER"
            
            logger.info(f"📍 URL: {search_url}")
            
            try:
                await page.goto(search_url, timeout=30000, wait_until='domcontentloaded')
                await page.wait_for_timeout(3000)
                
                content = await page.content()
                soup = BeautifulSoup(content, 'html.parser')
                
                # Cerca link aziende (selettori multipli)
                company_links = []
                company_links += soup.find_all('a', href=re.compile(r'/company/'))
                company_links += soup.find_all('a', {'data-test-app-aware-link': True})
                
                seen_companies = set()
                
                for link in company_links:
                    href = link.get('href', '')
                    
                    # Accetta sia link company che website esterni
                    if '/company/' in href:
                        company_url = href.split('?')[0]
                        if 'linkedin.com' not in company_url:
                            company_url = 'https://www.linkedin.com' + company_url
                        
                        if company_url not in seen_companies:
                            seen_companies.add(company_url)
                            results.append({
                                'platform': 'LinkedIn Italy',
                                'url': company_url,
                                'query': query,
                                'found_at': datetime.now().isoformat()
                            })
                            logger.info(f"✅ Company: {company_url}")
                    
                    elif href.startswith('http') and is_valid_lead_url(href):
                        if href not in seen_companies:
                            seen_companies.add(href)
                            results.append({
                                'platform': 'LinkedIn Italy',
                                'url': href,
                                'query': query,
                                'found_at': datetime.now().isoformat()
                            })
                            logger.info(f"✅ External: {href}")
                    
                    if len(results) >= max_results:
                        break
                
            except Exception as e:
                logger.warning(f"⚠️ LinkedIn navigation: {str(e)}")
            
            await browser.close()
            
    except Exception as e:
        logger.error(f"❌ LinkedIn: {str(e)}")
    
    logger.info(f"🎉 LINKEDIN: {len(results)} lead")
    return results

async def scrape_google_italy(query, max_results=10):
    """Scrape Google.it per aziende italiane"""
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
            
            commercial_query = f"{query} azienda servizio italia"
            search_url = f"https://www.google.it/search?q={quote_plus(commercial_query)}&num=30&hl=it&gl=it"
            
            logger.info(f"📍 URL: {search_url}")
            
            try:
                await page.goto(search_url, timeout=45000, wait_until='domcontentloaded')
                await page.wait_for_timeout(4000)
            except:
                print("hjgkjhgkjhg")
            
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
                        
                        if seen_domains[domain] < 2:
                            results.append({
                                'platform': 'Google Italy',
                                'url': href,
                                'query': query,
                                'found_at': datetime.now().isoformat()
                            })
                            seen_domains[domain] += 1
                            logger.info(f"✅ Lead: {href}")
                
                if len(results) >= max_results:
                    break
            
            await browser.close()
            
    except Exception as e:
        logger.error(f"❌ Google: {str(e)}")
    
    logger.info(f"🎉 GOOGLE: {len(results)} lead")
    return results

async def scrape_all_platforms(query):
    """Orchestrazione scraping - MAX 10 LEAD TOTALI"""
    logger.info(f"🚀 START: '{query}'")
    
    # Scraping parallelo
    tasks = [
        scrape_meta_ads_advanced(query, max_results=10),
        scrape_linkedin_italy(query, max_results=10),
        scrape_google_italy(query, max_results=10)
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    all_leads = []
    for platform_results in results:
        if isinstance(platform_results, list):
            all_leads.extend(platform_results)
        else:
            logger.error(f"❌ Error: {platform_results}")
    
    # Deduplica
    seen_urls = set()
    unique_leads = []
    for lead in all_leads:
        if lead['url'] not in seen_urls:
            seen_urls.add(lead['url'])
            unique_leads.append(lead)
    
    # LIMITA A 10 LEAD
    unique_leads = unique_leads[:10]
    
    logger.info(f"📊 Lead unici: {len(unique_leads)}")
    
    # Analisi landing pages
    async with aiohttp.ClientSession() as session:
        analysis_tasks = [
            analyze_landing_page(lead['url'], session) 
            for lead in unique_leads
        ]
        analyses = await asyncio.gather(*analysis_tasks, return_exceptions=True)
        
        enriched_leads = []
        for lead, analysis in zip(unique_leads, analyses):
            if isinstance(analysis, dict) and analysis:
                lead.update(analysis)
                enriched_leads.append(lead)
    
    # Ordina per lead_score
    enriched_leads.sort(key=lambda x: x.get('lead_score', 0), reverse=True)
    
    quality_leads = [l for l in enriched_leads if l.get('lead_score', 0) > 0]
    
    logger.info(f"✨ FINAL: {len(quality_leads)} quality lead")
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
        
        logger.info(f"🚀 API: '{query}'")
        
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            results = loop.run_until_complete(scrape_all_platforms(query))
        finally:
            loop.close()
        
        scraped_data = results
        
        logger.info(f"✅ DONE: {len(results)} lead")
        
        response = jsonify({
            'success': True,
            'count': len(results),
            'leads': results
        })
        
        response.headers['Content-Type'] = 'application/json'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        
        return response
        
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
        return jsonify({'error': 'Nessun dato da esportare'}), 400
    
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        'platform', 'url', 'query', 'found_at', 'emails', 'phones',
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
    # Ottimizzato per Render
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)