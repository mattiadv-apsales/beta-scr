from flask import Flask, render_template, request, jsonify, send_file
import asyncio
import re
import json
import csv
import io
from datetime import datetime
from urllib.parse import urlparse, urljoin, quote_plus
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
    'facebook.com', 'fb.com', 'instagram.com', 'twitter.com', 'x.com',
    'linkedin.com', 'reddit.com', 'youtube.com', 'youtu.be', 'tiktok.com',
    'bit.ly', 't.co', 'goo.gl', 'ow.ly', 'tinyurl.com', 'pinterest.com',
    'support.', 'help.', 'about.facebook', 'metastatus.com', 'transparency.',
    'policies.', 'terms.', 'privacy.', 'legal.', 'cookie.', 'reddithelp.com'
}

def get_random_user_agent():
    return random.choice(USER_AGENTS)

def is_valid_lead_url(url):
    """Verifica che sia un URL valido per un lead aziendale ITALIANO"""
    if not url or not url.startswith('http'):
        return False
    
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        path = parsed.path.lower()
        
        # Blocca domini social e utility
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
        
        # ✅ FILTRO ITALIA: Accetta solo domini .it o siti italiani noti
        is_italian = (
            domain.endswith('.it') or  # Domini .it
            '/it/' in path or  # Path /it/
            '/italia/' in path or  # Path /italia/
            any(city in domain for city in ['roma', 'milano', 'torino', 'napoli', 'firenze', 'bologna'])  # Città italiane nel dominio
        )
        
        if not is_italian:
            return False
            
        return True
    except:
        return False

def extract_emails(text):
    pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    emails = re.findall(pattern, text)
    # Filtra email comuni non valide
    return [e for e in set(emails) if not any(x in e.lower() for x in ['example.com', 'test.com', 'domain.com', 'email.com'])]

def extract_phones(text):
    patterns = [
        r'\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,4}[-.\s]?\d{1,9}',
        r'\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b',
        r'\(\d{3}\)\s*\d{3}[-.\s]?\d{4}'
    ]
    phones = []
    for pattern in patterns:
        found = re.findall(pattern, text)
        phones.extend([p for p in found if len(re.sub(r'\D', '', p)) >= 10])
    return list(set(phones))[:5]

def calculate_copy_quality(text):
    if not text or len(text) < 50:
        return 0
    
    score = 0
    text_lower = text.lower()
    
    if 100 < len(text) < 2000:
        score += 2
    
    cta_words = ['buy', 'get', 'try', 'start', 'join', 'subscribe', 'download', 'learn', 'discover', 'free', 'book', 'contact', 'request']
    if sum(1 for word in cta_words if word in text_lower) >= 2:
        score += 3
    
    benefit_words = ['save', 'increase', 'improve', 'boost', 'grow', 'reduce', 'easy', 'fast', 'simple', 'best', 'professional']
    if sum(1 for word in benefit_words if word in text_lower) >= 2:
        score += 3
    
    urgency_words = ['now', 'today', 'limited', 'offer', 'hurry', 'expires']
    if any(word in text_lower for word in urgency_words):
        score += 2
    
    return min(score, 10)

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
            
            contact_links = []
            for link in soup.find_all('a', href=True):
                href = link['href'].lower()
                link_text = link.get_text().lower()
                if any(kw in href or kw in link_text for kw in ['contact', 'get-in-touch', 'reach-us']):
                    full_url = urljoin(url, link['href'])
                    if is_valid_lead_url(full_url):
                        contact_links.append(full_url)
            
            has_form = bool(soup.find('form'))
            has_schema = bool(soup.find('script', type='application/ld+json'))
            
            cta_elements = soup.find_all(['button', 'a'], class_=re.compile(r'cta|button|btn', re.I))
            cta_elements += soup.find_all(['a', 'button'], string=re.compile(r'contact|buy|get|start|try|book', re.I))
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
                'has_form': has_form,
                'has_schema': has_schema,
                'has_cta': has_cta,
                'copy_quality_score': copy_quality,
                'lead_score': min(score, 10)
            }
    except Exception as e:
        logger.error(f"Error analyzing {url}: {str(e)}")
        return None

async def scrape_meta_ads(query, max_results=50):
    """Scrape Meta Ads Library per trovare advertiser ITALIANI"""
    logger.info(f"Scraping Meta Ads Library for ITALIAN leads: {query}")
    results = []
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=get_random_user_agent(),
                viewport={'width': 1920, 'height': 1080},
                locale='it-IT'  # Locale italiano
            )
            page = await context.new_page()
            
            # URL della Meta Ads Library - SOLO ITALIA
            search_url = f"https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=IT&q={quote_plus(query)}&search_type=keyword_unordered"
            
            logger.info(f"Navigating to Meta Ads Library (Italy only)...")
            await page.goto(search_url, timeout=45000, wait_until='domcontentloaded')
            await page.wait_for_timeout(5000)
            
            # Scroll per caricare ads
            for i in range(10):
                await page.evaluate('window.scrollBy(0, 800)')
                await page.wait_for_timeout(1500)
            
            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')
            
            # Estrai link da annunci pubblicitari
            seen_domains = defaultdict(int)
            
            # Cerca tutti i link in annunci
            all_links = soup.find_all('a', href=True)
            logger.info(f"Found {len(all_links)} total links")
            
            for link in all_links:
                href = link.get('href', '')
                
                # Estrai URL finale da link facebook
                if 'l.facebook.com' in href or 'facebook.com/l.php' in href:
                    match = re.search(r'[?&]u=([^&]+)', href)
                    if match:
                        from urllib.parse import unquote
                        href = unquote(match.group(1))
                
                if is_valid_lead_url(href):
                    domain = urlparse(href).netloc
                    
                    if seen_domains[domain] < 3:
                        results.append({
                            'platform': 'Meta Ads Italy',
                            'url': href,
                            'query': query,
                            'found_at': datetime.now().isoformat()
                        })
                        seen_domains[domain] += 1
                        logger.info(f"Found Italian lead: {href}")
                
                if len(results) >= max_results:
                    break
            
            await browser.close()
            
    except Exception as e:
        logger.error(f"Meta Ads error: {str(e)}")
    
    logger.info(f"Meta Ads Italy: {len(results)} leads found")
    return results

async def scrape_reddit(query, max_results=50):
    """Scrape Reddit per trovare link aziendali ITALIANI"""
    logger.info(f"Scraping Reddit for ITALIAN leads: {query}")
    results = []
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=get_random_user_agent())
            page = await context.new_page()
            
            # Cerca in subreddit italiani + query generale con filtro "site:.it"
            italian_query = f"{query} (site:.it OR subreddit:italy OR subreddit:ItalyInformatica)"
            search_url = f"https://old.reddit.com/search?q={quote_plus(italian_query)}&sort=relevance&t=all"
            
            await page.goto(search_url, timeout=30000)
            await page.wait_for_timeout(3000)
            
            # Scroll
            for _ in range(5):
                await page.evaluate('window.scrollBy(0, 1000)')
                await page.wait_for_timeout(1000)
            
            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')
            
            seen_domains = defaultdict(int)
            
            # Cerca link nei post
            posts = soup.find_all('div', class_='thing')
            logger.info(f"Found {len(posts)} Reddit posts")
            
            for post in posts:
                # Link diretto del post
                link_elem = post.find('a', class_='title')
                if link_elem and link_elem.get('href'):
                    href = link_elem['href']
                    
                    if is_valid_lead_url(href):
                        domain = urlparse(href).netloc
                        
                        if seen_domains[domain] < 3:
                            results.append({
                                'platform': 'Reddit Italy',
                                'url': href,
                                'query': query,
                                'found_at': datetime.now().isoformat()
                            })
                            seen_domains[domain] += 1
                            logger.info(f"Found Italian lead: {href}")
                
                if len(results) >= max_results:
                    break
            
            await browser.close()
            
    except Exception as e:
        logger.error(f"Reddit error: {str(e)}")
    
    logger.info(f"Reddit Italy: {len(results)} leads found")
    return results

async def scrape_google_search(query, max_results=30):
    """Scrape Google per trovare aziende ITALIANE"""
    logger.info(f"Scraping Google for ITALIAN leads: {query}")
    results = []
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=get_random_user_agent(),
                locale='it-IT'
            )
            page = await context.new_page()
            
            # Query ottimizzata per ITALIA
            commercial_query = f"{query} azienda servizio italia site:.it"
            search_url = f"https://www.google.it/search?q={quote_plus(commercial_query)}&num=50&hl=it&gl=it"
            
            await page.goto(search_url, timeout=30000)
            await page.wait_for_timeout(3000)
            
            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')
            
            seen_domains = defaultdict(int)
            
            # Estrai risultati di ricerca
            search_results = soup.find_all('div', class_='g')
            logger.info(f"Found {len(search_results)} Google Italy results")
            
            for result in search_results:
                link_elem = result.find('a', href=True)
                if link_elem:
                    href = link_elem['href']
                    
                    # Pulisci URL di Google
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
                            logger.info(f"Found Italian lead: {href}")
                
                if len(results) >= max_results:
                    break
            
            await browser.close()
            
    except Exception as e:
        logger.error(f"Google Search error: {str(e)}")
    
    logger.info(f"Google Italy: {len(results)} leads found")
    return results

async def scrape_all_platforms(query):
    """Scrape tutte le piattaforme in parallelo"""
    tasks = [
        scrape_meta_ads(query, max_results=40),
        scrape_reddit(query, max_results=40),
        scrape_google_search(query, max_results=40)
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    all_leads = []
    for platform_results in results:
        if isinstance(platform_results, list):
            all_leads.extend(platform_results)
        else:
            logger.error(f"Platform error: {platform_results}")
    
    # Rimuovi duplicati per URL
    seen_urls = set()
    unique_leads = []
    for lead in all_leads:
        if lead['url'] not in seen_urls:
            seen_urls.add(lead['url'])
            unique_leads.append(lead)
    
    logger.info(f"Total unique leads before analysis: {len(unique_leads)}")
    
    # Analizza landing pages
    async with aiohttp.ClientSession() as session:
        analysis_tasks = [analyze_landing_page(lead['url'], session) for lead in unique_leads]
        analyses = await asyncio.gather(*analysis_tasks, return_exceptions=True)
        
        enriched_leads = []
        for lead, analysis in zip(unique_leads, analyses):
            if isinstance(analysis, dict) and analysis:
                lead.update(analysis)
                enriched_leads.append(lead)
            elif isinstance(analysis, Exception):
                logger.error(f"Analysis error for {lead['url']}: {analysis}")
    
    # Ordina per score
    enriched_leads.sort(key=lambda x: x.get('lead_score', 0), reverse=True)
    
    # Filtra solo lead con score > 0
    quality_leads = [l for l in enriched_leads if l.get('lead_score', 0) > 0]
    
    logger.info(f"Final quality leads: {len(quality_leads)}")
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
            return jsonify({'error': 'Query is required'}), 400
        
        logger.info(f"=== Starting scrape for: {query} ===")
        
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
        return jsonify({'error': 'No data to export'}), 400
    
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        'platform', 'url', 'query', 'found_at', 'emails', 'phones',
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
        return jsonify({'error': 'No data to export'}), 400
    
    return send_file(
        io.BytesIO(json.dumps(scraped_data, indent=2).encode('utf-8')),
        mimetype='application/json',
        as_attachment=True,
        download_name=f'leads_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    )

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)