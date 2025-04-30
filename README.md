# SOValidator (Web-Version)

## Funktionen
- Lädt eine PDF mit eingebetteter XML
- Prüft XML-Syntax
- Validiert gegen das UBL-Invoice-2.1.xsd Schema

## Lokaler Start
```
pip install -r requirements.txt
python app.py
```

## Deployment auf Render.com
1. Neues Web Service erstellen
2. Python 3 wählen
3. Start-Kommando: `gunicorn app:app`
