from flask import Flask, render_template, request
import fitz  # PyMuPDF
from lxml import etree
import os

app = Flask(__name__)
XSD_PATH = os.path.join("UBL-XSD", "UBL-Invoice-2.1.xsd")

def extract_xml_from_pdf(pdf_path):
    doc = fitz.open(pdf_path)
    print(f"\U0001f4e6 Anzahl eingebetteter Dateien: {doc.embfile_count()}")

    for i in range(doc.embfile_count()):
        info = doc.embfile_info(i)
        name = info.get("filename", "").lower()
        print(f"\U0001f4c4 Gefunden: {name}")
        if name.endswith(".xml"):
            xml_bytes = doc.embfile_get(i)
            try:
                return xml_bytes.decode("utf-8")
            except UnicodeDecodeError:
                return xml_bytes.decode("latin1")

    if doc.embfile_count() > 0:
        print("⚠️ Keine Datei mit .xml-Endung – versuche erste eingebettete Datei")
        xml_bytes = doc.embfile_get(0)
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
                if not valid:
                    if "not closed" in msg:
                        suggestion = "💡 Vorschlag: Fehlender schließender Tag. Bitte prüfen Sie, ob z. B. ein </Tag> fehlt."
                    elif "expected '>'" in msg:
                        suggestion = "💡 Vorschlag: Tag nicht korrekt abgeschlossen. Möglicherweise fehlt ein >"
                elif valid:
                    xsd_ok, xsd_msg = validate_against_xsd(xml)
                    result += "<br>" + xsd_msg
                    if "Failed to parse QName" in xsd_msg:
                        suggestion = "💡 Vorschlag: In diesem Feld ist ein Qualified Name (QName) erforderlich. Prüfen Sie, ob versehentlich ein URL-Wert wie 'https:' angegeben wurde."
    return render_template("index.html", result=result, filename=filename, excerpt=excerpt, highlight_line=highlight_line, suggestion=suggestion)

if __name__ == "__main__":
    app.run(debug=True)
