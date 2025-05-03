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

# Codelisten vorbereiten
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
    except Exception:
        code_sets[sheet] = set()


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
    if doc.embfile_count() > 0:
        xml_bytes = doc.embfile_get(0)
        try:
            return xml_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return xml_bytes.decode("latin1")
    return None


def validate_xml(xml):
    parser = etree.XMLParser(recover=True)
    try:
        etree.fromstring(xml.encode("utf-8"), parser)
    except etree.XMLSyntaxError:
        pass
    if parser.error_log:
        suggestions = []
        xml_lines = xml.splitlines()
        for err in parser.error_log:
            line = err.line
            msg = f"Zeile {err.line}, Spalte {err.column}: {err.message}"
            suggestions.append(msg)
        return False, "❌ XML enthält Syntaxfehler:", [], None, suggestions
    else:
        return True, "✔️ XML ist wohlgeformt.", [], None, None


def list_all_xsd_files(schema_root):
    xsd_files = []
    for root, _, files in os.walk(schema_root):
        for file in files:
            if file.endswith(".xsd"):
                xsd_files.append(os.path.join(root, file))
    return xsd_files


def validate_against_all_xsds(xml, schema_root):
    results = []
    for xsd_path in list_all_xsd_files(schema_root):
        try:
            schema_doc = etree.parse(xsd_path)
            schema = etree.XMLSchema(schema_doc)
            doc = etree.fromstring(xml.encode("utf-8"))
            schema.assertValid(doc)
            return True, f"✔️ XML entspricht dem XSD ({os.path.basename(xsd_path)})."
        except Exception as e:
            results.append(str(e))
    return False, "❌ XML entspricht keiner XSD:<br>" + "<br>".join(results)


def validate_with_schematron(xml, xslt_path):
    try:
        xml_doc = etree.fromstring(xml.encode("utf-8"))
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

    file = request.files.get("pdf_file")
    if file and file.filename != "":
        filename = file.filename
        file_path = "uploaded.pdf"
        file.save(file_path)
    elif os.path.exists("uploaded.pdf"):
        filename = "uploaded.pdf"
        file_path = "uploaded.pdf"
    else:
        result = "❌ Keine Datei ausgewählt oder hochgeladen."
        return render_template("index.html", result=result, filename=filename)

    xml = extract_xml_from_pdf(file_path)
    if not xml:
        result = "❌ Keine XML-Datei in der PDF gefunden."
    else:
        valid, msg, excerpt, highlight_line, xml_suggestions = validate_xml(xml)
        result = msg
        if xml_suggestions:
            syntax_table = xml_suggestions

        if valid:
            xsd_ok, xsd_msg = validate_against_all_xsds(xml, DEFAULT_XSD_ROOT)
            result += "<br>" + xsd_msg

            if os.path.exists(DEFAULT_XSLT_PATH) and request.form.get("schematron"):
                sch_issues = validate_with_schematron(xml, DEFAULT_XSLT_PATH)
                for msg in sch_issues:
                    suggestions.append(f"❌ {msg}")

        # automatische Codelistenprüfung
        xml_lines = xml.splitlines()
        codelist_checks = []

        # dynamisch Element-Tags bestimmen + manuelle Ergänzungen für abweichende Tags
        for label in codelists:
            tag = ""
            if label.isdigit():
                continue
            elif label == "Currency": tag = "CurrencyCode"
            elif label == "Country": tag = "CountryID"
            elif label == "Language": tag = "LanguageCode"
            elif label == "Filename": tag = "Filename"
            elif label == "VATEX": tag = "TaxExemptionReasonCode"
            elif label == "5305": tag = "CategoryCode"
            else: tag = label + "Code"
            pattern = fr"<ram:{tag}>(.*?)</ram:{tag}>"
            allowed = code_sets.get(label, set())
            codelist_checks.append((pattern, allowed, label))

        # Manuelle Ergänzung spezieller Tags
        manual_checks = {
    "Currency": [r"<ram:InvoiceCurrencyCode>(.*?)</ram:InvoiceCurrencyCode>"],
    "Payment": [r"<ram:SpecifiedTradeSettlementPaymentMeans>.*?<ram:TypeCode>(.*?)</ram:TypeCode>"],
    "VAT CAT": [r"<ram:ApplicableTradeTax>.*?<ram:TypeCode>(.*?)</ram:TypeCode>"],
    "Date": [r'DateTimeString[^>]*?format="(.*?)"'],
    "Line Status": [r"<ram:LineStatusCode>(.*?)</ram:LineStatusCode>"],
    "INCOTERMS": [r"<ram:INCOTERMSCode>(.*?)</ram:INCOTERMSCode>"],
    "TRANSPORT": [r"<ram:TransportModeCode>(.*?)</ram:TransportModeCode>"]
}
        for label, patterns in manual_checks.items():
            for pattern in patterns:
                allowed = code_sets.get(label, set())
                codelist_checks.append((pattern, allowed, label + " (manuell)"))

        # Attribute prüfen (z. B. unitCode)
        attr_patterns = {
            "Unit": [
                r'<ram:BilledQuantity[^>]*?unitCode="(.*?)"',
                r'<ram:InvoicedQuantity[^>]*?unitCode="(.*?)"'
            ]
        }
        for label, patterns in attr_patterns.items():
            for pattern in patterns:
                allowed = code_sets.get(label, set())
                codelist_checks.append((pattern, allowed, f"{label} (Attr)"))

        for pattern, allowed_set, label in codelist_checks:
            regex = re.compile(pattern, re.DOTALL)
            allowed_normalized = {v for v in allowed_set}
            for match in regex.finditer(xml):
                value = match.group(1).strip()
                if value not in allowed_normalized:
                    from difflib import get_close_matches
                    suggestion = ""
                    if value.upper() in allowed_set:
                        suggestion = f"Möglicherweise meinten Sie: „{value.upper()}“"
                    elif value.lower() in allowed_set:
                        suggestion = f"Möglicherweise meinten Sie: „{value.lower()}“"
                    else:
                        candidates = get_close_matches(value, allowed_set, n=3, cutoff=0.6)
                        if candidates:
                            suggestion = "Möglicherweise meinten Sie: " + ", ".join(f"„{c}“" for c in candidates)

                    start = match.start(1)
                    line_number = xml.count("
", 0, start) + 1
                    xml_lines = xml.splitlines()
                    line_text = xml_lines[line_number - 1] if line_number - 1 < len(xml_lines) else ""
                    column_number = line_text.find(value) + 1

                    codelist_table.append({
                        "label": label,
                        "value": value,
                        "suggestion": suggestion,
                        "line": line_number,
                        "column": column_number
                    })

    codelisten_hinweis = "ℹ️ Hinweis: Codelistenprüfung basiert auf EN16931 v14 (gültig ab 2024-11-15)."

    return render_template("index.html",
                           result=result,
                           filename=filename,
                           excerpt=excerpt,
                           highlight_line=highlight_line,
                           suggestion="<br>".join(suggestions),
                           syntax_table=syntax_table,
                           codelist_table=codelist_table,
                           codelisten_hinweis=codelisten_hinweis)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
