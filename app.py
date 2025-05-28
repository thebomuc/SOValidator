from flask import Flask, render_template, request, send_file, session
import fitz  # PyMuPDF
import uuid
import tempfile
from lxml import etree
import os
import re
import pandas as pd
from openpyxl import load_workbook
from difflib import get_close_matches
import sys
import io

# Dynamischer Pfad für PyInstaller (sys._MEIPASS) für statische Ressourcen und Templates
if hasattr(sys, '_MEIPASS'):
    base_path = sys._MEIPASS
    template_folder = os.path.join(base_path, 'templates')
    static_folder = os.path.join(base_path, 'static')
    DEFAULT_XSLT_PATH = os.path.join(base_path, 'EN16931-CII-validation.xslt')
    EXCEL_PATH = os.path.join(base_path, 'static', 'data', '4. EN16931+FacturX code lists values v14 - used from 2024-11-15.xlsx')
    DEFAULT_XSD_ROOT = os.path.join(base_path, 'ZF232_DE', 'Schema')
else:
    base_path = os.path.abspath(".")
    template_folder = "templates"
    static_folder = "static"
    DEFAULT_XSLT_PATH = "EN16931-CII-validation.xslt"
    EXCEL_PATH = os.path.join("static", "data", "4. EN16931+FacturX code lists values v14 - used from 2024-11-15.xlsx")
    DEFAULT_XSD_ROOT = os.path.join("ZF232_DE", "Schema")

# Flask-App mit explizitem Pfad zu Templates und Static (für .exe)
app = Flask(__name__, template_folder=template_folder, static_folder=static_folder)
app.secret_key = "supergeheimer-schlüssel"  # 🔒 für Session erforderlich
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB Upload-Limit

# Dynamischer Basispfad je nach Umgebung (Render / lokal / PyInstaller)
if hasattr(sys, '_MEIPASS'):
    base_path = sys._MEIPASS
else:
    base_path = os.path.abspath(".")

DEFAULT_XSD_ROOT = os.path.join(base_path, "ZF232_DE", "Schema")
DEFAULT_XSLT_PATH = os.path.join(base_path, "EN16931-CII-validation.xslt")
EXCEL_PATH = os.path.join(base_path, "static", "data", "4. EN16931+FacturX code lists values v14 - used from 2024-11-15.xlsx")

codelists = {
    "Country": "Alpha-2 code",
    "Currency": "Alphabetic Code",
    "ICD": "Code",
    "1001": "Code",
    "1153": "Code Values",
    "VAT CAT": "Code",
    "Text": "Code",
    "Payment": "Code",
    "5305": "Code",
    "Allowance": "Code",
    "Item": "Code",
    "Charge": "Code",
    "MIME": "Code",
    "EAS": "AES",
    "VATEX": "CODE",
    "Unit": "Code",
    "Line Status": "Code",
    "Language": "Code",
    "Characteristic": "Code",
    "Line Reason": "Code",
    "INCOTERMS": "Code",
    "TRANSPORT": "Code",
    "Date": "Code",
    "HybridDocument": "Code",
    "HybridConformance": "Code",
    "Filename": "Code",
    "HybridVersion": "Code",
}
code_sets = {}

for sheet, column in codelists.items():
    try:
        df = pd.read_excel(EXCEL_PATH, sheet_name=sheet, engine="openpyxl")
        df.columns = df.columns.str.strip()
        values = df[column].dropna().astype(str).str.strip().unique()
        code_sets[sheet] = set(values)
    except Exception:
        code_sets[sheet] = set()

MANDATORY_TAGS = [
    "ram:ID", "ram:IssueDateTime", "ram:SellerTradeParty", "ram:BuyerTradeParty",
    "ram:SpecifiedSupplyChainTradeDelivery", "ram:ApplicableHeaderTradeSettlement",
    "ram:CountryID", "ram:InvoiceCurrencyCode", "ram:LineID", "ram:TypeCode"
]

def extract_xml_from_pdf(pdf_path):
    doc = fitz.open(pdf_path)
    for i in range(doc.embfile_count()):
        name = doc.embfile_info(i).get("filename", "").lower()
        if name.endswith(".xml"):
            xml_bytes = doc.embfile_get(i)
            return xml_bytes.decode("utf-8", errors="replace")
    if doc.embfile_count() > 0:
        xml_bytes = doc.embfile_get(0)
        return xml_bytes.decode("utf-8", errors="replace")
    return None

def validate_xml(xml):
    parser = etree.XMLParser(recover=True)
    try:
        etree.fromstring(xml.encode("utf-8"), parser)
    except etree.XMLSyntaxError:
        pass
    suggestions = []
    if parser.error_log:
        for err in parser.error_log:
            msg = f"⚠️ Strukturfehler: Zeile {err.line}, Spalte {err.column}: {err.message}"
            suggestions.append(msg)
        return False, "❌ XML enthält Syntaxfehler:", [], None, suggestions
    else:
        for tag in MANDATORY_TAGS:
            for match in re.finditer(fr"<{tag}>(.*?)</{tag}>", xml):
                content = match.group(1).strip()
                if not content:
                    line = xml.count("\n", 0, match.start(1)) + 1
                    suggestions.append(f"⚠️ Pflichtfeld {tag} ist leer (Zeile {line})")
        return True, "✔️ XML ist wohlgeformt.", [], None, suggestions if suggestions else None

def list_all_xsd_files(schema_root):
    xsd_files = []
    for root, _, files in os.walk(schema_root):
        for file in files:
            if file.endswith(".xsd"):
                xsd_files.append(os.path.join(root, file))
    return xsd_files

def validate_against_all_xsds(xml, schema_root):
    results = []
    for xsd_path in list_all_xsd_files(schema_root):
        try:
            schema_doc = etree.parse(xsd_path)
            schema = etree.XMLSchema(schema_doc)
            doc = etree.fromstring(xml.encode("utf-8"))
            schema.assertValid(doc)
            return True, f"✔️ XML entspricht dem XSD ({os.path.basename(xsd_path)})."
        except Exception as e:
            if "is not expected" in str(e) or "Expected is" in str(e):
                results.append(f"⚠️ Strukturfehler in {os.path.basename(xsd_path)}: {str(e)}")
            else:
                results.append(str(e))
    return False, "❌ XML entspricht keiner XSD:<br>" + "<br>".join(results)

def validate_with_schematron(xml, xslt_path):
    try:
        xml_doc = etree.fromstring(xml.encode("utf-8"))
        xslt_doc = etree.parse(xslt_path)
        transform = etree.XSLT(xslt_doc)
        svrl = transform(xml_doc)
        failed = svrl.xpath("//svrl:failed-assert", namespaces={"svrl": "http://purl.oclc.org/dsdl/svrl"})
        return [fa.find("svrl:text", namespaces={"svrl": "http://purl.oclc.org/dsdl/svrl"}).text for fa in failed]
    except Exception as e:
        return [f"⚠️ Fehler bei Schematron-Validierung: {str(e)}"]

@app.route("/download_corrected", methods=["POST"])
def download_corrected():
    original_pdf_path = session.get("original_pdf_path")
    if not original_pdf_path or not os.path.exists(original_pdf_path):
        return "❌ Originale PDF nicht gefunden.", 400
    
    # Originale XML + Korrekturen aus Form abrufen
    xml_raw = request.form.get("xml_data")
    corrections = request.form.getlist("correction")  # z. B. ["5305|s|S", ...]
    
    # XML anwenden
    corrected_xml = xml_raw
    for correction in corrections:
        tag, old, new = correction.split("|")
        corrected_xml = corrected_xml.replace(f">{old}<", f">{new}<")

    # XML in PDF einbetten (vereinfacht)
    original_pdf_path = "uploaded.pdf"
    corrected_pdf_path = "corrected.pdf"
    doc = fitz.open(original_pdf_path)
    doc.embfile_del(0)
    doc.embfile_add("factur-x.xml", corrected_xml.encode("utf-8"))
    doc.save(corrected_pdf_path)

    return send_file(corrected_pdf_path, as_attachment=True)

@app.route("/", methods=["GET", "POST"])
def index():
    result = ""
    filename = ""
    excerpt = []
    highlight_line = None
    suggestions = []
    codelist_table = []
    syntax_table = []

    uploaded = request.files.get("pdf_file")
    if not uploaded or uploaded.filename == "":
        result = "❌ Keine Datei ausgewählt oder hochgeladen."
        return render_template("index.html", result=result, filename=filename)

    filename = uploaded.filename
    file_ext = os.path.splitext(filename)[1].lower()
    is_pdf = file_ext == ".pdf"
    is_xml = file_ext == ".xml" or uploaded.content_type in ["application/xml", "text/xml"]

    # Temporäre, eindeutige Datei erzeugen
    tmp_suffix = file_ext if is_pdf or is_xml else ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=tmp_suffix) as tmp:
        file_path = tmp.name
        uploaded.save(file_path)

    session["original_pdf_path"] = file_path

    try:
        # XML aus PDF extrahieren oder direkt einlesen
        if is_pdf:
            xml = extract_xml_from_pdf(file_path)
            if not xml:
                result = "❌ Keine XML-Datei in der PDF gefunden."
                return render_template("index.html", result=result, filename=filename)
        elif is_xml:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                xml = f.read()
        else:
            result = "❌ Ungültiger Dateityp. Bitte nur PDF oder XML hochladen."
            return render_template("index.html", result=result, filename=filename)

        # → ab hier weiter mit xml-Validierung:
        valid, msg, excerpt, highlight_line, xml_suggestions = validate_xml(xml)
        result = msg
        if xml_suggestions:
            syntax_table = xml_suggestions

        if valid:
            xsd_ok, xsd_msg = validate_against_all_xsds(xml, DEFAULT_XSD_ROOT)
            result += "<br>" + xsd_msg

            if os.path.exists(DEFAULT_XSLT_PATH) and request.form.get("schematron"):
                sch_issues = validate_with_schematron(xml, DEFAULT_XSLT_PATH)
                suggestions.extend(f"❌ {msg}" for msg in sch_issues)
    
        xml_lines = xml.splitlines()
        element_context_mapping = {
            "Currency": [r"<ram:InvoiceCurrencyCode>(.*?)</ram:InvoiceCurrencyCode>"],
            "Country": [r"<ram:CountryID>(.*?)</ram:CountryID>"],
            "Payment": [r"<ram:SpecifiedTradeSettlementPaymentMeans>.*?<ram:TypeCode>(.*?)</ram:TypeCode>"],
            "VAT CAT": [r"<ram:ApplicableTradeTax>.*?<ram:TypeCode>(.*?)</ram:TypeCode>"],
            "5305": [r"<ram:CategoryCode>(.*?)</ram:CategoryCode>"],
            "1153": [r"<ram:ReferenceTypeCode>(.*?)</ram:ReferenceTypeCode>"],
            "Date": [r'DateTimeString[^>]*?format="(.*?)"'],
            "Line Status": [r"<ram:LineStatusCode>(.*?)</ram:LineStatusCode>"],
            "INCOTERMS": [r"<ram:INCOTERMSCode>(.*?)</ram:INCOTERMSCode>"],
            "TRANSPORT": [r"<ram:TransportModeCode>(.*?)</ram:TransportModeCode>"],
            "1001": [r"<rsm:ExchangedDocument>.*?<ram:TypeCode>(.*?)</ram:TypeCode>"],
            "Unit": [
                r'<ram:BilledQuantity[^>]*?unitCode="(.*?)"',
                r'<ram:InvoicedQuantity[^>]*?unitCode="(.*?)"'
            ]
        }

        for label, patterns in element_context_mapping.items():
            allowed_set = code_sets.get(label, set())
            for pattern in patterns:
                regex = re.compile(pattern)
                for match in regex.finditer(xml):
                    raw = match.group(1) if match.lastindex else ""
                    value = raw.strip() if raw else ""
                    if value == "" or value not in allowed_set:
                      if value == "" or value not in allowed_set:
                        start = match.start(1) if match.lastindex else match.start()
                        line_number = xml.count("\n", 0, start) + 1
                        offset = start - sum(len(l) + 1 for l in xml_lines[:line_number - 1])
                        column_number = offset + 1

                        if not allowed_set:
                            dropdown_html = "⚠️ Kein Wert angegeben oder keine Codeliste verfügbar"
                        else:
                            sorted_options = sorted(allowed_set)
                            old_value = value if value else "__LEER__"
                            dropdown_html = f'→ Möglicherweise meinten Sie: <select name="correction">'
                            for option in sorted_options:
                                selected = 'selected' if option == value else ''
                                dropdown_html += f'<option value="{label}|{old_value}|{option}" {selected}>{option}</option>'
                            dropdown_html += '</select></label>'

                        suggestion = dropdown_html

                        codelist_table.append({
                            "label": label,
                            "value": value,
                            "suggestion": suggestion,
                            "line": line_number,
                            "column": column_number
                        })

        codelist_table.sort(key=lambda x: (x["line"], x["column"]))

    finally:
    # Hochgeladene Datei entfernen – wichtig für parallele Nutzung!
        if os.path.exists(file_path):
            os.remove(file_path)

    return render_template("index.html",
                           result=result,
                           filename=filename,
                           excerpt=excerpt,
                           highlight_line=highlight_line,
                           suggestion="<br>".join(suggestions),
                           syntax_table=syntax_table,
                           codelist_table=codelist_table,
                           codelisten_hinweis="ℹ️ Hinweis: Codelistenprüfung basiert auf EN16931 v14 (gültig ab 2024-11-15).",
                           original_xml=xml)  # <-- korrekt eingerückt und am Ende mit Komma davor

if __name__ == "__main__":
    if hasattr(sys, '_MEIPASS'):
        # .exe-Version (PyInstaller)
        app.run(debug=True, host="127.0.0.1", port=5000)
    else:
        # Lokale Entwicklung oder z. B. Render-Deployment
        app.run(debug=True, host="0.0.0.0", port=5000)
