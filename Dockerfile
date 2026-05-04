# 1. Usa un ambiente Python puro e leggero
FROM python:3.10-slim

# 2. Crea la cartella di lavoro
WORKDIR /app

# 3. Copia il file dei requisiti e installa le librerie Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Installa il browser Chromium
RUN playwright install chromium

# 5. IL TRUCCO MAGICO: Installa tutte le dipendenze di sistema (librerie grafiche Linux) in automatico
RUN playwright install-deps

# 6. Copia tutto il tuo codice (app.py) nel server
COPY . .

# 7. Avvia il server con Gunicorn
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8000", "--timeout", "120", "--workers", "2"]
