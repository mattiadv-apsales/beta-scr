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
    if not url or not url.startswith('http'):
        return False
    
    try:
        domain = urlparse(url).netloc.lower()
        blocked = ['facebook.com', 'fb.com', 'instagram.com', 'twitter.com', 
                  'x.com', 'youtube.com', 'forms.gle', 'google.com/forms']
        
        if any(b in domain for b in blocked):
            return False
        if '.' not in domain:
            return False
        
        return True
    except:
        return False

def extract_emails(text):
    pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    emails = re.findall(pattern, text)
    return [e for e in set(emails) if 'example.com' not in e.lower()][:3]

def extract_phones(text):
    patterns = [
        r'\+39[\s-]?\d{2,3}[\s-]?\d{3,4}[\s-]?\d{3,4}',
        r'\b0\d{1,3}[\s-]?\d{3,4}[\s-]?\d{3,4}\b',
        r'\b3\d{2}[\s-]?\d{3}[\s-]?\d{3,4}\b',
    ]
    phones = []
    for pattern in patterns:
        phones.extend([p for p in re.findall(pattern, text) if len(re.sub(r'\D', '', p)) >= 9])
    return list(set(phones))[:3]

async def quick_analyze(url, session):
    """Analisi VELOCISSIMA (5s timeout)"""
    try:
        async with session.get(
            url, 
            timeout=aiohttp.ClientTimeout(total=5),
            headers={'User-Agent': get_random_user_agent()},
            allow_redirects=True
        ) as response:
            if response.status != 200:
                return None
            
            html = await response.text()
            soup = BeautifulSoup(html, 'html.parser')
            text = soup.get_text(separator=' ', strip=True)[:1500]
            
            emails = extract_emails(text)
            phones = extract_phones(text)
            has_form = bool(soup.find('form'))
            
            score = (4 if emails else 0) + (4 if phones else 0) + (2 if has_form else 0)
            
            return {
                'emails': emails,
                'phones': phones,
                'has_form': has_form,
                'lead_score': min(score, 10)
            }
    except:
        return None

async def scrape_meta_ads_fast(query, max_results=20):
    """Meta Ads - 4 SCROLL VELOCI"""
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
            
            await page.goto(search_url, timeout=30000, wait_until='domcontentloaded')
            await page.wait_for_timeout(3000)
            
            external_urls = set()
            
            # SOLO 4 SCROLL
            for scroll in range(4):
                logger.info(f"📜 Meta {scroll+1}/4")
                
                content = await page.content()
                soup = BeautifulSoup(content, 'html.parser')
                
                # Estrai tutti i link
                for link in soup.find_all('a', href=True):
                    href = link.get('href', '')
                    
                    if 'l.facebook.com' in href or 'l.php' in href:
                        href = decode_fb_redirect(href)
                    
                    if href.startswith('http') and is_valid_lead_url(href):
                        if not any(fb in href for fb in ['facebook.com', 'instagram.com']):
                            external_urls.add(href)
                
                if len(external_urls) >= max_results:
                    break
                
                await page.evaluate('window.scrollBy(0, 1500)')
                await page.wait_for_timeout(1200)
            
            await browser.close()
            
            for url in list(external_urls)[:max_results]:
                results.append({
                    'platform': 'Meta Ads',
                    'url': url,
                    'query': query
                })
    
    except Exception as e:
        logger.error(f"❌ Meta: {str(e)}")
    
    logger.info(f"✅ META: {len(results)}")
    return results

async def scrape_google_fast(query, max_results=15):
    """Google - SOLO REQUESTS (no Playwright)"""
    logger.info(f"🔍 GOOGLE: '{query}'")
    results = []
    
    try:
        async with aiohttp.ClientSession() as session:
            search_query = f"{query} italia azienda"
            url = f"https://www.google.it/search?q={quote_plus(search_query)}&num=30&hl=it"
            
            async with session.get(
                url,
                headers={'User-Agent': get_random_user_agent()},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')
                
                seen = set()
                
                # Cerca link nei risultati
                for link in soup.find_all('a', href=True):
                    href = link['href']
                    
                    if href.startswith('/url?q='):
                        href = href.split('/url?q=')[1].split('&')[0]
                    
                    if is_valid_lead_url(href):
                        domain = urlparse(href).netloc
                        if domain not in seen:
                            seen.add(domain)
                            results.append({
                                'platform': 'Google',
                                'url': href,
                                'query': query
                            })
                    
                    if len(results) >= max_results:
                        break
    
    except Exception as e:
        logger.error(f"❌ Google: {str(e)}")
    
    logger.info(f"✅ GOOGLE: {len(results)}")
    return results

async def scrape_bing_fast(query, max_results=15):
    """Bing come alternativa veloce"""
    logger.info(f"🔎 BING: '{query}'")
    results = []
    
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://www.bing.com/search?q={quote_plus(query + ' italia')}&setlang=it"
            
            async with session.get(
                url,
                headers={'User-Agent': get_random_user_agent()},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')
                
                seen = set()
                
                for link in soup.find_all('a', href=True):
                    href = link['href']
                    
                    if is_valid_lead_url(href):
                        domain = urlparse(href).netloc
                        if domain not in seen:
                            seen.add(domain)
                            results.append({
                                'platform': 'Bing',
                                'url': href,
                                'query': query
                            })
                    
                    if len(results) >= max_results:
                        break
    
    except Exception as e:
        logger.error(f"❌ Bing: {str(e)}")
    
    logger.info(f"✅ BING: {len(results)}")
    return results

async def scrape_all_platforms(query):
    """ULTRA-VELOCE: 30-40s totali"""
    logger.info(f"🚀 START: '{query}'")
    
    # Scraping parallelo
    tasks = [
        scrape_meta_ads_fast(query, 20),
        scrape_google_fast(query, 15),
        scrape_bing_fast(query, 15)
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
    
    # Analisi SOLO dei primi 20 lead (risparmia tempo)
    async with aiohttp.ClientSession() as session:
        top_leads = unique_leads[:20]
        
        analyses = await asyncio.gather(
            *[quick_analyze(lead['url'], session) for lead in top_leads],
            return_exceptions=True
        )
        
        # Lead con analisi
        for lead, analysis in zip(top_leads, analyses):
            if isinstance(analysis, dict):
                lead.update(analysis)
            else:
                lead.update({
                    'emails': [],
                    'phones': [],
                    'has_form': False,
                    'lead_score': 0
                })
        
        # Lead rimanenti senza analisi
        for lead in unique_leads[20:]:
            lead.update({
                'emails': [],
                'phones': [],
                'has_form': False,
                'lead_score': 0
            })
    
    # Ordina per score
    unique_leads.sort(key=lambda x: x.get('lead_score', 0), reverse=True)
    
    logger.info(f"✨ FINAL: {len(unique_leads)} lead")
    return unique_leads

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
        'platform', 'url', 'query', 'emails', 'phones', 'has_form', 'lead_score'
    ])
    
    writer.writeheader()
    for row in scraped_data:
        row_copy = row.copy()
        row_copy['emails'] = '; '.join(row_copy.get('emails', []))
        row_copy['phones'] = '; '.join(row_copy.get('phones', []))
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