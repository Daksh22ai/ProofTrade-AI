/**
 * extract_docx.js — One-off utility to extract plain text from the trading playbook .docx
 *
 * Node.js alternative to extract_docx.py, using PowerShell's built-in zip extraction.
 * Not part of the running system.
 *
 * Usage:
 *   node archive/extract_docx.js
 */
const fs   = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const docxPath = path.join(__dirname, 'Complete_Trading_System_v3.docx');
const tmpDir   = path.join(__dirname, '..', 'docx_extract_tmp');

if (fs.existsSync(tmpDir)) fs.rmSync(tmpDir, { recursive: true, force: true });

execSync(`powershell -Command "Expand-Archive -Path '${docxPath}' -DestinationPath '${tmpDir}' -Force"`);

let xml = fs.readFileSync(path.join(tmpDir, 'word', 'document.xml'), 'utf-8');
xml = xml.replace(/<\/w:p>/g, '\n').replace(/<\/w:tr>/g, '\n');
let text = xml.replace(/<[^>]+>/g, '');
text = text.replace(/\n\s*\n+/g, '\n\n');

const outPath = path.join(__dirname, '..', 'playbook_extracted.txt');
fs.writeFileSync(outPath, text, 'utf-8');
console.log(`Extracted ${text.length} characters → playbook_extracted.txt`);

fs.rmSync(tmpDir, { recursive: true, force: true });
