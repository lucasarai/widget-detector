# 1. Usa l'immagine UFFICIALE di Microsoft Playwright (ha già Chromium e Ubuntu pre-configurati!)
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

# 2. Crea la cartella di lavoro
WORKDIR /app

# 3. Copia i requisiti e installa solo Flask, BS4 e Gunicorn
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copia il tuo codice geniale
COPY . .

# 5. Avvia il server
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8000", "--timeout", "120", "--workers", "2"]
