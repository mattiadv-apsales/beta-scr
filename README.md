# 🎯 Lead Scraper Pro

App web completa per lo scraping automatico di lead da **Meta Ads Library**, **Reddit** e **LinkedIn** con analisi intelligente delle landing page.

## 🚀 Caratteristiche

### Scraping Multi-Piattaforma
- ✅ **Meta Ads Library**: Estrae annunci pubblicitari e link associati
- ✅ **Reddit**: Cerca post e link rilevanti per query
- ✅ **LinkedIn**: Scraping di contenuti e link aziendali
- ✅ Gestione automatica di user-agent casuali
- ✅ Filtro automatico domini bloccati (social, URL shortener)

### Analisi Landing Page
- 📧 **Estrazione Email**: Trova tutte le email nelle pagine
- 📱 **Estrazione Telefoni**: Rileva numeri di telefono in vari formati
- 🔗 **Pagine Contatto**: Identifica link a pagine di contatto
- 📝 **Form Detection**: Verifica presenza di form di contatto
- 🎯 **CTA Analysis**: Analizza call-to-action presenti
- ✍️ **Copy Quality Score**: Valuta la qualità del copywriting (0-10)
- ⭐ **Lead Score**: Punteggio complessivo del lead (0-10)

### Export Dati
- 📥 Export CSV con tutti i dati strutturati
- 📥 Export JSON per integrazioni API
- 📊 Statistiche in tempo reale

## 📋 Prerequisiti

- Python 3.11+
- Docker (opzionale, per deployment)

## 🛠️ Installazione Locale

### 1. Clone del Repository
```bash
git clone <your-repo-url>
cd lead-scraper-pro
```

### 2. Crea Virtual Environment
```bash
python -m venv venv
source venv/bin/activate  # Su Windows: venv\Scripts\activate
```

### 3. Installa Dipendenze
```bash
pip install -r requirements.txt
playwright install chromium
```

### 4. Avvia l'App
```bash
python app.py
```

L'app sarà disponibile su: **http://localhost:5000**

## 🐳 Deployment con Docker

### Build Immagine
```bash
docker build -t lead-scraper-pro .
```

### Run Container
```bash
docker run -p 5000:5000 lead-scraper-pro
```

## ☁️ Deployment Online

### Opzione 1: Render.com (Consigliato)

1. **Crea account su [Render.com](https://render.com)**

2. **Connetti Repository GitHub**
   - Vai su Dashboard → New → Web Service
   - Connetti il tuo repository GitHub

3. **Configurazione Automatica**
   - Render rileverà automaticamente il `render.yaml`
   - Oppure configura manualmente:
     - **Environment**: Docker
     - **Region**: Frankfurt
     - **Plan**: Starter (gratis)

4. **Deploy**
   - Clicca "Create Web Service"
   - Render costruirà e deployerà automaticamente l'app

**Tempo di deploy**: ~5-10 minuti

**URL**: `https://lead-scraper-pro.onrender.com`

### Opzione 2: Fly.io

1. **Installa Fly CLI**
```bash
curl -L https://fly.io/install.sh | sh
```

2. **Login**
```bash
fly auth login
```

3. **Launch App**
```bash
fly launch
```

4. **Deploy**
```bash
fly deploy
```

**Tempo di deploy**: ~3-5 minuti

### Opzione 3: Railway

1. **Vai su [Railway.app](https://railway.app)**
2. **New Project → Deploy from GitHub**
3. **Seleziona il repository**
4. **Railway rileva automaticamente il Dockerfile**
5. **Deploy automatico**

## 📖 Come Usare

### 1. Interfaccia Web

1. Apri l'app nel browser
2. Inserisci una query di ricerca (es: "digital marketing", "fitness app", "ecommerce")
3. Clicca "🚀 Avvia Scraping"
4. Attendi il completamento (può richiedere 2-5 minuti)
5. Visualizza i risultati con statistiche
6. Esporta in CSV o JSON

### 2. API Endpoints

#### Avvia Scraping
```bash
POST /api/scrape
Content-Type: application/json

{
  "query": "digital marketing"
}
```

**Risposta:**
```json
{
  "success": true,
  "count": 150,
  "leads": [
    {
      "platform": "Meta Ads",
      "url": "https://example.com",
      "query": "digital marketing",
      "emails": ["contact@example.com"],
      "phones": ["+1234567890"],
      "contact_links": ["https://example.com/contact"],
      "has_form": true,
      "has_cta": true,
      "copy_quality_score": 8,
      "lead_score": 9
    }
  ]
}
```

#### Export CSV
```bash
GET /export/csv
```

#### Export JSON
```bash
GET /export/json
```

## ⚙️ Configurazione

### Limiti
- **Max lead per piattaforma**: 200
- **Max lead per dominio**: 200
- **Timeout richieste**: 15 secondi
- **Timeout Playwright**: 30 secondi

### Modifica Limiti
Modifica le costanti in `app.py`:
```python
async def scrape_meta_ads(query, max_results=200):  # Cambia qui
```

## 🔧 Troubleshooting

### Errore: "Playwright browsers not found"
```bash
playwright install chromium
playwright install-deps chromium
```

### Errore: "Port already in use"
Cambia porta in `app.py`:
```python
app.run(host='0.0.0.0', port=8080)  # Cambia 5000 con 8080
```

### LinkedIn Access Restricted
LinkedIn può bloccare scraping automatizzato. L'app gestisce l'errore gracefully.

### Timeout durante scraping
Aumenta timeout in `app.py`:
```python
await page.goto(url, timeout=60000)  # Da 30000 a 60000
```

## 📊 Metriche Performance

- **Scraping speed**: ~50-100 lead/minuto
- **Analisi landing page**: ~10-20 pagine/minuto
- **Memory usage**: ~500MB-1GB
- **CPU usage**: Moderato durante scraping

## 🔒 Sicurezza

- ✅ User-agent rotation per evitare blocchi
- ✅ Timeout su tutte le richieste
- ✅ Gestione errori completa
- ✅ Validazione URL prima del fetch
- ✅ Nessun dato sensibile in logs

## 🚧 Limitazioni

- LinkedIn ha protezioni anti-scraping aggressive
- Meta Ads Library può limitare accessi non autenticati
- Reddit può richiedere rate limiting su alto volume
- Alcuni siti possono bloccare richieste automatizzate

## 📝 Note Legali

⚠️ **Importante**: Usa questo strumento in modo responsabile e rispetta i Terms of Service delle piattaforme. Lo scraping può violare i ToS di alcuni siti. Usa solo per scopi legittimi e educativi.

## 🤝 Contributi

Contributi benvenuti! Apri una PR o Issue su GitHub.

## 📄 Licenza

MIT License - Usa liberamente per progetti personali e commerciali.

## 🆘 Support

Per problemi o domande:
- Apri una Issue su GitHub
- Consulta la documentazione delle dipendenze
- Controlla i logs per errori specifici

## 🎉 Credits

Sviluppato con:
- Flask (Web Framework)
- Playwright (Browser Automation)
- BeautifulSoup (HTML Parsing)
- aiohttp (Async HTTP)

---

**Made with ❤️ for lead generation professionals**