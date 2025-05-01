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
    from openpyxl import load_workbook
    import pandas as pd

    # Pfad zur Excel-Datei im Projektverzeichnis (muss in Render vorhanden sein)
    excel_path = "static/data/EN16931 code lists values v14 - used from 2024-11-15.xlsx"
    codelists = {
        "Currency": "Alphabetic Code",
        "Country": "Alpha-2 code",
        "5305": "Code",
        "VATEX": "Code",
        "1153": "Code",
        "1001": "Code",
        "Allowance": "Code",
        "Charge": "Code",
    }
    code_sets = {}
    try:
        for sheet, column in codelists.items():
            df = pd.read_excel(excel_path, sheet_name=sheet, engine="openpyxl")
            code_sets[sheet] = set(df[column].dropna().astype(str).str.strip().unique())
    except Exception as e:
        print("⚠️ Fehler beim Laden der Codelisten:", e)
    result = ""
    filename = ""
    excerpt = []  # wird ggf. später mit markierten Zeilen überschrieben
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
                result = f"<span style='color:orange;font-weight:bold'>{msg}</span>"
                if not valid:
                    if "not closed" in msg:
                        suggestions.append("💡 Vorschlag: Fehlender schließender Tag. Bitte prüfen Sie, ob z. B. ein </Tag> fehlt.")
                    if "expected '>'" in msg:
                        suggestions.append("💡 Vorschlag: Tag nicht korrekt abgeschlossen. Möglicherweise fehlt ein >")
                elif valid:
                    xsd_ok, xsd_msg = validate_against_all_xsds(xml, DEFAULT_XSD_ROOT)
                    result += "<br><span style='color:darkorange'>" + xsd_msg + "</span>"
                    if "Failed to parse QName" in xsd_msg:
                        suggestions.append("💡 Vorschlag: In diesem Feld ist ein Qualified Name (QName) erforderlich. Prüfen Sie, ob versehentlich ein URL-Wert wie 'https:' angegeben wurde.")
                    if os.path.exists(DEFAULT_XSLT_PATH):
                        sch_issues = validate_with_schematron(xml, DEFAULT_XSLT_PATH)
                        for msg in sch_issues:
                            suggestions.append(f"🧾 Schematron: <span style='color:blue;font-weight:bold'>{msg}</span>")
                nonstandard_tags = detect_nonstandard_tags(xml)
                # Codelistenprüfung ausführen
                codelist_checks = [
                    (r"<ram:CurrencyCode>(.*?)</ram:CurrencyCode>", code_sets.get("Currency", set()), "CurrencyCode"),
                    (r"<ram:CountryID>(.*?)</ram:CountryID>", code_sets.get("Country", set()), "CountryID"),
                    (r"<ram:CategoryCode>(.*?)</ram:CategoryCode>", code_sets.get("5305", set()), "CategoryCode"),
                    (r"<ram:TaxExemptionReasonCode>(.*?)</ram:TaxExemptionReasonCode>", code_sets.get("VATEX", set()), "VATEX"),
                    (r"<ram:TypeCode>(.*?)</ram:TypeCode>", code_sets.get("1001", set()), "DocumentType (1001)"),
                    (r"<ram:FunctionCode>(.*?)</ram:FunctionCode>", code_sets.get("1153", set()), "FunctionCode (1153)"),
                    (r"<ram:AllowanceReasonCode>(.*?)</ram:AllowanceReasonCode>", code_sets.get("Allowance", set()), "AllowanceReasonCode"),
                    (r"<ram:ChargeReasonCode>(.*?)</ram:ChargeReasonCode>", code_sets.get("Charge", set()), "ChargeReasonCode"),
                ]
                for pattern, allowed_set, label in codelist_checks:
                    for match in re.findall(pattern, xml):
                        if match.strip() not in allowed_set:
                            suggestions.append(f"❌ Ungültiger {label}: {match.strip()} ist nicht in der offiziellen Codeliste enthalten.")
                            # Markierung im XML-Quelltext (für Anzeige)
                            highlight_tag = f">{match.strip()}<"
                            xml_lines = xml.splitlines()
                            for i, line in enumerate(xml_lines):
                                if highlight_tag in line:
                                    excerpt, highlight_line = extract_code_context(xml_lines, i + 1)
                                    # Färbe den betroffenen Wert im XML-Auszug rot ein
                                    excerpt[highlight_line] = excerpt[highlight_line].replace(match.strip(), f"<span style='color:red;font-weight:bold'>{match.strip()}</span>")
                                    break

                if nonstandard_tags:
                    for tag in nonstandard_tags:
                        suggestions.append(f"⚠️ Hinweis: Nicht-standardisiertes Tag erkannt: &lt;ram:{tag}&gt;. Dieses Tag ist möglicherweise nicht Teil des offiziellen Schemas.")
                
    suggestions.append("ℹ️ Hinweis: Codelistenprüfung basierend auf 'EN16931 code lists values v14 - used from 2024-11-15.xlsx'.")
    legend = """<div style='margin-top:1em; font-size:0.9em'>
<strong>Legende:</strong><br>
<span style='color:red;font-weight:bold'>Rot:</span> Ungültiger Codewert laut Codeliste<br>
<span style='color:blue;font-weight:bold'>Blau:</span> Verstoß gegen EN16931-Regel (Schematron)<br>
<span style='color:orange;font-weight:bold'>Orange:</span> XML-Syntaxfehler<br>
<span style='color:darkorange'>Dunkelorange:</span> XSD-Fehler<br>
</div>"""
    return render_template("index.html", result=result + legend, filename=filename, excerpt=excerpt, highlight_line=highlight_line, suggestion="<br>".join(suggestions))

if __name__ == "__main__":
    app.run(debug=True)
