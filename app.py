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

def validate_xml(xml_content):
    try:
        etree.fromstring(xml_content.encode('utf-8'))
        return True, "✔️ XML ist wohlgeformt."
    except etree.XMLSyntaxError as e:
        return False, f"❌ Syntaxfehler: {e}"

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
    if request.method == "POST":
        file = request.files["pdf_file"]
        if file:
            file_path = os.path.join("uploaded.pdf")
            file.save(file_path)
            xml = extract_xml_from_pdf(file_path)
            if not xml:
                result = "❌ Keine XML-Datei in der PDF gefunden."
            else:
                valid, msg = validate_xml(xml)
                result = msg + "<br>"
                if valid:
                    xsd_ok, xsd_msg = validate_against_xsd(xml)
                    result += "<br>" + xsd_msg
        else:
            result = "❌ Bitte eine Datei hochladen."
    return render_template("index.html", result=result)
