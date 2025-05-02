from flask import Flask, render_template, request, send_file
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
CORRECTED_PDF_PATH = "static/data/corrected_invoice.pdf"

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


def embed_xml_in_pdf(original_pdf_path, xml_str, output_pdf_path):
    doc = fitz.open(original_pdf_path)
    for i in range(doc.embfile_count()):
        info = doc.embfile_info(i)
        if info.get("filename", "").endswith(".xml"):
            doc.embfile_del(i)
    doc.embfile_add("factur-x.xml", xml_str.encode("utf-8"))
    doc.save(output_pdf_path, garbage=4, deflate=True)
    doc.close()


def apply_corrections_to_xml(xml_content, corrections):
    for correction in corrections:
        original = correction['value']
        suggestion = correction['suggestion']
        if suggestion:
            value_only = original.split(" ")[0]
            xml_content = re.sub(f'>{value_only}<', f'>{suggestion}<', xml_content)
    return xml_content


def index():
    result = ""
    filename = ""
    excerpt = []
    highlight_line = None
    suggestions = []
    codelist_table = []
    syntax_table = []
    corrected_xml = None
    show_download = False

    if request.method == "POST":
        if "fix_all" in request.form:
            xml = request.form.get("raw_xml", "")
            import json
            codelist_table = json.loads(request.form.get("raw_table", "[]"))
            corrected_xml = apply_corrections_to_xml(xml, codelist_table)
            filename = request.form.get("original_filename", "uploaded.pdf")
            corrected_filename = "static/data/" + filename.rsplit(".", 1)[0] + "_SOC.pdf"
            embed_xml_in_pdf("uploaded.pdf", corrected_xml, corrected_filename)
            global CORRECTED_PDF_PATH
            CORRECTED_PDF_PATH = corrected_filename
            result = "✔️ Alle vorgeschlagenen Korrekturen wurden übernommen."
            show_download = True
        else:
            file = request.files.get("pdf_file")
            if file:
                filename = file.filename
                file_path = "uploaded.pdf"
                file.save(file_path)
                xml = extract_xml_from_pdf(file_path)
                if not xml:
                    result = "❌ Keine XML-Datei in der PDF gefunden."
                else:
                    parser = etree.XMLParser(recover=True)
                    try:
                        etree.fromstring(xml.encode("utf-8"), parser)
                        result = "✔️ XML ist wohlgeformt."
                    except:
                        result = "❌ XML enthält Syntaxfehler."

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
                                    suggestion = value.upper()
                                elif value.lower() in allowed_set:
                                    suggestion = value.lower()
                                line = xml[:match.start(1)].count("\n") + 1
                                codelist_table.append({
                                    "label": label,
                                    "value": value,
                                    "line": line,
                                    "column": 1,
                                    "suggestion": suggestion
                                })

    return render_template("index.html",
                           result=result,
                           filename=filename,
                           excerpt=excerpt,
                           highlight_line=highlight_line,
                           suggestion="<br>".join(suggestions),
                           syntax_table=syntax_table,
                           codelist_table=codelist_table,
                           raw_xml=xml if request.method == "POST" else "",
                           raw_table=codelist_table,
                           corrected_xml=corrected_xml,
                           show_download=show_download,
                           codelisten_hinweis="Codelistenprüfung basierend auf Excel-Vorgabe")


@app.route("/download")
def download():
    return send_file(CORRECTED_PDF_PATH, as_attachment=True, download_name=os.path.basename(CORRECTED_PDF_PATH))


app.add_url_rule("/", "index", index, methods=["GET", "POST"])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
