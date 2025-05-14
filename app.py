from flask import Flask, render_template, request
import fitz  # PyMuPDF
from lxml import etree
import os
import re
import pandas as pd
from openpyxl import load_workbook
from difflib import get_close_matches
import sys

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

@app.route("/", methods=["GET", "POST"])
def index():
    result = ""
    filename = ""
    excerpt = []
    highlight_line = None
    suggestions = []
    codelist_table = []
    syntax_table = []

    file = request.files.get("pdf_file")
    if file and file.filename:
        filename = file.filename
        file_path = "uploaded.pdf"
        try:
            file.save(file_path)
        except PermissionError:
            result = "❌ Schreibfehler: Kann Datei nicht speichern (PermissionError)"
            return render_template("index.html", result=result)
    elif os.path.exists("uploaded.pdf"):
        filename = "uploaded.pdf"
        file_path = "uploaded.pdf"
    else:
        result = "❌ Keine Datei ausgewählt oder hochgeladen."
        return render_template("index.html", result=result)

    xml = extract_xml_from_pdf(file_path)
    if not xml:
        result = "❌ Keine XML-Datei in der PDF gefunden."
    else:
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
                        if value == "":
                            suggestion = "⚠️ Kein Wert angegeben"
                        elif value.upper() in allowed_set:
                            suggestion = f"Möglicherweise meinten Sie: „{value.upper()}“"
                        elif value.lower() in allowed_set:
                            suggestion = f"Möglicherweise meinten Sie: „{value.lower()}“"
                        else:
                            close_matches = get_close_matches(value, allowed_set, n=3, cutoff=0.6)
                            suggestion = "Möglicherweise meinten Sie: " + ", ".join(f"„{m}“" for m in close_matches) if close_matches else "–"

                        start = match.start(1) if match.lastindex else match.start()
                        line_number = xml.count("\n", 0, start) + 1
                        line_text = xml_lines[line_number - 1] if line_number - 1 < len(xml_lines) else ""
                        offset = start - sum(len(l) + 1 for l in xml_lines[:line_number - 1])
                        column_number = offset + 1

                        codelist_table.append({
                            "label": label,
                            "value": value,
                            "suggestion": suggestion,
                            "line": line_number,
                            "column": column_number
                        })

        codelist_table.sort(key=lambda x: (x["line"], x["column"]))

    return render_template("index.html",
                           result=result,
                           filename=filename,
                           excerpt=excerpt,
                           highlight_line=highlight_line,
                           suggestion="<br>".join(suggestions),
                           syntax_table=syntax_table,
                           codelist_table=codelist_table,
                           codelisten_hinweis="ℹ️ Hinweis: Codelistenprüfung basiert auf EN16931 v14 (gültig ab 2024-11-15).")

if __name__ == "__main__":
    if hasattr(sys, '_MEIPASS'):
        # .exe-Version (PyInstaller)
        app.run(debug=True, host="127.0.0.1", port=5000)
    else:
        # Lokale Entwicklung oder z. B. Render-Deployment
        app.run(debug=True, host="0.0.0.0", port=5000)
