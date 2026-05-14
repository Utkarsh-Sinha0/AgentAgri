# 🚀 AgentAgri Quick Start Guide

## **Fastest Path to Running AgentAgri: 5 Minutes**

This guide gets you from zero to a fully functional agricultural AI agent in 5 minutes.

---

## Step 1: Install Ollama & Download Gemma 4 (2 minutes)

### A. Install Ollama
Go to **[ollama.ai](https://ollama.ai)** and download for your OS (Windows, Mac, Linux).

Run the installer and follow the prompts. Ollama will start automatically.

### B. Pull Gemma 4 Model

Open **PowerShell** and run:

```powershell
ollama pull gemma4:2b
```

This downloads the **2B model (4GB)** - recommended for fast demo.

**OR** for higher quality (8.9GB, slower):
```powershell
ollama pull gemma4:e4b
```

**That's it!** Ollama is now running at `http://localhost:11434` with Gemma 4 ready to use.

---

## Step 2: Clone & Start AgentAgri (3 minutes)

### A. Clone the Repository

```powershell
git clone https://github.com/Utkarsh-Sinha0/AgentAgri.git
cd AgentAgri
```

### B. Copy Environment File

```powershell
copy .env.example .env
```

### C. Start Docker

```powershell
docker-compose up --build
```

**Docker automatically:**
- ✅ Detects your running Ollama instance
- ✅ Uses the Gemma 4 model you downloaded
- ✅ Starts the FastAPI backend
- ✅ Builds the frontend
- ✅ Seeds demo data
- ✅ Creates the database

**Wait for the message:** `Uvicorn running on http://localhost:8000`

---

## Step 3: Access AgentAgri (Instant)

### Open in Browser

```
http://localhost:8000
```

You now have:
- 🎨 Professional 7-page dashboard
- 🌾 Crop disease diagnosis
- 🌤️ Weather forecasts
- 💰 Market prices
- 💼 Finance tracking
- 🤖 Gemma 4 AI Advisor showcase
- 📊 System health monitoring

---

## Step 4 (Optional): Setup Telegram Bot

### A. Create Telegram Bot

1. Open Telegram
2. Chat with **@BotFather**
3. Send `/newbot`
4. Follow prompts
5. **Copy your bot token** (looks like `123456789:ABCDEFGhijklmnop`)

### B. Add Token to AgentAgri

Edit `.env` file:

```powershell
TELEGRAM_BOT_TOKEN=your_token_here
```

**Restart Docker:**
```powershell
docker-compose down
docker-compose up
```

### C. Talk to Your Bot on Telegram

```
/start              → Welcome & registration
/field              → Register your field
/crop               → Select crop & stage
/demo               → Load demo farm with history
📸 Send photo       → Crop disease analysis
/prices             → Market prices for crops
/finance            → P&L tracking
/memory             → Field history & insights
/help               → See all commands
```

---

## 🎯 What's Happening Behind the Scenes

```
Your Ollama (localhost:11434)
    ↓
Docker-Compose Network
    ├─ FastAPI Backend (connects to Ollama)
    ├─ PostgreSQL Database
    ├─ Redis Cache
    ├─ MCP Services (Weather, Market, Schemes)
    └─ React Frontend (PWA)
    ↓
Telegram Bot (connects to FastAPI)
    ↓
Farmer gets agricultural AI advice
```

**Key Point**: Docker automatically discovers your local Ollama instance. Zero configuration needed.

---

## 🔧 Troubleshooting

### Docker can't find Ollama

**Symptom**: `Connection refused http://localhost:11434`

**Solution**: Make sure Ollama is running:
```powershell
ollama serve
```

Ollama should print: `Listening on http://localhost:11434`

### Telegram bot not responding

**Symptom**: Bot registered but no replies

**Solution**: 
1. Check `.env` has correct `TELEGRAM_BOT_TOKEN`
2. Run `docker-compose logs app` to see errors
3. Restart: `docker-compose down && docker-compose up`

### Low memory / Slow response

**Problem**: Using E4B model (8.9GB) on limited RAM

**Solution**: Use 2B model instead:
```powershell
ollama pull gemma4:2b
# Edit .env: OLLAMA_MODEL=gemma4:2b
# Restart Docker
```

### Database error on startup

**Solution**: Reset database:
```powershell
docker-compose down -v
docker-compose up
```

---

## ✨ Features You Can Try Right Now

### 1. **Crop Disease Diagnosis**
```
Telegram: Send a photo of a diseased leaf
Response: Symptom analysis, causes, recommended actions
```

### 2. **Weather Awareness**
```
Dashboard → Weather tab
See: 5-day forecast, rainfall alerts, humidity warnings
```

### 3. **Market Intelligence**
```
Telegram: /prices
See: Rice, wheat, maize, pulses prices with trends
```

### 4. **Financial Tracking**
```
Telegram: /expense fertilizer 500 "10kg urea"
Telegram: /sale rice 50 "sold at ₹2400/quintal"
See: P&L, margins, category breakdown
```

### 5. **Gemma 4 Showcase**
```
Dashboard → AI Advisor tab
See: Model name, latency, reasoning summary, tools used
```

### 6. **System Health**
```
Dashboard → Settings tab
See: All service health, model info, database status
```

---

## 📚 Next Steps

- **Learn all features**: Read [FEATURES.md](docs/FEATURES.md)
- **Deploy to production**: Read [SETUP.md](docs/SETUP.md)
- **API documentation**: Read [API.md](docs/API.md)
- **Architecture deep dive**: Read [README.md](README.md)

---

## 🎓 Key Takeaway

**AgentAgri is designed to work out-of-the-box:**
1. Download Ollama + Gemma 4 model → Done
2. Run `docker-compose up` → Done
3. Open browser to `http://localhost:8000` → Done
4. (Optional) Add Telegram bot token → Done

**No signup. No API keys. No cloud. Pure local AI for farmers.**

---

## 📞 Support

- Check logs: `docker-compose logs -f app`
- Check health: `http://localhost:8000/health`
- Read docs: [docs/](docs/) folder
- GitHub issues: [Issues](https://github.com/Utkarsh-Sinha0/AgentAgri/issues)

---

**Happy farming! 🌾**
