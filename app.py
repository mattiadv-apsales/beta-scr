from flask import Flask, render_template, request, jsonify, send_file
import asyncio
import re
import json
import csv
import io
from datetime import datetime
from urllib.parse import urlparse, urljoin
import random
import logging
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from bs4 import BeautifulSoup
import aiohttp
from collections import defaultdict

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Storage per i risultati
scraped_data = []

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15'
]

BLOCKED_DOMAINS = {
    'facebook.com', 'fb.com', 'instagram.com', 'twitter.com', 'x.com',
    'linkedin.com', 'reddit.com', 'youtube.com', 'youtu.be', 'tiktok.com',
    'bit.ly', 't.co', 'goo.gl', 'ow.ly', 'short.link', 'tinyurl.com'
}

CONTACT_KEYWORDS = ['contact', 'about', 'get-in-touch', 'reach-us', 'support', 'help']

def get_random_user_agent():
    return random.choice(USER_AGENTS)

def is_valid_url(url):
    if not url or not url.startswith('http'):
        return False
    domain = urlparse(url).netloc.lower()
    return not any(blocked in domain for blocked in BLOCKED_DOMAINS)

def extract_emails(text):
    pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    return list(set(re.findall(pattern, text)))

def extract_phones(text):
    patterns = [
        r'\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,4}[-.\s]?\d{1,9}',
        r'\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b',
        r'\(\d{3}\)\s*\d{3}[-.\s]?\d{4}'
    ]
    phones = []
    for pattern in patterns:
        phones.extend(re.findall(pattern, text))
    return list(set(phones))

def calculate_copy_quality(text):
    if not text or len(text) < 50:
        return 0
    
    score = 0
    text_lower = text.lower()
    
    # Lunghezza appropriata
    if 100 < len(text) < 2000:
        score += 2
    
    # Call to action
    cta_words = ['buy', 'get', 'try', 'start', 'join', 'subscribe', 'download', 'learn', 'discover', 'free']
    if any(word in text_lower for word in cta_words):
        score += 2
    
    # Benefici
    benefit_words = ['save', 'increase', 'improve', 'boost', 'grow', 'reduce', 'easy', 'fast', 'simple']
    if any(word in text_lower for word in benefit_words):
        score += 2
    
    # Urgenza
    urgency_words = ['now', 'today', 'limited', 'offer', 'hurry', 'expires']
    if any(word in text_lower for word in urgency_words):
        score += 2
    
    # Social proof
    if any(word in text_lower for word in ['customers', 'reviews', 'rated', 'trusted']):
        score += 2
    
    return min(score, 10)

async def analyze_landing_page(url, session):
    try:
        logger.info(f"Analyzing landing page: {url}")
        
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15), headers={'User-Agent': get_random_user_agent()}) as response:
            if response.status != 200:
                return None
            
            html = await response.text()
            soup = BeautifulSoup(html, 'html.parser')
            
            # Estrai testo
            text = soup.get_text(separator=' ', strip=True)
            
            # Trova emails e telefoni
            emails = extract_emails(text)
            phones = extract_phones(text)
            
            # Cerca pagina contatti
            contact_links = []
            for link in soup.find_all('a', href=True):
                href = link['href'].lower()
                if any(keyword in href for keyword in CONTACT_KEYWORDS):
                    full_url = urljoin(url, link['href'])
                    contact_links.append(full_url)
            
            # Verifica form
            has_form = bool(soup.find('form'))
            
            # Verifica Schema.org
            has_schema = bool(soup.find('script', type='application/ld+json'))
            
            # CTA
            cta_elements = soup.find_all(['button', 'a'], class_=re.compile(r'cta|button|btn', re.I))
            has_cta = len(cta_elements) > 0
            
            # Quality score
            copy_quality = calculate_copy_quality(text[:2000])
            
            # Score finale
            score = 0
            if emails: score += 3
            if phones: score += 3
            if contact_links: score += 2
            if has_form: score += 1
            if has_cta: score += 1
            
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

async def scrape_meta_ads(query, max_results=200):
    logger.info(f"Scraping Meta Ads Library for: {query}")
    results = []
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=get_random_user_agent())
            page = await context.new_page()
            
            search_url = f"https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=ALL&q={query}&search_type=keyword_unordered"
            
            await page.goto(search_url, timeout=30000, wait_until='domcontentloaded')
            await page.wait_for_timeout(3000)
            
            # Scroll per caricare più risultati
            for _ in range(5):
                await page.evaluate('window.scrollBy(0, window.innerHeight)')
                await page.wait_for_timeout(1000)
            
            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')
            
            # Estrai link dagli annunci
            links = soup.find_all('a', href=True)
            seen_domains = defaultdict(int)
            
            for link in links:
                href = link.get('href', '')
                if href and 'http' in href and is_valid_url(href):
                    domain = urlparse(href).netloc
                    if seen_domains[domain] < max_results:
                        results.append({
                            'platform': 'Meta Ads',
                            'url': href,
                            'query': query,
                            'found_at': datetime.now().isoformat()
                        })
                        seen_domains[domain] += 1
                
                if len(results) >= max_results:
                    break
            
            await browser.close()
            
    except Exception as e:
        logger.error(f"Meta Ads scraping error: {str(e)}")
    
    logger.info(f"Meta Ads: found {len(results)} leads")
    return results[:max_results]

async def scrape_reddit(query, max_results=200):
    logger.info(f"Scraping Reddit for: {query}")
    results = []
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=get_random_user_agent())
            page = await context.new_page()
            
            search_url = f"https://www.reddit.com/search/?q={query}&type=link"
            
            await page.goto(search_url, timeout=30000, wait_until='domcontentloaded')
            await page.wait_for_timeout(3000)
            
            # Scroll
            for _ in range(5):
                await page.evaluate('window.scrollBy(0, window.innerHeight)')
                await page.wait_for_timeout(1000)
            
            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')
            
            links = soup.find_all('a', href=True)
            seen_domains = defaultdict(int)
            
            for link in links:
                href = link.get('href', '')
                if href and href.startswith('http') and is_valid_url(href):
                    domain = urlparse(href).netloc
                    if seen_domains[domain] < max_results:
                        results.append({
                            'platform': 'Reddit',
                            'url': href,
                            'query': query,
                            'found_at': datetime.now().isoformat()
                        })
                        seen_domains[domain] += 1
                
                if len(results) >= max_results:
                    break
            
            await browser.close()
            
    except Exception as e:
        logger.error(f"Reddit scraping error: {str(e)}")
    
    logger.info(f"Reddit: found {len(results)} leads")
    return results[:max_results]

async def scrape_linkedin(query, max_results=200):
    logger.info(f"Scraping LinkedIn for: {query}")
    results = []
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=get_random_user_agent())
            page = await context.new_page()
            
            search_url = f"https://www.linkedin.com/search/results/content/?keywords={query}"
            
            try:
                await page.goto(search_url, timeout=30000, wait_until='domcontentloaded')
                await page.wait_for_timeout(3000)
                
                # Scroll
                for _ in range(3):
                    await page.evaluate('window.scrollBy(0, window.innerHeight)')
                    await page.wait_for_timeout(1000)
                
                content = await page.content()
                soup = BeautifulSoup(content, 'html.parser')
                
                links = soup.find_all('a', href=True)
                seen_domains = defaultdict(int)
                
                for link in links:
                    href = link.get('href', '')
                    if href and href.startswith('http') and is_valid_url(href):
                        domain = urlparse(href).netloc
                        if seen_domains[domain] < max_results:
                            results.append({
                                'platform': 'LinkedIn',
                                'url': href,
                                'query': query,
                                'found_at': datetime.now().isoformat()
                            })
                            seen_domains[domain] += 1
                    
                    if len(results) >= max_results:
                        break
                        
            except PlaywrightTimeout:
                logger.warning("LinkedIn access might be restricted")
            
            await browser.close()
            
    except Exception as e:
        logger.error(f"LinkedIn scraping error: {str(e)}")
    
    logger.info(f"LinkedIn: found {len(results)} leads")
    return results[:max_results]

async def scrape_all_platforms(query):
    tasks = [
        scrape_meta_ads(query),
        scrape_reddit(query),
        scrape_linkedin(query)
    ]
    
    results = await asyncio.gather(*tasks)
    all_leads = []
    for platform_leads in results:
        all_leads.extend(platform_leads)
    
    # Analizza landing pages
    logger.info(f"Analyzing {len(all_leads)} landing pages...")
    
    async with aiohttp.ClientSession() as session:
        analysis_tasks = []
        for lead in all_leads:
            analysis_tasks.append(analyze_landing_page(lead['url'], session))
        
        analyses = await asyncio.gather(*analysis_tasks)
        
        for lead, analysis in zip(all_leads, analyses):
            if analysis:
                lead.update(analysis)
            else:
                lead.update({
                    'emails': [],
                    'phones': [],
                    'contact_links': [],
                    'has_form': False,
                    'has_schema': False,
                    'has_cta': False,
                    'copy_quality_score': 0,
                    'lead_score': 0
                })
    
    # Ordina per score
    all_leads.sort(key=lambda x: x.get('lead_score', 0), reverse=True)
    
    return all_leads

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/scrape', methods=['POST'])
def scrape():
    global scraped_data
    
    try:
        data = request.get_json()
        query = data.get('query', '')
        
        if not query:
            return jsonify({'error': 'Query is required'}), 400
        
        logger.info(f"Starting scrape for query: {query}")
        
        # Esegui scraping asincrono
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
        logger.error(f"Scrape error: {str(e)}")
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