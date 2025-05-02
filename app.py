from flask import Flask, render_template, request
import fitz  # PyMuPDF
from lxml import etree
import os
import re
import pandas as pd
from openpyxl import load_workbook

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB Upload-Limit

# Feste Pfade
DEFAULT_XSD_ROOT = "ZF232_DE/Schema"
DEFAULT_XSLT_PATH = "EN16931-CII-validation.xslt"
EXCEL_PATH = "static/data/4. EN16931+FacturX code lists values v14 - used from 2024-11-15.xlsx"

# Code-Listen vorbereiten
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
        if column in df.columns:
            values = df[column].dropna().astype(str).str.strip().unique()
            code_sets[sheet] = set(values)
        else:
            df = pd.read_excel(EXCEL_PATH, sheet_name=sheet, engine="openpyxl", header=None)
            flat = df.values.flatten()
            cleaned = set(str(v).strip() for v in flat if pd.notnull(v))
            code_sets[sheet] = cleaned
    except Exception as e:
        print(f"❌ Fehler beim Laden von '{sheet}': {e}")

def extract_xml_from_pdf(pdf_path):
    doc = fitz.open(pdf_path)
    for i in range(doc.embfile_count()):
        info = doc.embfile_info(i)
        name = info.get("filename", "").lower()
        if name.endswith(".xml"):
            xml_bytes = doc.embfile_get(i)
            try:
                return xml_bytes.decode("utf-8")
            except UnicodeDecodeError:
                return xml_bytes.decode("latin1")
    return None

def validate_xml(xml_content):
    parser = etree.XMLParser(recover=True)
    try:
        etree.fromstring(xml_content.encode("utf-8"), parser)
    except etree.XMLSyntaxError:
        pass
    if parser.error_log:
        suggestions = []
        xml_lines = xml_content.splitlines()
        for err in parser.error_log:
            excerpt = xml_lines[max(0, err.line - 3):err.line + 2]
            suggestions.append(f"❌ Zeile {err.line}, Spalte {err.column}: {err.message}\n" + "\n".join(excerpt))
        return False, "❌ XML enthält Syntaxfehler:", [], None, suggestions
    return True, "✔️ XML ist wohlgeformt.", [], None, None

def validate_against_all_xsds(xml_content, schema_root):
    results = []
    for xsd_path in list_all_xsd_files(schema_root):
        try:
            schema_doc = etree.parse(xsd_path)
            schema = etree.XMLSchema(schema_doc)
            doc = etree.fromstring(xml_content.encode("utf-8"))
            schema.assertValid(doc)
            return True, f"✔️ XML entspricht dem XSD ({os.path.basename(xsd_path)})."
        except etree.DocumentInvalid:
            errors = schema.error_log.filter_from_errors()
            details = "".join([f"<li>Zeile {err.line}: {err.message}</li>" for err in errors])
            results.append(f"<details><summary><strong>{os.path.basename(xsd_path)}</strong></summary><ul>{details}</ul></details>")
        except Exception as e:
            results.append(f"<details><summary><strong>{os.path.basename(xsd_path)}</strong></summary><pre>{e}</pre></details>")
    return False, "❌ XSD-Validierung fehlgeschlagen:" + "<br>" + "<br>".join(results)

def list_all_xsd_files(schema_root):
    xsd_files = []
    for root, _, files in os.walk(schema_root):
        for file in files:
            if file.endswith(".xsd"):
                xsd_files.append(os.path.join(root, file))
    return xsd_files

def index():
    result = ""
    filename = ""
    excerpt = []
    highlight_line = None
    suggestions = []
    codelist_table = []
    syntax_table = []
    if request.method == "POST":
        file = request.files.get("pdf_file")
        if file:
            filename = file.filename
            file_path = "uploaded.pdf"
            file.save(file_path)
            xml = extract_xml_from_pdf(file_path)
            if not xml:
                result = "❌ Keine XML-Datei in der PDF gefunden."
            else:
                valid, msg, excerpt, highlight_line, xml_suggestions = validate_xml(xml)
                result = f"<span style='color:black'>{msg}</span>"
                if xml_suggestions:
                    syntax_table = xml_suggestions
                if valid:
                    xsd_ok, xsd_msg = validate_against_all_xsds(xml, DEFAULT_XSD_ROOT)
                    result += "<br><span style='color:black'>" + xsd_msg + "</span>"
                codelist_checks = [
                    (r"<ram:CurrencyCode>(.*?)</ram:CurrencyCode>", code_sets.get("Currency", set()), "CurrencyCode"),
                    (r"<ram:CountryID>(.*?)</ram:CountryID>", code_sets.get("Country", set()), "CountryID"),
                    (r"<ram:CategoryCode>(.*?)</ram:CategoryCode>", code_sets.get("5305", set()), "CategoryCode"),
                ]
                for pattern, allowed_set, label in codelist_checks:
                    for match in re.finditer(pattern, xml):
                        value = match.group(1).strip()
                        if value not in allowed_set:
                            suggestion = ""
                            if value.upper() in allowed_set:
                                suggestion = f" Möglicherweise meinten Sie \"{value.upper()}\"."
                            elif value.lower() in allowed_set:
                                suggestion = f" Möglicherweise meinten Sie \"{value.lower()}\"."
                            line = xml[:match.start(1)].count("\n") + 1
                            codelist_table.append({
                                "label": label,
                                "value": value,
                                "line": line,
                                "column": 1,
                                "suggestion": suggestion.strip()
                            })
    return render_template("index.html",
                           result=result,
                           filename=filename,
                           excerpt=excerpt,
                           highlight_line=highlight_line,
                           suggestion="<br>".join(suggestions),
                           syntax_table=syntax_table,
                           codelist_table=codelist_table,
                           codelisten_hinweis="Codelistenprüfung basierend auf Excel-Vorgabe")

app.add_url_rule("/", "index", index, methods=["GET", "POST"])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
