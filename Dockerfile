# 1. Usa l'immagine UFFICIALE di Microsoft Playwright
FROM mcr.microsoft.com/playwright/python:v1.59.0-jammy

# 2. Crea la cartella di lavoro
WORKDIR /app

# 3. Copia i requisiti e installa le dipendenze
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copia il tuo codice geniale
COPY . .

# 5. Avvia il server (usiamo il formato shell senza parentesi quadre per leggere la variabile $PORT dinamica di Railway)
CMD gunicorn app:app --bind 0.0.0.0:$PORT --timeout 120 --workers 2
