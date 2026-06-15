"""
extract_docx.py — One-off utility to extract plain text from the trading playbook .docx

Used during development to extract the playbook content for prompt engineering.
Not part of the running system.

Usage:
    python archive/extract_docx.py
    # Outputs: playbook_extracted.txt in the project root
"""
import zipfile
import re
import os

docx_path = os.path.join(os.path.dirname(__file__), 'Complete_Trading_System_v3.docx')

with zipfile.ZipFile(docx_path) as z:
    xml = z.read('word/document.xml').decode('utf-8')

xml = xml.replace('</w:p>', '\n')
xml = xml.replace('</w:tr>', '\n')
text = re.sub(r'<[^>]+>', '', xml)
text = re.sub(r'\n\s*\n+', '\n\n', text)

out_path = os.path.join(os.path.dirname(__file__), '..', 'playbook_extracted.txt')
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(text)

print(f"Extracted {len(text)} characters → playbook_extracted.txt")
