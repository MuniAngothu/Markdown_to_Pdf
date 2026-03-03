"""
markdown_to_pdf.py
==================
Batch-converts every Markdown file in the Markdown/ folder to a
Notion-style PDF saved in the PDF/ folder.

Folder layout (git-friendly, no spaces):
  MD/
  ├── input/             ← input  (.md source files)
  ├── output/            ← output (.pdf rendered files)
  └── markdown_to_pdf.py

Pipeline per file:
  1. Read & normalise Markdown
  2. Convert Markdown → HTML  (python-markdown + Pygments)
  3. Post-process HTML        (callout boxes, code whitespace)
  4. Inject CSS               (Notion light theme)
  5. Render PDF               (xhtml2pdf / ReportLab)

Usage:
  python markdown_to_pdf.py

Dependencies:
  pip install markdown xhtml2pdf Pygments
"""

import re
from pathlib import Path
import markdown
from xhtml2pdf import pisa


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — FOLDER PATHS
# ═══════════════════════════════════════════════════════════════════════════

BASE_DIR     = Path(r"c:\Users\angot\Documents\GitHub\MD")
MARKDOWN_DIR = BASE_DIR / "input"    # input  — all .md files live here
PDF_DIR      = BASE_DIR / "output"   # output — all .pdf files go here

# Create output folder if it doesn't exist yet
PDF_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — MARKDOWN → HTML
# ═══════════════════════════════════════════════════════════════════════════

def read_markdown(path: Path) -> str:
    """Read file and strip optional outer ```markdown fence."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"^````markdown\s*\n", "", text)
    text = re.sub(r"\n````\s*$", "", text)
    return text


def convert_to_html(md_text: str) -> str:
    """
    Convert Markdown text to an HTML fragment.
    codehilite + fenced_code with noclasses=True makes Pygments write
    inline style="color:..." on every <span>, which xhtml2pdf can render.
    """
    engine = markdown.Markdown(
        extensions=["tables", "fenced_code", "codehilite"],
        extension_configs={
            "codehilite": {
                "noclasses"     : True,          # inline style=, not CSS classes
                "guess_lang"    : True,           # auto-detect if lang not tagged
                "pygments_style": "friendly",     # clean light-background palette
            }
        },
    )
    return engine.convert(md_text)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — HTML POST-PROCESSING
# ═══════════════════════════════════════════════════════════════════════════

def make_callout_boxes(html: str) -> str:
    """
    Convert bold-label paragraphs (optionally followed by a <ul>) into
    styled callout boxes.

    Pattern A — label with inline content:
        <p><strong>Label:</strong> some text</p>
        → <div class="callout"><span class="callout-label">Label:</span> some text</div>

    Pattern B — standalone label followed by a list:
        <p><strong>Label:</strong></p>
        <ul>...</ul>
        → <div class="callout"><span class="callout-label">Label:</span><ul>...</ul></div>
    """
    # Pattern B: label-only paragraph followed by <ul> or <ol>
    html = re.sub(
        r'<p><strong>([^<]+:)</strong>\s*</p>\s*(<(?:ul|ol)[^>]*>.*?</(?:ul|ol)>)',
        lambda m: (
            '<div class="callout">'
            f'<span class="callout-label">{m.group(1)}</span>'
            f'{m.group(2)}'
            '</div>'
        ),
        html,
        flags=re.DOTALL,
    )

    # Pattern A: label with trailing inline text (no following list)
    html = re.sub(
        r'<p><strong>([^<]+:)</strong>([^<].*?)</p>',
        lambda m: (
            '<div class="callout">'
            f'<span class="callout-label">{m.group(1)}</span> '
            f'{m.group(2).strip()}'
            '</div>'
        ),
        html,
        flags=re.DOTALL,
    )

    return html


def fix_pre_whitespace(html: str) -> str:
    """
    xhtml2pdf collapses whitespace inside <pre> blocks.
    Handles two formats:
      • codehilite output : <div class="codehilite"><pre>…</pre></div>
      • plain fenced code : <pre><code>…</code></pre>
    Strategy: split on newlines, replace leading spaces with &nbsp;,
    rejoin with <br/>.  HTML entities inside are left untouched.
    """
    def preserve(inner: str) -> str:
        inner = inner.rstrip("\n")   # drop trailing blank line added by markdown
        lines = inner.split("\n")
        result = []
        for line in lines:
            stripped = line.lstrip(" ")
            indent   = len(line) - len(stripped)
            result.append("&nbsp;" * indent + stripped)
        return "<br/>".join(result)

    # ── Format 1: codehilite wraps in <div class="codehilite"><pre>…</pre></div>
    def fix_codehilite(m: re.Match) -> str:
        return f'<div class="codehilite"><pre>{preserve(m.group(1))}</pre></div>'

    html = re.sub(
        r'<div class="codehilite"><pre>(.*?)</pre></div>',
        fix_codehilite,
        html,
        flags=re.DOTALL,
    )

    # ── Format 2: plain <pre><code>…</code></pre> (unfenced / fallback)
    def fix_plain(m: re.Match) -> str:
        return f'<pre><code>{preserve(m.group(1))}</code></pre>'

    html = re.sub(
        r'<pre><code[^>]*>(.*?)</code></pre>',
        fix_plain,
        html,
        flags=re.DOTALL,
    )

    return html


def strip_pygments_backgrounds(html: str) -> str:
    """
    Pygments (via noclasses=True) injects inline background-color on the
    <div class="codehilite"> and <pre> elements.  Strip those so our CSS
    controls the block appearance instead of being overridden.
    """
    # Remove style attr from the codehilite div entirely
    html = re.sub(
        r'(<div class="codehilite") style="[^"]*"',
        r'\1',
        html,
    )
    # Remove only the background portion from any inline style on <pre>
    html = re.sub(
        r'(<pre)[^>]*style="[^"]*"',
        r'\1',
        html,
    )
    return html


def add_code_box_headers(html: str) -> str:
    """
    Wrap every code block in a two-row table:
      Row 1 — colored header bar with language label (JSON / Python / Code / Prompt)
      Row 2 — the code content

    Using a <table> guarantees correct rendering in xhtml2pdf, which has
    incomplete support for CSS borders on arbitrary divs.
    Detection is based on content keywords inside the already-processed block.
    """
    # ── Language signatures → (label, header_bg_color) ──────────────────────
    LANG_RULES = [
        # JSON  — quotes around keys, bbox, chunk_id
        (r'chunk_id|bbox|&quot;metadata&quot;|&quot;content&quot;|&quot;source_pdf&quot;',
         "JSON",   "#92400e"),   # amber-brown
        # Python — def / class / import / .get(
        (r'def&nbsp;|class&nbsp;|import&nbsp;|\.get\(|return&nbsp;|BaseTool',
         "Python", "#1e40af"),   # deep blue
        # Prompt — natural-language instruction blocks
        (r'You are a|You may ONLY|MUST cite|do not contain',
         "Prompt", "#065f46"),   # dark green
    ]
    FALLBACK = ("Code", "#3730a3")   # indigo

    def detect(content: str) -> tuple:
        for pattern, label, color in LANG_RULES:
            if re.search(pattern, content):
                return label, color
        return FALLBACK

    def wrap(m: re.Match) -> str:
        inner        = m.group(1)   # already whitespace-processed content
        label, color = detect(inner)
        return (
            # Outer table — visible border + rounded feel via solid border
            '<table width="100%" style="'
            'border-collapse:collapse;'
            'border:1px solid #d1d5db;'
            'margin:10pt 0 16pt 0;">'

            # Header row — pill-label on coloured bar
            '<tr>'
            f'<td style="background:{color};padding:5pt 12pt;">'
            f'<span style="color:#ffffff;'
            f'font-family:Arial,Helvetica,sans-serif;'
            f'font-size:7pt;'
            f'font-weight:bold;">'
            f'{label}'
            '</span>'
            '</td>'
            '</tr>'

            # Code content row
            '<tr>'
            '<td style="padding:0;background:#fdfcfb;'
            'border-top:1px solid #d1d5db;">'
            f'<div class="codehilite">{inner}</div>'
            '</td>'
            '</tr>'
            '</table>'
        )

    # Match <div class="codehilite"> … </div>  (no nested divs inside after fix_pre)
    html = re.sub(
        r'<div class="codehilite">(.*?)</div>',
        wrap,
        html,
        flags=re.DOTALL,
    )

    # ── Also box plain <pre><code> blocks (unfenced / fallback) ─────────────
    def wrap_plain(m: re.Match) -> str:
        inner        = m.group(1)
        label, color = detect(inner)
        return (
            '<table width="100%" style="'
            'border-collapse:collapse;'
            'border:1px solid #d1d5db;'
            'margin:10pt 0 16pt 0;">'
            '<tr>'
            f'<td style="background:{color};padding:5pt 12pt;">'
            f'<span style="color:#ffffff;'
            f'font-family:Arial,Helvetica,sans-serif;'
            f'font-size:7pt;font-weight:bold;">{label}</span>'
            '</td></tr>'
            '<tr>'
            '<td style="padding:0;background:#fdfcfb;'
            'border-top:1px solid #d1d5db;">'
            f'<pre><code>{inner}</code></pre>'
            '</td></tr>'
            '</table>'
        )

    html = re.sub(
        r'<pre><code[^>]*>(.*?)</code></pre>',
        wrap_plain,
        html,
        flags=re.DOTALL,
    )

    return html


def postprocess(html: str) -> str:
    html = make_callout_boxes(html)
    html = strip_pygments_backgrounds(html)
    html = fix_pre_whitespace(html)
    html = add_code_box_headers(html)   # must run AFTER whitespace is fixed
    return html


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — CSS  (Notion light theme)
# ═══════════════════════════════════════════════════════════════════════════

CSS = """
/* ── Page layout + footer ────────────────────────────────── */
@page {
    size  : A4;
    margin: 2.0cm 2.6cm 2.8cm 2.6cm;
}

* { box-sizing: border-box; }

html, body {
    background : #ffffff;
    color      : #37352f;
    font-family: Arial, Helvetica, sans-serif;
    font-size  : 10.5pt;
    line-height: 1.8;
}

/* ── Headings ────────────────────────────────────────────── */
/* H1 — document title: very large, full-width rule underneath */
h1 {
    font-size     : 22pt;
    font-weight   : bold;
    color         : #111111;
    margin-top    : 0;
    margin-bottom : 10pt;
    padding-bottom: 8pt;
    border-bottom : 2.5px solid #e3e3de;
    line-height   : 1.2;
}

/* H2 — section: mid-weight, left accent bar, no underline */
h2 {
    font-size     : 13pt;
    font-weight   : bold;
    color         : #1a1a1a;
    margin-top    : 22pt;
    margin-bottom : 5pt;
    padding-left  : 10pt;
    border-left   : 4px solid #2f80ed;
    line-height   : 1.3;
}

/* H3 — sub-section: slightly muted, no border */
h3 {
    font-size    : 10.5pt;
    font-weight  : bold;
    color        : #444444;
    margin-top   : 14pt;
    margin-bottom: 3pt;
    text-transform: uppercase;
}

/* H4 — label-level */
h4 {
    font-size    : 10pt;
    font-weight  : bold;
    color        : #777777;
    margin-top   : 10pt;
    margin-bottom: 2pt;
}

/* ── Body text ───────────────────────────────────────────── */
p {
    margin: 0 0 7pt 0;
    color : #37352f;
}

strong, b { color: #111111; font-weight: bold; }
em,     i { color: #595959; font-style: italic; }

a {
    color          : #2f80ed;
    text-decoration: underline;
}

/* ── Lists ───────────────────────────────────────────────── */
ul, ol {
    margin : 4pt 0 9pt 22pt;
    padding: 0;
}

li {
    margin-bottom: 5pt;
    color        : #37352f;
    line-height  : 1.7;
}

/* ── Callout / Focus box ─────────────────────────────────── */
/* Notion-style: icon prefix, soft blue tint, left accent     */
.callout {
    background : #f0f7ff;
    border-left: 4px solid #2f80ed;
    padding    : 10pt 14pt 10pt 14pt;
    margin     : 12pt 0 14pt 0;
    color      : #1a1a1a;
    font-size  : 10.5pt;
    line-height: 1.7;
}

.callout-label {
    font-weight   : bold;
    color         : #1a6bbf;
    font-size     : 8.5pt;
    text-transform: uppercase;
    display       : block;
    margin-bottom : 5pt;
    padding-bottom: 4pt;
    border-bottom : 1px solid #c8dff8;
}

.callout ul,
.callout ol {
    margin-top : 5pt;
    margin-left: 16pt;
}

.callout li {
    margin-bottom: 4pt;
    color        : #1a1a1a;
}

/* ── Inline code ─────────────────────────────────────────── */
code {
    font-family  : Courier, monospace;
    font-size    : 8.5pt;
    background   : #f2f1ee;
    color        : #c0392b;
    padding      : 1pt 4pt;
}

/* ── Code blocks ────────────────────────────────────────────
   The <table> wrapper from add_code_box_headers() supplies the
   outer border + colored header row.  .codehilite and pre only
   control the inner code area background and typography.      */
.codehilite {
    background : #fdfcfb;
    padding    : 11pt 14pt;
    margin     : 0;
    font-family: Courier, monospace;
    font-size  : 8.5pt;
    line-height: 1.7;
    color      : #2d2d2d;
}

.codehilite pre {
    background : none;
    border     : none;
    padding    : 0;
    margin     : 0;
    font-family: Courier, monospace;
    font-size  : 8.5pt;
    color      : #2d2d2d;
}

/* Fallback plain <pre> (also wrapped by add_code_box_headers) */
pre {
    background : #fdfcfb;
    padding    : 11pt 14pt;
    margin     : 0;
    font-family: Courier, monospace;
    font-size  : 8.5pt;
    color      : #2d2d2d;
    line-height: 1.7;
}

pre code {
    background: none;
    color     : #2d2d2d;
    font-size : 8.5pt;
    padding   : 0;
}

/* ── Tables ──────────────────────────────────────────────── */
table {
    width          : 100%;
    border-collapse: collapse;
    margin         : 10pt 0 16pt 0;
    font-size      : 9pt;
}

/* Header row: dark navy-gray — stands out strongly */
thead tr {
    background: #2d3748;
}

th {
    font-weight: bold;
    color      : #ffffff;
    text-align : left;
    padding    : 7pt 10pt;
    border     : 1px solid #2d3748;
    background : #2d3748;
}

/* Body rows: very subtle warm-gray stripe */
td {
    color         : #37352f;
    padding       : 6pt 10pt;
    border        : 1px solid #e4e4e0;
    vertical-align: top;
    background    : #ffffff;
    line-height   : 1.6;
}

/* ── Divider ─────────────────────────────────────────────── */
hr {
    border    : none;
    border-top: 2px solid #e3e3de;
    margin    : 22pt 0;
}

/* ── Blockquote ──────────────────────────────────────────── */
blockquote {
    border-left: 4px solid #cbd5e0;
    background : #f7f6f3;
    margin     : 10pt 0;
    padding    : 8pt 14pt;
    color      : #595959;
    font-style : italic;
    line-height: 1.7;
}
"""


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5 — HTML TEMPLATE
# ═══════════════════════════════════════════════════════════════════════════

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Problem Statement</title>
  <style>{css}</style>
</head>
<body>
{body}
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6 — PDF RENDERING
# ═══════════════════════════════════════════════════════════════════════════

def render_pdf(html_string: str, output_path: Path) -> bool:
    """Write HTML to PDF via xhtml2pdf. Returns True on success."""
    with open(str(output_path), "wb") as fh:
        result = pisa.CreatePDF(html_string, dest=fh, encoding="utf-8")
    return not result.err


def convert_file(md_path: Path) -> None:
    """Full pipeline for a single Markdown file → PDF."""
    stem     = md_path.stem                   # e.g. "business_analysis"
    pdf_path = PDF_DIR / f"{stem}.pdf"

    # Step 1: Read Markdown
    md_text   = read_markdown(md_path)

    # Step 2: Markdown → raw HTML fragment
    raw_html  = convert_to_html(md_text)

    # Step 3: Post-process (callouts, code whitespace, syntax boxes)
    body_html = postprocess(raw_html)

    # Step 4: Assemble full HTML document
    full_html = HTML_TEMPLATE.format(css=CSS, body=body_html)

    # Step 5: Render PDF
    success = render_pdf(full_html, pdf_path)
    status  = "OK " if success else "ERR"
    print(f"  [{status}]  {md_path.name}  →  output/{pdf_path.name}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 7 — MAIN  (batch conversion)
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    md_files = sorted(MARKDOWN_DIR.glob("*.md"))

    if not md_files:
        print(f"[!!] No .md files found in {MARKDOWN_DIR}")
        return

    print(f"\nConverting {len(md_files)} file(s)")
    print(f"  Source : {MARKDOWN_DIR}")
    print(f"  Output : {PDF_DIR}")
    print("-" * 52)

    for md_path in md_files:
        convert_file(md_path)

    print("-" * 52)
    print(f"Done. PDFs saved to → {PDF_DIR}\n")


if __name__ == "__main__":
    main()
