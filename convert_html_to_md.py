"""
Convert SEC 10-K HTML filings to well-structured Markdown files.

SEC XBRL/HTML filings use inline styles (font-weight: bold, font-size) rather than 
semantic HTML headings (h1, h2, h3). This script:
1. Parses the HTML and extracts text content
2. Detects SEC 10-K structure (PART I-IV, ITEM 1-16)
3. Applies proper Markdown heading hierarchy (#, ##, ###)
4. Preserves tables and financial data
5. Outputs clean .md files ready for PageIndex consumption
"""

import os
import re
import sys
from pathlib import Path
from bs4 import BeautifulSoup
import html2text


def extract_sec_10k_to_markdown(html_path: str, output_path: str) -> str:
    """Convert SEC 10-K HTML filing to structured Markdown."""
    
    print(f"Processing: {os.path.basename(html_path)}")
    
    # Read HTML
    with open(html_path, 'r', encoding='utf-8', errors='ignore') as f:
        html_content = f.read()
    
    # Parse with BeautifulSoup to clean up
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Remove script and style tags
    for tag in soup(['script', 'style', 'meta', 'link']):
        tag.decompose()
    
    # Convert to text using html2text (preserves tables and structure)
    h = html2text.HTML2Text()
    h.body_width = 0  # No word wrapping
    h.ignore_links = True
    h.ignore_images = True
    h.ignore_emphasis = False
    h.protect_links = False
    h.unicode_snob = True
    h.skip_internal_links = True
    
    text = h.handle(str(soup))
    
    # Clean up excessive whitespace
    text = re.sub(r'\n{4,}', '\n\n\n', text)
    
    # Now apply SEC 10-K heading structure
    lines = text.split('\n')
    structured_lines = []
    
    # Extract filing title from filename
    basename = os.path.basename(html_path).replace('.html', '')
    parts = basename.split('-')
    if len(parts) >= 2:
        ticker = parts[0].upper()
        date = parts[1]
        year = date[:4]
        title = f"# {ticker} Annual Report (10-K) — Fiscal Year {year}"
    else:
        title = f"# SEC 10-K Filing"
    
    structured_lines.append(title)
    structured_lines.append("")
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        
        # Skip empty lines (will be handled by spacing)
        if not stripped:
            structured_lines.append("")
            continue
        
        # Detect PART headings (PART I, PART II, etc.)
        part_match = re.match(r'^(?:#{0,4}\s*)?(?:\*{0,2})?\s*PART\s+([IVX]+)\s*(?:\*{0,2})?$', stripped, re.IGNORECASE)
        if part_match:
            structured_lines.append(f"\n## PART {part_match.group(1).upper()}\n")
            continue
        
        # Detect ITEM headings (Item 1, Item 1A, Item 7A, etc.)
        item_match = re.match(
            r'^(?:#{0,4}\s*)?(?:\*{0,2})?\s*(?:ITEM|Item)\s+(\d+[A-Ca-c]?)[\.\s\u2014\-—]+\s*(.+?)(?:\*{0,2})?$', 
            stripped, re.IGNORECASE
        )
        if item_match:
            item_num = item_match.group(1).upper()
            item_title = item_match.group(2).strip().rstrip('*').strip()
            structured_lines.append(f"\n### Item {item_num}. {item_title}\n")
            continue
        
        # Detect standalone ITEM references without title on same line
        item_standalone = re.match(
            r'^(?:#{0,4}\s*)?(?:\*{0,2})?\s*(?:ITEM|Item)\s+(\d+[A-Ca-c]?)\s*(?:\*{0,2})?\.?\s*$', 
            stripped, re.IGNORECASE
        )
        if item_standalone:
            # Look ahead for the title on next non-empty line
            item_num = item_standalone.group(1).upper()
            for j in range(i+1, min(i+4, len(lines))):
                next_stripped = lines[j].strip()
                if next_stripped and not re.match(r'^\s*$', next_stripped):
                    item_title = next_stripped.strip('*').strip()
                    structured_lines.append(f"\n### Item {item_num}. {item_title}\n")
                    break
            continue
        
        # Detect common SEC section headings 
        section_patterns = [
            r'^(?:\*{2})(.+?)(?:\*{2})$',  # **bold text** (html2text converts <b> to **)
        ]
        
        # Keep the line as-is for non-heading content
        # Remove any existing markdown heading markers that html2text may have added incorrectly
        if stripped.startswith('#'):
            # Check if it's a legitimate heading or just formatting noise
            heading_text = stripped.lstrip('#').strip()
            if len(heading_text) > 3 and len(heading_text) < 200:
                structured_lines.append(stripped)
            else:
                structured_lines.append(heading_text)
        else:
            structured_lines.append(line)
    
    # Join and clean up
    markdown = '\n'.join(structured_lines)
    
    # Final cleanup: remove excessive newlines
    markdown = re.sub(r'\n{4,}', '\n\n\n', markdown)
    
    # Remove any remaining HTML tags
    markdown = re.sub(r'<[^>]+>', '', markdown)
    
    # Write output
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(markdown)
    
    # Stats
    line_count = len(markdown.split('\n'))
    heading_count = len(re.findall(r'^#{1,4}\s', markdown, re.MULTILINE))
    word_count = len(markdown.split())
    
    print(f"  ✅ Output: {output_path}")
    print(f"  📊 {line_count} lines, {word_count} words, {heading_count} headings")
    
    return output_path


def main():
    docs_dir = Path("./documents")
    output_dir = Path("./documents/markdown")
    
    html_files = sorted(docs_dir.glob("*.html"))
    
    if not html_files:
        print("❌ No HTML files found in ./documents/")
        sys.exit(1)
    
    print(f"Found {len(html_files)} HTML filings to convert:\n")
    
    converted = []
    for html_file in html_files:
        md_filename = html_file.stem + ".md"
        md_path = output_dir / md_filename
        result = extract_sec_10k_to_markdown(str(html_file), str(md_path))
        converted.append(result)
        print()
    
    print(f"✅ All {len(converted)} filings converted to Markdown!")
    print(f"📁 Output directory: {output_dir}")


if __name__ == "__main__":
    main()
