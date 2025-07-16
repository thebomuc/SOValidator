import xml.etree.ElementTree as ET

def replace_category_codes(xml_str, replacements):
    ns = {'ram': 'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100'}
    tree = ET.ElementTree(ET.fromstring(xml_str))
    all_codes = tree.findall('.//ram:CategoryCode', ns)
    for repl in replacements:
        idx = repl['index']
        if 0 <= idx < len(all_codes):
            all_codes[idx].text = repl['new_value']
    return ET.tostring(tree.getroot(), encoding='unicode')
