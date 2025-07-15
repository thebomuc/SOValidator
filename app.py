from flask import Flask, render_template, request, send_file, session
from markupsafe import Markup
import zipfile
import fitz  # PyMuPDF
import tempfile
from lxml import etree
import os
import re
import pandas as pd
from difflib import get_close_matches
import sys
import xml.etree.ElementTree as ET
import re
import hashlib

def xml_escape_values(xml):
    """
    Ersetzt in allen XML-Elementwerten die Zeichen &, <, >, ", ' durch die korrekten Entities.
    """
    def escape_match(match):
        value = match.group(1)
        value = (value.replace('&', '&amp;')
                      .replace('<', '&lt;')
                      .replace('>', '&gt;')
                      .replace('"', '&quot;')
                      .replace("'", '&apos;'))
        return f">{value}<"
    # Nur Inhalte escapen, nicht Tags!
    return re.sub(r'>([^<]+)<', escape_match, xml)

def replace_at_positions(xml, corrections):
    """Ersetzt die angegebenen Zeichenbereiche durch die neuen Werte (von hinten nach vorne!)."""
    print("--- XML vor Korrekturen (repr, 1000 Zeichen): ---")
    print(repr(xml[:1000]))
    corr_list = []
    for c in corrections:
        parts = c.split("|")
        if len(parts) == 4:
            label, start, end, new_value = parts
            start, end = int(start), int(end)
            print(f"Korrektur: {label} {start}:{end} → '{xml[start:end]}' → '{new_value}'")
            corr_list.append((start, end, new_value))
    # Sortieren: von hinten nach vorne!
    corr_list.sort(reverse=True, key=lambda x: x[0])
    xml = list(xml)

    for start, end, new_value in corr_list:
        print(f"Position {start}:{end} → '{''.join(xml[start:end])}'")
        print(f"Ersetze [{start}:{end}] '{''.join(xml[start:end])}' → '{new_value}'")
        xml[start:end] = new_value
    return "".join(xml)
    result = "".join(xml)
    print("--- XML nach Korrekturen (repr, 1000 Zeichen): ---")
    print(repr(result[:1000]))
    return result

def replace_nth_tag_value(xml, tag, old, new, n):
    """
    Ersetzt das n-te Vorkommen von <tag>old</tag> durch <tag>new</tag>
    """
    pattern = fr'(<{tag}>)({re.escape(old)})(</{tag}>)'
    matches = list(re.finditer(pattern, xml))
    if len(matches) < n:
        print(f"WARN: {n}. Vorkommen von <{tag}>{old}</{tag}> nicht gefunden!")
        return xml
    m = matches[n-1]
    start, end = m.start(2), m.end(2)
    xml_new = xml[:start] + new + xml[end:]
    print(f"Ersetze das {n}. <{tag}>{old}</{tag}> zu <{tag}>{new}</{tag}>")
    return xml_new

    # Index des n-ten Vorkommens
    match = matches[n-1]
    start, end = match.start(2), match.end(2)
    before = xml[max(0, start-50):end+50]
    print(f"Vor Ersetzung #{n}: {before}")

    # Ersetze genau das n-te Vorkommen
    corrected_xml = xml[:start] + new + xml[end:]
    after = corrected_xml[max(0, start-50):start+len(new)+50]
    print(f"Nach Ersetzung #{n}: {after}")

    return corrected_xml
    
# Dynamischer Pfad für PyInstaller (sys._MEIPASS) für statische Ressourcen und Templates
if hasattr(sys, '_MEIPASS'):
    base_path = sys._MEIPASS
    template_folder = os.path.join(base_path, 'templates')
    static_folder = os.path.join(base_path, 'static')
    DEFAULT_XSLT_PATH = os.path.join(base_path, 'EN16931-CII-validation.xslt')
    EXCEL_PATH = os.path.join(base_path, 'static', 'data', '4. EN16931+FacturX code lists values v14 - used from 2024-11-15.xlsx')
    DEFAULT_XSD_ROOT = os.path.join(base_path, 'ZF232_DE', 'Schema')
else:
    base_path = os.path.abspath(".")
    template_folder = "templates"
    static_folder = "static"
    DEFAULT_XSLT_PATH = "EN16931-CII-validation.xslt"
    EXCEL_PATH = os.path.join("static", "data", "4. EN16931+FacturX code lists values v14 - used from 2024-11-15.xlsx")
    DEFAULT_XSD_ROOT = os.path.join("ZF232_DE", "Schema")

app = Flask(__name__, template_folder=template_folder, static_folder=static_folder)
app.secret_key = "supergeheimer-schlüssel"
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

MANDATORY_TAGS = [
    "ram:ID", "ram:IssueDateTime", "ram:SellerTradeParty", "ram:BuyerTradeParty",
    "ram:SpecifiedSupplyChainTradeDelivery", "ram:ApplicableHeaderTradeSettlement",
    "ram:CountryID", "ram:InvoiceCurrencyCode", "ram:LineID", "ram:TypeCode"
]

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
        values = df[column].dropna().astype(str).str.strip().unique()
        code_sets[sheet] = set(values)
    except Exception:
        code_sets[sheet] = set()

def escape_all_text(xml):
    # Parst die XML, escapt Text- und Tail-Inhalte, serialisiert zurück
    root = ET.fromstring(xml)
    def recurse(elem):
        if elem.text:
            elem.text = (
                elem.text.replace('&', '&amp;')
                         .replace('<', '&lt;')
                         .replace('>', '&gt;')
                         .replace('"', '&quot;')
                         .replace("'", '&apos;')
            )
        if elem.tail:
            elem.tail = (
                elem.tail.replace('&', '&amp;')
                         .replace('<', '&lt;')
                         .replace('>', '&gt;')
                         .replace('"', '&quot;')
                         .replace("'", '&apos;')
            )
        for child in elem:
            recurse(child)
    recurse(root)
    return ET.tostring(root, encoding="unicode")

def check_errorcodes(xml, file_path):
    reasons = []
    allowed_xml_names = [
        "ZUGFeRD-invoice.xml", "zugferd-invoice.xml", "factur-x.xml", "xrechnung.xml"
    ]
    # --- E0051: PDF-Prüfungen ohne VeraPDF ---
    try:
        doc = fitz.open(file_path)
        # 1. Keine eingebettete XML?
        if doc.embfile_count() == 0:
            reasons.append("E0051: PDF enthält keine eingebettete Rechnung (weder XML, noch irgendetwas anderes).")
        # 2. PDF-Version prüfen
        pdf_version = None
        if hasattr(doc, "pdf_version"):
            pdf_version = doc.pdf_version
        else:
            meta = doc.metadata or {}
            pdf_version = meta.get("pdf:PDFVersion") or meta.get("format")
        if not pdf_version or "1.7" not in str(pdf_version):
            reasons.append(f"E0051: PDF hat PDF-Version: ({pdf_version}). Bei FACTUR-X sagt die Norm 1.7")

        # 3. PDF/A-3-Kennung in Metadaten (nicht rechtssicher!)
        meta_str = str(doc.metadata)
        pdfa3_hint = False
        if "/PDF/A-3" in meta_str or "/pdfaid:part>3<" in meta_str:
            pdfa3_hint = True
        for xref in range(1, doc.xref_length()):
            try:
                stream = doc.xref_stream(xref)
                if b'PDF/A-3' in stream or b'pdfaid:part>3<' in stream:
                    pdfa3_hint = True
                    break
            except Exception:
                pass
        if not pdfa3_hint:
            if check_custom_xmp(file_path):
                pass  # Kein Fehler, da Custom-XMP vorhanden!
            else:
                reasons.append("E0051: PDF scheint kein PDF/A-3 zu sein (Metadatenprüfung, unsicher).")
        # 4. Embedded-XML-Filename prüfen
        if doc.embfile_count() > 0:
            emb_name = doc.embfile_info(0).get("filename", "")
            if emb_name not in allowed_xml_names:
                reasons.append(
                    "E0051: Filename der eingebetteten Rechnung ist nicht korrekt. "
                    f"Gefunden: {emb_name}, erlaubt: {', '.join(allowed_xml_names)}"
                )
    except Exception as e:
        reasons.append(f"E0051: PDF konnte nicht geprüft werden. ({e})")
    # --- E0070: Rechnungsnummer/Charge auf Preisebene ---
    if xml is not None:
        if not re.search(r"<ram:ID>\s*\S+\s*</ram:ID>", xml):
            reasons.append("E0070: Fehlende Rechnungsnummer im Dokument.")
        if re.search(r"<ram:GrossPrice>.*?<ram:Charge>.*?</ram:Charge>.*?</ram:GrossPrice>", xml, re.DOTALL):
            reasons.append("E0070: Charge auf Preisebene (unter GrossPrice) gefunden.")
        # --- E0053: XML/Format-Prüfung ---
        try:
            etree.fromstring(xml.encode("utf-8"))
        except Exception:
            reasons.append("E0053: Invalides XML.")
        # XRechnung-Format
        if not re.search(r"<rsm:CrossIndustryInvoice", xml):
            reasons.append("E0053: Ungültiges XRechnungs-Format (Root-Tag fehlt).")
        # Prüfe auf UBL/PEPPOL nur am Root-Tag!
        if re.search(r'<Invoice[^>]+xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"', xml):
            reasons.append("E0053: PEPPOL UBL-Format erkannt (nicht zulässig für XRechnung/Factur-X-Workflow).")
        # --- E0054: Nach Extraktion kein XML ---
        try:
            etree.fromstring(xml.encode("utf-8"))
        except Exception:
            reasons.append("E0054: Extrahiertes Objekt ist keine als XML klassifizierbare Datei (z.B. fehlendes End-Tag).")
    return reasons

def extract_xml_from_pdf(pdf_path):
    doc = fitz.open(pdf_path)
    for i in range(doc.embfile_count()):
        name = doc.embfile_info(i).get("filename", "").lower()
        if name.endswith(".xml"):
            xml_bytes = doc.embfile_get(i)
            return xml_bytes.decode("utf-8", errors="replace")
    if doc.embfile_count() > 0:
        xml_bytes = doc.embfile_get(0)
        return xml_bytes.decode("utf-8", errors="replace")
    return None

def extract_raw_xml_from_pdf(pdf_path):
    """Suche im gesamten PDF nach Roh-XML-Streams (forensisch, kein offizieller Anhang)."""
    doc = fitz.open(pdf_path)
    for xref in range(1, doc.xref_length()):
        try:
            stream = doc.xref_stream(xref)
            # Suche nach typische XML-Anfänge (vorsicht auf UTF-8 BOM etc.)
            if stream.strip().startswith(b'<?xml') or b'<rsm:CrossIndustryInvoice' in stream[:200]:
                try:
                    text = stream.decode("utf-8", errors="replace")
                except Exception:
                    continue
                # Nur, wenn das halbwegs wie XML aussieht:
                if "<" in text and ">" in text and "</" in text:
                    return text, xref
        except Exception:
            continue
    return None, None

def check_custom_xmp(pdf_path):
    doc = fitz.open(pdf_path)
    xmp = doc.metadata.get("xmp")
    if not xmp:
        try:
            xmp = doc.xmp_metadata
        except Exception:
            xmp = None
    if not xmp:
        return False  # Kein XMP, Bedingung nicht erfüllt

    ns_list = [
        "urn:ferd:pdfa:CrossIndustryDocument:invoice:1p0#",
        "urn:zugferd:pdfa:CrossIndustryDocument:invoice:2p0#",
        "urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#",
        "urn:factur-x:pdfa:CrossIndustryDocument:1p0#",
    ]
    required_fields = [
        "DocumentType", "DocumentFileName", "Version", "ConformanceLevel"
    ]
    try:
        root = etree.fromstring(xmp.encode("utf-8"))
    except Exception:
        return False

    # Suche alle rdf:Description mit gewünschtem Namespace
    found = False
    for desc in root.findall(".//{*}Description"):
        about = desc.attrib.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about", "")
        if any(ns in about for ns in ns_list):
            # Prüfe auf mindestens eines der geforderten Felder
            for field in required_fields:
                if field in desc.attrib or desc.find(f".//{{*}}{field}") is not None:
                    found = True
                    break
        if found:
            break
    return found

def validate_xml(xml):
    parser = etree.XMLParser(recover=True)
    try:
        etree.fromstring(xml.encode("utf-8"), parser)
    except etree.XMLSyntaxError:
        pass
    suggestions = []
    if parser.error_log:
        for err in parser.error_log:
            msg = f"⚠️ Strukturfehler: Zeile {err.line}, Spalte {err.column}: {err.message}"
            suggestions.append(msg)
        return False, "❌ XML enthält Syntaxfehler:", [], None, suggestions
    else:
        for tag in MANDATORY_TAGS:
            for match in re.finditer(fr"<{tag}>(.*?)</{tag}>", xml):
                content = match.group(1).strip()
                if not content:
                    line = xml.count("\n", 0, match.start(1)) + 1
                    suggestions.append(f"⚠️ Pflichtfeld {tag} ist leer (Zeile {line})")
        return True, "✔️ XML ist wohlgeformt.", [], None, suggestions if suggestions else None

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
            if "is not expected" in str(e) or "Expected is" in str(e):
                results.append(f"⚠️ Strukturfehler in {os.path.basename(xsd_path)}: {str(e)}")
            else:
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

def detect_xml_standard(xml):
    if xml is None:
        return "Unbekannt"
    # UBL-2.1/2.2/2.3 (Invoice-2)
    if re.search(r'<Invoice[^>]+xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"', xml):
        return "UBL Invoice-2"
    # XRechnung/Factur-X/CII
    if re.search(r'<rsm:CrossIndustryInvoice', xml):
        return "CII (CrossIndustryInvoice – z.B. XRechnung, Factur-X, ZUGFeRD)"
    # PEPPOL Hinweis
    if "peppol" in xml.lower():
        return "PEPPOL UBL"
    return "Unbekannt"

@app.route("/download_corrected", methods=["POST"])
def download_corrected():
    import io
    import zipfile

    original_pdf_path = session.get("original_pdf_path")
    if not original_pdf_path or not os.path.exists(original_pdf_path):
        return "❌ Originale PDF nicht gefunden.", 400

    xml_raw = request.form.get("xml_data")
    # ACHTUNG: Das ist jetzt die "Korrektur"-Liste mit Zeichenpositionen!
    corrections = sum([c.split(",") for c in request.form.getlist("correction")], [])
    print("Korrekturen empfangen:", corrections)
    print("XML vorher:", xml_raw[:1000])

    # *** Nur das hier! ***
    corrected_xml = replace_at_positions(xml_raw, corrections)
    # === Zusätzliche Logs zur Ursachenforschung ===
    print("Nach replace_at_positions:")
    print("  [1744:1745]:", repr(corrected_xml[1744:1745]))
    print("  [4313:4314]:", repr(corrected_xml[4313:4314]))
    for m in re.finditer(r"<ram:CategoryCode>(.*?)</ram:CategoryCode>", corrected_xml):
        print(f"{m.start(1)}:{m.end(1)} = '{m.group(1)}'")
    
    print("---- Kontrolle der Zeichen an den Korrektur-Positionen ----")
    for c in corrections:
        parts = c.split("|")
        if len(parts) == 4:
            label, start, end, new_value = parts
            start, end = int(start), int(end)
            snippet = corrected_xml[start-10:end+10]  # Kontext drum herum
            print(f"Korrektur {label}: Position {start}:{end} → vor Korrektur: '{xml_raw[start:end]}' | nach Korrektur: '{corrected_xml[start:end]}' | Kontext: '{snippet}'")

    print("---- CategoryCode-Übersicht (Positionen & Werte im gesamten XML) ----")
    for m in re.finditer(r"<ram:CategoryCode>(.*?)</ram:CategoryCode>", corrected_xml):
        print(f"CategoryCode bei [{m.start(1)}:{m.end(1)}] = '{m.group(1)}'")
    print("-------------------------------------------------------------")
    print("XML nach Korrektur:", corrected_xml[:1000])

    # 2. Dann escapen
    corrected_xml = xml_escape_values(corrected_xml)

    corrected_pdf_path = tempfile.mktemp(suffix=".pdf")
    doc = fitz.open(original_pdf_path)
    print("PDF Embedded Files (vorher):", doc.embfile_count())
    for i in range(doc.embfile_count()):
        print(doc.embfile_info(i))
    repair_embed = request.form.get("repair_embed")
    if repair_embed == "yes":
        print("PDF Embedded Files (vorher):", doc.embfile_count())
        for i in range(doc.embfile_count()):
            print(doc.embfile_info(i))
        if doc.embfile_count() > 0:
            doc.embfile_del(0)
        print("-------- XML, das jetzt wirklich eingebettet wird --------")
        print(corrected_xml)
        print("-------- ENDE --------")
        print("Test: Enthält corrected_xml an Position 1744–1745:", repr(corrected_xml[1744:1745]))
        print("Alle CategoryCodes im XML:")
        for m in re.finditer(r"<ram:CategoryCode>(.*?)</ram:CategoryCode>", corrected_xml):
            print(f"{m.start(1)}:{m.end(1)} = '{m.group(1)}'")
        doc.embfile_add("factur-x.xml", corrected_xml.encode("utf-8"))
        print("PDF Embedded Files (nachher):", doc.embfile_count())
        for i in range(doc.embfile_count()):
            print(doc.embfile_info(i))

    doc.save(corrected_pdf_path)
    with fitz.open(corrected_pdf_path) as check_doc:
        xml_bytes = check_doc.embfile_get(0)
        xml_str = xml_bytes.decode("utf-8", errors="replace")
        print("-------- Aus PDF extrahiertes XML nach dem Speichern --------")
        print(xml_str)
        print("-------- ENDE EXTRAKT --------")

    print(">>> PDF Embedded Files (nachher):")
    with fitz.open(corrected_pdf_path) as check_doc:
        xml_bytes = check_doc.embfile_get(0)
        print("MD5 von embedded XML:", hashlib.md5(xml_bytes).hexdigest())
        embedded_count = check_doc.embfile_count()
        print("Embedded count:", embedded_count)
        for i in range(embedded_count):
            info = check_doc.embfile_info(i)
            print("Anhang-Info:", info)
            xml_bytes = check_doc.embfile_get(i)
            try:
                xml_str = xml_bytes.decode("utf-8", errors="replace")
            except Exception as e:
                xml_str = str(xml_bytes)
            print(f"Embedded XML (erste 200 Zeichen): {xml_str[:200]!r}")
            print("Komplettes eingebettetes XML:")
            print(xml_str)
            print("-" * 60)

    orig_filename = session.get("uploaded_filename")
    if not orig_filename:
        orig_filename = "Rechnung"
    basename, ext = os.path.splitext(orig_filename)
    download_name = f"{basename}_corrected.pdf"

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        with open(corrected_pdf_path, "rb") as f:
            pdf_bytes = f.read()
            print("MD5 vom endgültigen PDF:", hashlib.md5(pdf_bytes).hexdigest())
            zf.writestr(download_name, pdf_bytes)
    zip_buffer.seek(0)

    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{basename}_corrected.zip"
    )
    
@app.route("/", methods=["GET", "POST"])
def index():
    result = ""
    filename = ""
    excerpt = []
    highlight_line = None
    suggestions = []
    codelist_table = []
    syntax_table = []
    error_reasons = []

    uploaded = request.files.get("pdf_file")
    if not uploaded or uploaded.filename == "":
        result = "❌ Keine Datei ausgewählt oder hochgeladen."
        return render_template("index.html", result=result, filename=filename)

    filename = uploaded.filename
    session["uploaded_filename"] = filename
    file_ext = os.path.splitext(filename)[1].lower()
    is_pdf = file_ext == ".pdf"
    is_xml = file_ext == ".xml" or uploaded.content_type in ["application/xml", "text/xml"]

    tmp_suffix = file_ext if is_pdf or is_xml else ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=tmp_suffix) as tmp:
        file_path = tmp.name
        uploaded.save(file_path)

    session["original_pdf_path"] = file_path

    try:
        # XML aus PDF extrahieren oder direkt einlesen
        if is_pdf:
            xml = extract_xml_from_pdf(file_path)
            if not xml:
                # Forensisch nach Roh-XML suchen!
                raw_xml, xref_no = extract_raw_xml_from_pdf(file_path)
                if raw_xml:
                    # Info für User!
                    result = (
                        "❌ Keine korrekt eingebettete XML-Datei in der PDF gefunden.<br>"
                        "🕵️‍♂️ <b>Aber:</b> Im PDF wurde eine Roh-XML im Objekt gefunden (nicht offiziell eingebettet).<br>"
                    )
                    # Option für den User: Sollen wir das PDF automatisch „reparieren“ (richtig einbetten)?
                    # Correction Proposal als Dropdown!
                    repair_dropdown = (
                        '<form method="POST" action="/download_corrected">'
                        '<input type="hidden" name="xml_data" value="{}">'.format(raw_xml.replace('"', "&quot;")) +
                        '<input type="hidden" name="correction" value="EMBEDRAW|noembed|embed">'
                        '<label>PDF reparieren (XML korrekt als Anhang einbetten)? '
                        '<select name="repair_embed">'
                        '<option value="yes" selected>Ja, reparieren</option>'
                        '<option value="no">Nein, PDF bleibt wie sie ist</option>'
                        '</select></label> '
                        '<button type="submit">📥 Korrigierte PDF herunterladen</button>'
                        '</form>'
                    )
                    result += repair_dropdown
                    return render_template("index.html", result=result, filename=filename)
                else:
                    result = "❌ Keine XML-Datei in der PDF gefunden."
                    error_reasons = check_errorcodes(None, file_path)
                    if error_reasons:
                        result += "<br><br><b>Fehlererkennung:</b><ul>"
                        for reason in error_reasons:
                            result += f"<li>{reason}</li>"
                        result += "</ul>"
                    return render_template("index.html", result=result, filename=filename)


        xml_standard = detect_xml_standard(xml)

        valid, msg, excerpt, highlight_line, xml_suggestions = validate_xml(xml)
        result = msg
        if xml_suggestions:
            syntax_table = xml_suggestions

        if valid:
            xsd_ok, xsd_msg = validate_against_all_xsds(xml, DEFAULT_XSD_ROOT)
            result += "<br>" + xsd_msg
            if os.path.exists(DEFAULT_XSLT_PATH) and request.form.get("schematron"):
                sch_issues = validate_with_schematron(xml, DEFAULT_XSLT_PATH)
                suggestions.extend(f"❌ {msg}" for msg in sch_issues)
    
        xml_lines = xml.splitlines()
        element_context_mapping = {
            "Currency": [r"<ram:InvoiceCurrencyCode>(.*?)</ram:InvoiceCurrencyCode>"],
            "Country": [r"<ram:CountryID>(.*?)</ram:CountryID>"],
            "Payment": [r"<ram:SpecifiedTradeSettlementPaymentMeans>.*?<ram:TypeCode>(.*?)</ram:TypeCode>"],
            "VAT CAT": [r"<ram:ApplicableTradeTax>.*?<ram:TypeCode>(.*?)</ram:TypeCode>"],
            "5305": [r"<ram:CategoryCode>(.*?)</ram:CategoryCode>"],
            "1153": [r"<ram:ReferenceTypeCode>(.*?)</ram:ReferenceTypeCode>"],
            "Date": [r'DateTimeString[^>]*?format="(.*?)"'],
            "Line Status": [r"<ram:LineStatusCode>(.*?)</ram:LineStatusCode>"],
            "INCOTERMS": [r"<ram:INCOTERMSCode>(.*?)</ram:INCOTERMSCode>"],
            "TRANSPORT": [r"<ram:TransportModeCode>(.*?)</ram:TransportModeCode>"],
            "1001": [r"<rsm:ExchangedDocument>.*?<ram:TypeCode>(.*?)</ram:TypeCode>"],
            "Unit": [
                r'<ram:BilledQuantity[^>]*?unitCode="(.*?)"',
                r'<ram:InvoicedQuantity[^>]*?unitCode="(.*?)"'
            ]
        }
        for label, patterns in element_context_mapping.items():
            allowed_set = code_sets.get(label, set())
            value_counter = {}
            for pattern in patterns:
                regex = re.compile(pattern)
                for match in regex.finditer(xml):
                    value = match.group(1).strip() if match.lastindex and match.group(1) else ""
                    count = value_counter.get(value, 0) + 1
                    value_counter[value] = count

                    if value == "" or value not in allowed_set:
                        start = match.start(1) if match.lastindex else match.start()
                        end = match.end(1) if match.lastindex else match.end()
                
                        line_number = xml.count("\n", 0, start) + 1
                        offset = start - sum(len(l) + 1 for l in xml_lines[:line_number - 1])
                        column_number = offset + 1
                        if not allowed_set:
                            dropdown_html = "⚠️ Kein Wert angegeben oder keine Codeliste verfügbar"
                        else:
                            sorted_options = sorted(allowed_set)
                            old_value = value if value else "__LEER__"
    
                            # 1. Prefix-Match (z.B. "58ggg" => "58" bei Payment)
                            prefix_match = None
                            for option in allowed_set:
                                if value and value.startswith(option):
                                    prefix_match = option
                                    break

                            # 2. Korrekturvorschlags-Logik
                            if prefix_match:
                                closest_match = [prefix_match]
                            elif label == "5305" and value and value.upper() != value:
                                if value.upper() in allowed_set:
                                    closest_match = [value.upper()]
                                else:
                                    closest_match = get_close_matches(value, allowed_set, n=1, cutoff=0.6)
                            else:
                                closest_match = get_close_matches(value, allowed_set, n=1, cutoff=0.6)

                            dropdown_html = f'<label>→ Möglicherweise meinten Sie: '
                            dropdown_html += f'<select name="correction">'
                            for option in sorted_options:
                                selected = 'selected' if closest_match and option == closest_match[0] else ''
                                dropdown_html += (
                                    f'<option value="{label}|{start}|{end}|{option}" {selected}>{option}</option>'
                                )
                            dropdown_html += '</select></label>'

                        suggestion = Markup(dropdown_html)
                        codelist_table.append({
                            "label": label,
                            "value": value,
                            "suggestion": suggestion,  # wird im Template ge-„safed“
                            "line": line_number,
                            "column": column_number,
                            # entscheidend: start & end-Position des zu ersetzenden Inhalts!
                            "correction_value": f"{label}|{start}|{end}|{closest_match[0] if closest_match else ''}"
                        })
        codelist_table.sort(key=lambda x: (x["line"], x["column"]))

        # Fehlercode-Prüfung
        error_reasons = check_errorcodes(xml, file_path)

    finally:
        pass

    # Fehlerausgabe ans Result anhängen
    if error_reasons:
        result += "<br><br><b>SON Fehlererkennung:</b><ul>"
        for reason in error_reasons:
            result += f"<li>{reason}</li>"
        result += "</ul>"

    return render_template("index.html",
                           result=result,
                           filename=filename,
                           excerpt=excerpt,
                           highlight_line=highlight_line,
                           suggestion="<br>".join(suggestions),
                           syntax_table=syntax_table,
                           codelist_table=codelist_table,
                           codelisten_hinweis="ℹ️ Hinweis: Codelistenprüfung basiert auf EN16931 v14 (gültig ab 2024-11-15).",
                           original_xml=xml,
                           xml_standard=xml_standard
    )

if __name__ == "__main__":
    if hasattr(sys, '_MEIPASS'):
        app.run(debug=True, host="127.0.0.1", port=5000)
    else:
        port = int(os.environ.get("PORT", 10000))  # Default to 10000 if PORT not set
        app.run(host="0.0.0.0", port=port)
