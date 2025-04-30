from flask import Flask, render_template, request
import fitz
from lxml import etree
import os

app = Flask(__name__)
XSD_PATH = os.path.join("UBL-XSD", "UBL-Invoice-2.1.xsd")

def extract_xml_from_pdf(pdf_path):
    doc = fitz.open(pdf_path)
    for i in range(doc.embfile_count()):
        info = doc.embfile_info(i)
        if info['filename'].endswith('.xml'):
            xml_bytes = doc.embfile_get(i)
            return xml_bytes.decode('utf-8')
    return None

def extract_code_context(xml_lines, error_line, context=2):
    start = max(0, error_line - context - 1)
    end = min(len(xml_lines), error_line + context)
    excerpt = xml_lines[start:end]
    return excerpt, error_line - start - 1

def validate_xml(xml_content):
    try:
        etree.fromstring(xml_content.encode('utf-8'))
        return True, "✔️ XML ist wohlgeformt.", None, None
    except etree.XMLSyntaxError as e:
        msg = str(e)
        line = e.position[0]
        xml_lines = xml_content.splitlines()
        excerpt, highlight_line = extract_code_context(xml_lines, line)
        return False, msg, excerpt, highlight_line

def validate_against_xsd(xml_content):
    try:
        schema_doc = etree.parse(XSD_PATH)
        schema = etree.XMLSchema(schema_doc)
        doc = etree.fromstring(xml_content.encode("utf-8"))
        schema.assertValid(doc)
        return True, "✔️ XML entspricht dem XSD."
    except Exception as e:
        return False, f"❌ XSD-Validierung fehlgeschlagen: {e}"

@app.route("/", methods=["GET", "POST"])
def index():
    result = ""
    filename = ""
    excerpt = []
    highlight_line = None
    suggestion = ""
    if request.method == "POST":
        file = request.files["pdf_file"]
        if file:
            filename = file.filename
            file_path = "uploaded.pdf"
            file.save(file_path)
            xml = extract_xml_from_pdf(file_path)
            if not xml:
                result = "❌ Keine XML-Datei in der PDF gefunden."
            else:
                valid, msg, excerpt, highlight_line = validate_xml(xml)
                result = msg
                if not valid and "not closed" in msg:
                    suggestion = "💡 Vorschlag: Fehlender schließender Tag. Bitte prüfen Sie, ob z. B. ein </Tag> fehlt."
                elif not valid and "expected '>'" in msg:
                    suggestion = "💡 Vorschlag: Tag nicht korrekt abgeschlossen. Möglicherweise fehlt ein >"
                elif valid:
                    xsd_ok, xsd_msg = validate_against_xsd(xml)
                    result += "<br>" + xsd_msg
    return render_template("index.html", result=result, filename=filename, excerpt=excerpt, highlight_line=highlight_line, suggestion=suggestion)
