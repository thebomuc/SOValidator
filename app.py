from flask import Flask, render_template, request, session
import fitz  # PyMuPDF
from lxml import etree
import os
import re
import pandas as pd

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB Upload-Limit
app.secret_key = "supersecret"  # notwendig für Session

# Pfade
DEFAULT_XSD_ROOT = "ZF232_DE/Schema"
EXCEL_PATH = "static/data/EN16931 code lists values v14 - used from 2024-11-15.xlsx"
DEFAULT_XSLT_PATH = "EN16931-CII-validation.xslt"

# Lade Codelisten
codelists = {
    "Currency": "Alphabetic Code",
    "Country": "Alpha-2 code",
    "5305": "Code",
    "VATEX": "CODE",
    "1153": "Code Values",
    "1001": "Code",
    "Allowance": "Code",
    "Charge": "Code",
}
code_sets = {}
try:
    for sheet, column in codelists.items():
        df = pd.read_excel(EXCEL_PATH, sheet_name=sheet, engine="openpyxl")
        code_sets[sheet] = set(df[column].dropna().astype(str).str.strip().unique())
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

def extract_code_context(xml_lines, error_line, context=2):
    start = max(0, error_line - context - 1)
    end = min(len(xml_lines), error_line + context)
    excerpt = xml_lines[start:end]
    return excerpt, error_line - start - 1

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
            excerpt, _ = extract_code_context(xml_lines, err.line)
            error_line_index = err.line - max(0, err.line - 3) - 1
            if 0 <= error_line_index < len(excerpt):
                line_content = excerpt[error_line_index]
                excerpt[error_line_index] = (
                    f"<span style='color:red;font-weight:bold;text-decoration:underline'>{line_content}</span>"
                )
            suggestions.append(f"❌ Zeile {err.line}, Zeichen {err.column}: {err.message}<br>" + "<br>".join(excerpt))
        return False, "❌ XML enthält Syntaxfehler:", [], None, suggestions
    else:
        return True, "✔️ XML ist wohlgeformt.", [], None, None

def validate_against_selected_xsds(xml_content, selected_schemas):
    results = []
    success = False
    for xsd_path in selected_schemas:
        try:
            schema_doc = etree.parse(xsd_path)
            schema = etree.XMLSchema(schema_doc)
            doc = etree.fromstring(xml_content.encode("utf-8"))
            schema.assertValid(doc)
            results.append(f"✔️ XML entspricht dem XSD ({os.path.basename(xsd_path)}).")
            success = True
        except etree.DocumentInvalid as e:
            errors = schema.error_log.filter_from_errors()
            details = "".join([f"<li>Zeile {err.line}: {err.message}</li>" for err in errors])
            results.append(f"❌ Fehler bei {os.path.basename(xsd_path)}:<ul>{details}</ul>")
        except Exception as e:
            results.append(f"❌ Fehler bei {os.path.basename(xsd_path)}:<pre>{e}</pre>")
    return success, "<br>".join(results)

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

        if file and selected_schemas:
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
                    xsd_ok, xsd_msg = validate_against_selected_xsds(xml, selected_schemas)
                    result += "<br><span style='color:black'>" + xsd_msg + "</span>"

        # Session speichern
        session['selected_schemas'] = selected_schema_keys

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
