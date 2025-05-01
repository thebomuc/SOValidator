from flask import Flask, render_template, request
import fitz  # PyMuPDF
from lxml import etree
import os
import re

app = Flask(__name__)

# Feste XSD/XSLT-Pfade
DEFAULT_XSD_ROOT = "ZF232_DE/Schema"
DEFAULT_XSLT_PATH = "EN16931-CII-validation.xslt"


def list_all_xsd_files(schema_root):
    xsd_files = []
    for root, _, files in os.walk(schema_root):
        for file in files:
            if file.endswith(".xsd"):
                xsd_files.append(os.path.join(root, file))
    return xsd_files

def extract_xml_from_pdf(pdf_path):
    doc = fitz.open(pdf_path)
    print(f"📦 Anzahl eingebetteter Dateien: {doc.embfile_count()}")
    for i in range(doc.embfile_count()):
        info = doc.embfile_info(i)
        name = info.get("filename", "").lower()
        print(f"📄 Gefunden: {name}")
        if name.endswith(".xml"):
            xml_bytes = doc.embfile_get(i)
            try:
                return xml_bytes.decode("utf-8")
            except UnicodeDecodeError:
                return xml_bytes.decode("latin1")
    if doc.embfile_count() > 0:
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

def extract_guideline_id(xml_content):
    match = re.search(r"<ram:GuidelineSpecifiedDocumentContextParameter>\s*<ram:ID>(.*?)</ram:ID>", xml_content)
    return match.group(1) if match else "unbekannt"

def validate_xml(xml_content):
    try:
        etree.fromstring(xml_content.encode("utf-8"))
        return True, "✔️ XML ist wohlgeformt.", None, None
    except etree.XMLSyntaxError as e:
        msg = str(e)
        line = e.position[0]
        xml_lines = xml_content.splitlines()
        excerpt, highlight_line = extract_code_context(xml_lines, line)
        return False, msg, excerpt, highlight_line

def detect_nonstandard_tags(xml_content):
    known_tags = {"ID", "Name", "CityName", "PostcodeCode", "LineOne", "StreetName", "Country", "URIID"}
    nonstandard = set()
    tag_pattern = re.compile(r"<(/?)(ram:)(\w+)")
    for match in tag_pattern.findall(xml_content):
        tagname = match[2]
        if tagname not in known_tags:
            nonstandard.add(tagname)
    return sorted(nonstandard)

def validate_against_all_xsds(xml_content, schema_root):
    results = []
    for xsd_path in list_all_xsd_files(schema_root):
        try:
            schema_doc = etree.parse(xsd_path)
            schema = etree.XMLSchema(schema_doc)
            doc = etree.fromstring(xml_content.encode("utf-8"))
            schema.assertValid(doc)
            return True, f"✔️ XML entspricht dem XSD ({os.path.basename(xsd_path)})."
        except etree.DocumentInvalid as e:
            errors = schema.error_log.filter_from_errors()
            details = "".join([f"<li>Zeile {err.line}: {err.message}</li>" for err in errors])
            results.append(f"<details><summary><strong>{os.path.basename(xsd_path)}</strong></summary><ul>{details}</ul></details>")
        except Exception as e:
            results.append(f"<details><summary><strong>{os.path.basename(xsd_path)}</strong></summary><pre>{e}</pre></details>")
    return False, "❌ XSD-Validierung fehlgeschlagen:" + "<br>" + "<br>".join(results)

def validate_with_schematron(xml_content, xslt_path):
    try:
        xml_doc = etree.fromstring(xml_content.encode("utf-8"))
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
                guideline_id = extract_guideline_id(xml)
                result = msg + f"<br>📋 Guideline-Profil: {guideline_id}"
                if not valid:
                    if "not closed" in msg:
                        suggestions.append("💡 Vorschlag: Fehlender schließender Tag. Bitte prüfen Sie, ob z. B. ein </Tag> fehlt.")
                    if "expected '>'" in msg:
                        suggestions.append("💡 Vorschlag: Tag nicht korrekt abgeschlossen. Möglicherweise fehlt ein >")
                elif valid:
                    xsd_ok, xsd_msg = validate_against_all_xsds(xml, DEFAULT_XSD_ROOT)
                    result += "<br>" + xsd_msg
                    if "Failed to parse QName" in xsd_msg:
                        suggestions.append("💡 Vorschlag: In diesem Feld ist ein Qualified Name (QName) erforderlich. Prüfen Sie, ob versehentlich ein URL-Wert wie 'https:' angegeben wurde.")
                    if os.path.exists(DEFAULT_XSLT_PATH):
                        sch_issues = validate_with_schematron(xml, DEFAULT_XSLT_PATH)
                        suggestions.extend([f"🧾 Schematron: {msg}" for msg in sch_issues])
                nonstandard_tags = detect_nonstandard_tags(xml)
                if nonstandard_tags:
                    for tag in nonstandard_tags:
                        suggestions.append(f"⚠️ Hinweis: Nicht-standardisiertes Tag erkannt: &lt;ram:{tag}&gt;. Dieses Tag ist möglicherweise nicht Teil des offiziellen Schemas.")
                
    return render_template("index.html", result=result, filename=filename, excerpt=excerpt, highlight_line=highlight_line, suggestion="<br>".join(suggestions))

if __name__ == "__main__":
    app.run(debug=True)
