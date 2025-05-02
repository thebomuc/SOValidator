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
EXCEL_PATH = "4. EN16931+FacturX code lists values v14 - used from 2024-11-15.xlsx"

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
try:
    for sheet, column in codelists.items():
        df = pd.read_excel(EXCEL_PATH, sheet_name=sheet, engine="openpyxl")
        code_sets[sheet] = set(df[column].dropna().astype(str).str.strip().unique())
except Exception as e:
    print("⚠️ Fehler beim Vorladen der Codelisten:", e)


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
            error_msg = (
                f"❌ Zeile {err.line}, Spalte {err.column}: {err.message}<br>"
                + "<br>".join(excerpt)
            )
            suggestions.append(error_msg)

        return False, "❌ XML enthält Syntaxfehler:", [], None, suggestions
    else:
        return True, "✔️ XML ist wohlgeformt.", [], None, None


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
            if 'cached_etree' not in globals():
                global cached_etree
                cached_etree = etree.fromstring(xml_content.encode("utf-8"))
            doc = cached_etree
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
    codelist_table = []
    syntax_table = []

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
                valid, msg, excerpt, highlight_line, xml_suggestions = validate_xml(xml)
                result = f"<span style='color:black'>{msg}</span>"
                if xml_suggestions:
                    syntax_table = xml_suggestions

                if valid:
                    xsd_ok, xsd_msg = validate_against_all_xsds(xml, DEFAULT_XSD_ROOT)
                    result += "<br><span style='color:black'>" + xsd_msg + "</span>"

                    if os.path.exists(DEFAULT_XSLT_PATH) and request.form.get("schematron"):
                        sch_issues = validate_with_schematron(xml, DEFAULT_XSLT_PATH)
                        for msg in sch_issues:
                            suggestions.append(f"❌ {msg}")

                xml_lines = xml.splitlines()
                codelist_checks = [
                    (r"<ram:CurrencyCode>(.*?)</ram:CurrencyCode>", code_sets.get("Currency", set()), "CurrencyCode"),
                    (r"<ram:CountryID>(.*?)</ram:CountryID>", code_sets.get("Country", set()), "CountryID"),
                    (r"<ram:CategoryCode>(.*?)</ram:CategoryCode>", code_sets.get("5305", set()), "CategoryCode"),
                    (r"<ram:TaxExemptionReasonCode>(.*?)</ram:TaxExemptionReasonCode>", code_sets.get("VATEX", set()), "VATEX"),
                    (r"<ram:ExchangedDocument>.*?<ram:TypeCode>(.*?)</ram:TypeCode>", code_sets.get("1001", set()), "DocumentType (1001)"),
                    (r"<ram:FunctionCode>(.*?)</ram:FunctionCode>", code_sets.get("1153", set()), "FunctionCode (1153)"),
                    (r"<ram:AllowanceReasonCode>(.*?)</ram:AllowanceReasonCode>", code_sets.get("Allowance", set()), "AllowanceReasonCode"),
                    (r"<ram:ChargeReasonCode>(.*?)</ram:ChargeReasonCode>", code_sets.get("Charge", set()), "ChargeReasonCode"),
                ]
                for pattern, allowed_set, label in codelist_checks:
                    for match in re.finditer(pattern, xml):
                        value = match.group(1).strip()
                        if value not in allowed_set:
                            start_index = match.start(1)
                            before = xml[:start_index]
                            line = before.count("\n") + 1
                            col = start_index - before.rfind("\n")
                            codelist_table.append({
                                "label": label,
                                "value": value,
                                "line": line,
                                "column": col
                            })

                if request.form.get("nonstandard"):
                    nonstandard_tags = detect_nonstandard_tags(xml)
                    for tag in nonstandard_tags:
                        suggestions.append(f"❌ Nicht in verwendeter XSD enthalten: &lt;ram:{tag}&gt;")

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
                           codelisten_hinweis=codelisten_hinweis)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
