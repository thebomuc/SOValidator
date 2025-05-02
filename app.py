from flask import Flask, render_template, request, session
import fitz  # PyMuPDF
from lxml import etree
import os
import re
import pandas as pd

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB Upload-Limit
app.secret_key = "supersecret"

DEFAULT_XSD_ROOT = "ZF232_DE/Schema"
DEFAULT_XSLT_PATH = "EN16931-CII-validation.xslt"
EXCEL_PATH = "static/data/EN16931 code lists values v14 - used from 2024-11-15.xlsx"

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
    "Live Status": "Code",
    "Characteristic": "Code",
    "LineReason": "Code",
    "INCOTERMS": "Code",
    "TRANSPORT": "Code",
    "Date": "Code",
    "HybridDocument": "Code",
    "HybridConformance": "Code",
    "Filename": "Code",
    "HybridVersion": "Code",
}
code_sets = {}
try:
    for sheet in codelists:
        df = pd.read_excel(EXCEL_PATH, sheet_name=sheet, engine="openpyxl")
        values = set()
        for col in df.columns:
            values.update(df[col].dropna().astype(str).str.strip().unique())
        code_sets[sheet] = values
except Exception as e:
    print("⚠️ Fehler beim Laden der Codelisten:", e)

def list_all_xsd_files(schema_root):
    xsd_files = []
    for root, _, files in os.walk(schema_root):
        for file in files:
            if file.endswith(".xsd"):
                xsd_files.append(os.path.join(root, file))
    return xsd_files

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

def validate_against_selected_xsds(xml_content, selected_schemas):
    valid_schemas = []
    invalid_schemas = []
    for xsd_path in selected_schemas:
        try:
            schema_doc = etree.parse(xsd_path)
            schema = etree.XMLSchema(schema_doc)
            doc = etree.fromstring(xml_content.encode("utf-8"))
            schema.assertValid(doc)
            valid_schemas.append(os.path.basename(xsd_path))
        except etree.DocumentInvalid as e:
            invalid_schemas.append((os.path.basename(xsd_path), str(e)))
        except Exception as e:
            invalid_schemas.append((os.path.basename(xsd_path), f"Fehler: {str(e)}"))
    return valid_schemas, invalid_schemas

@app.route("/", methods=["GET", "POST"])
def index():
    result = ""
    filename = ""
    excerpt = []
    highlight_line = None
    suggestions = []
    codelist_table = []
    syntax_table = []

    available_schema_folders = {
        "Factur-X_1.07.2_BASIC": os.path.join(DEFAULT_XSD_ROOT, "Factur-X_1.07.2_BASIC"),
        "Factur-X_1.07.2_EN16931": os.path.join(DEFAULT_XSD_ROOT, "Factur-X_1.07.2_EN16931"),
        "Factur-X_1.07.2_EXTENDED": os.path.join(DEFAULT_XSD_ROOT, "Factur-X_1.07.2_EXTENDED"),
        "Factur-X_1.0.05": os.path.join(DEFAULT_XSD_ROOT, "Factur-X_1.0.05"),
        "Factur-X_1.0.06": os.path.join(DEFAULT_XSD_ROOT, "Factur-X_1.0.06"),
        "Factur-X_1.0.07": os.path.join(DEFAULT_XSD_ROOT, "Factur-X_1.0.07")
    }
    schema_choices = [(key, key.replace("_", " ")) for key in available_schema_folders]

    if request.method == "POST":
        file = request.files.get("pdf_file")
        selected_schema_keys = request.form.getlist("schemas")
        if not selected_schema_keys:
            selected_schema_keys = list(available_schema_folders.keys())

        selected_schemas = []
        for key in selected_schema_keys:
            folder = available_schema_folders.get(key)
            if folder:
                selected_schemas.extend(list_all_xsd_files(folder))

        if file:
            filename = file.filename
            file_path = "uploaded.pdf"
            file.save(file_path)

            if not selected_schemas:
                result = "⚠️ Keine XSD-Dateien in den gewählten Schemas gefunden."
            else:
                xml = extract_xml_from_pdf(file_path)
                if not xml:
                    result = "❌ Keine XML-Datei in der PDF gefunden."
                else:
                    result = "✔️ XML ist wohlgeformt."
                    valid, invalid = validate_against_selected_xsds(xml, selected_schemas)
                    if valid:
                        result += "<br><span style='color:green'>✔️ Gültig für:<ul>" + "".join(f"<li>🟢 {x}</li>" for x in valid) + "</ul></span>"
                    if invalid:
                        result += "<br><span style='color:red'>❌ Ungültig für:<ul>" + "".join(f"<li>🔴 {x}: {msg.splitlines()[0]}</li>" for x, msg in invalid) + "</ul></span>"

                    # ➕ Dynamische Attribut- und Elementinhalt-Prüfung für alle bekannten Codelistenfelder
                    for sheet, values in code_sets.items():
                        # Attributprüfung
                        pattern_attr = fr'{sheet}="(.*?)"'
                        for match in re.findall(pattern_attr, xml):
                            value = match.strip()
                            if value not in values:
                                suggestions.append(f"❌ Ungültiger Wert in Attribut {sheet}: {value} ist nicht in der offiziellen Codeliste enthalten.")

                    # ➕ Prüfung auf Codelistenwerte im Elementinhalt per Mapping
                    element_mapping = {
                        "CurrencyCode": "Currency",
                        "CountryID": "Country",
                        "CategoryCode": "5305",
                        "TaxExemptionReasonCode": "VATEX",
                        "TypeCode": "1001",
                        "FunctionCode": "1153",
                        "AllowanceReasonCode": "Allowance",
                        "ChargeReasonCode": "Charge",
                        "MimeCode": "MIME",
                        "Unit": "Unit",
                        "EAS": "EAS",
                        "INCOTERMS": "INCOTERMS",
                        "TRANSPORT": "TRANSPORT",
                        "Filename": "Filename",
                        "HybridVersion": "HybridVersion",
                        "HybridDocument": "HybridDocument",
                        "HybridConformance": "HybridConformance",
                        "LineStatus": "LineStatus",
                        "Characteristic": "Characteristic",
                        "LineReason": "LineReason",
                        "Date": "Date",
                        "Payment": "Payment",
                        "Text": "Text",
                        "ICD": "ICD",
                        "Item": "Item",
                        "VATCAT": "VAT CAT"
                    }
                    for tag, sheet in element_mapping.items():
                        allowed_values = code_sets.get(sheet, set())
                        pattern_elem = fr'<ram:{tag}>(.*?)</ram:{tag}>'
                        for match in re.findall(pattern_elem, xml):
                            value = match.strip()
                            if value not in allowed_values:
                                suggestions.append(f"❌ Ungültiger Wert in <ram:{tag}>: {value} ist nicht in der offiziellen Codeliste ({sheet}) enthalten.")
                    
codelisten_hinweis = "ℹ️ Hinweis: Codelistenprüfung basierend auf 'EN16931 code lists values v14 - used from 2024-11-15.xlsx'."
legend = """<div style='margin-top:1em; font-size:0.9em'>
<strong>Legende:</strong><br>
<span style='color:red;font-weight:bold'>❌ Fehler</span><br>
<span style='color:orange;font-weight:bold'>⚠️ Warnung</span><br>
<span style='color:black'>✔️ Erfolgreich</span>
</div>"""

return render_template("index.html",
    result=result + legend,
    filename=filename,
    excerpt=excerpt,
    highlight_line=highlight_line,
    suggestion="<br>".join(suggestions),
    syntax_table=syntax_table,
    codelist_table=codelist_table,
    codelisten_hinweis=codelisten_hinweis,
    schema_choices=schema_choices
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
