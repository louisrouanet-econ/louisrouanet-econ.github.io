#!/usr/bin/env python3
"""Build the machine-readable layer of the website.

Reads the publication list in index.html, extracts the text of every paper
that has a local PDF, and writes:

  research/<slug>/index.html   a page per paper: Google Scholar citation_*
                               meta tags, abstract, full text as HTML
                               sections, BibTeX, links to the PDF
  research/<slug>.md           the same paper as markdown with front matter
  cv.md                        plain-text version of the CV
  llms.txt                     index for LLM crawlers (https://llmstxt.org)
  llms-full.txt                every paper concatenated into one file
  sitemap.xml                  sitemap covering the page, PDFs and paper pages
  robots.txt                   allows all crawlers and points at the sitemap

Nothing visible is added to index.html: the script only refreshes the
schema.org JSON-LD block between the <!-- jsonld:start --> / <!-- jsonld:end -->
markers and the <link rel="alternate" href="llms.txt"> tag in <head>.
Crawlers find the paper pages through the sitemap, llms.txt and the JSON-LD.

Run from the repository root:  python3 scripts/build_llm_text.py
Requires poppler-utils (pdftotext). For scanned PDFs it also needs
pdftoppm + tesseract; without them the scan is skipped with a warning.

The site URL is taken from a CNAME file when one exists (GitHub Pages
creates it when a custom domain is connected), otherwise from DEFAULT_BASE.
Hand corrections live in scripts/paper_overrides.json.
"""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE = "https://louisrouanet-econ.github.io"
CV_PATH = "wp-content/uploads/2025/04/Louis_Rouanet_s_Resume.pdf"
OWNER = "Louis Rouanet"
RESEARCH_DIR = ROOT / "research"
SCHOLAR_URL = "https://scholar.google.fr/citations?user=oHFKP-2njvsC"

GROUP_TYPE = {
    "Journal articles": "journal article",
    "Working Papers": "working paper",
    "Book chapters": "book chapter",
}

HEADING_WORDS = (
    "abstract", "introduction", "conclusion", "conclusions", "references",
    "appendix", "bibliography", "acknowledgements", "acknowledgments",
    "data appendix", "online appendix", "supplementary material",
)
STOPWORDS = {"the", "of", "and", "in", "a", "an", "to", "for", "on", "is", "at", "by", "or", "its"}

SITE_SUMMARY = ("Personal academic website of Louis Rouanet, assistant professor of political economy at "
                "the University of Florida's Hamilton Center: publications, working papers, book chapters, "
                "CV and teaching in political economy and economic history.")
BIO = ("Louis Rouanet is an Assistant Professor of Political Economy at the Hamilton School, "
       "University of Florida (previously University of Texas at El Paso and Western Kentucky "
       "University; PhD George Mason University, 2021) and an Associate Editor at *Public Choice*. "
       "His research is in political economy and economic history, with a focus on the French "
       "Revolution, revolutionary and Napoleonic France, inflationary finance, military "
       "conscription, and the political economy of institutions.")


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def base_url() -> str:
    cname = ROOT / "CNAME"
    if cname.is_file():
        host = cname.read_text().strip()
        if host:
            return "https://" + host
    return DEFAULT_BASE


def slugify(title: str) -> str:
    s = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[’'`]", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    out: list[str] = []
    for w in s.split("-"):
        if len("-".join(out + [w])) > 60:
            break
        out.append(w)
    while len(out) > 2 and out[-1] in STOPWORDS:
        out.pop()
    return "-".join(out) or "paper"


def load_overrides() -> dict:
    """Optional hand-written corrections keyed by the local PDF path:
    {"wp-content/...pdf": {"abstract": "...", "authors": [...], "description": "...",
                           "doi": "...", "published_url": "...", "year": "..."}}"""
    path = ROOT / "scripts" / "paper_overrides.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def text_of(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def clean_venue(venue: str) -> str:
    venue = re.sub(r"\s+", " ", venue).strip()
    venue = re.sub(r"^In:\s*", "", venue).rstrip(".")
    if venue and venue == venue.lower():  # the WordPress export lower-cased some venues
        venue = " ".join(w.capitalize() if w not in {"of", "and", "the", "in", "at"} else w
                         for w in venue.split(" "))
        venue = venue[0].upper() + venue[1:]
    return venue


def split_authors(coauthors: str) -> list[str]:
    s = re.sub(r"^with\s+", "", coauthors.strip(), flags=re.I)
    return [p.strip() for p in re.split(r",\s*|\s+and\s+|\s*&\s*", s) if p.strip()]


def esc(s: str) -> str:
    return html.escape(s, quote=True)


# ----------------------------------------------------------------------------
# index.html parsing
# ----------------------------------------------------------------------------
def parse_publications(page: str) -> list[dict]:
    pubs: list[dict] = []
    for sec in re.finditer(
        r'<section class="publication-group" aria-label="([^"]+)">(.*?)</section>', page, re.S
    ):
        group = html.unescape(sec.group(1))
        for art in re.finditer(r'<article class="publication">(.*?)</article>', sec.group(2), re.S):
            body = art.group(1)
            m = re.search(r'<h3><a href="([^"]+)">(.*?)</a></h3>', body, re.S)
            if not m:
                continue
            href, title = html.unescape(m.group(1)), text_of(m.group(2))
            venue_m = re.search(r'<p class="venue">(.*?)</p>', body, re.S)
            venue = clean_venue(text_of(venue_m.group(1))) if venue_m else ""
            year_m = re.search(r'<li class="date">.*?<span>(\d{4})</span>', body, re.S)
            co_m = re.search(r'<li class="coauthor">.*?<span>(.*?)</span>\s*</li>', body, re.S)
            coauthors = text_of(co_m.group(1)) if co_m else ""
            local = not re.match(r"https?://", href)
            pubs.append({
                "title": title,
                "slug": slugify(title),
                "local_pdf": href if local else "",
                "external_url": "" if local else href,
                "venue": venue,
                "year": year_m.group(1) if year_m else "",
                "coauthors": coauthors,
                "authors": [OWNER] + split_authors(coauthors),
                "group": group,
                "type": GROUP_TYPE.get(group, "paper"),
            })
    return pubs


# ----------------------------------------------------------------------------
# PDF text extraction
# ----------------------------------------------------------------------------
def pdf_text(pdf: Path) -> str:
    out = subprocess.run(["pdftotext", "-enc", "UTF-8", str(pdf), "-"],
                         capture_output=True, text=True, check=True).stdout
    if len(out.split()) >= 50:
        return out
    return ocr_text(pdf)


def ocr_text(pdf: Path) -> str:
    if not (shutil.which("pdftoppm") and shutil.which("tesseract")):
        print(f"  ! {pdf.name} has no text layer and OCR tools are missing; skipped", file=sys.stderr)
        return ""
    print(f"  (OCR) {pdf.name}", file=sys.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", "300", "-gray", "-png", str(pdf), f"{tmp}/p"], check=True)
        chunks = []
        for p in sorted(Path(tmp).glob("p-*.png")):
            r = subprocess.run(["tesseract", str(p), "-", "-l", "eng", "--psm", "1"],
                               capture_output=True, text=True, check=True)
            chunks.append(r.stdout)
    return "\f".join(chunks)


def is_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 80 or s.endswith((".", ",", ";", ":")):
        return False
    if s.lower().rstrip(".") in HEADING_WORDS:
        return True
    if re.match(r"^(\d+(\.\d+)*\.?|[A-Z]\.\d*|[IVX]+\.)\s+[A-Z][^.]*$", s):
        return True
    return bool(re.match(r"^(Appendix|Online Appendix)\b", s))


def clean_text(raw: str) -> str:
    text = unicodedata.normalize("NFKC", raw)  # ligatures such as ﬁ -> fi
    text = text.replace("\f", "\n\n").replace("­", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"(\w)-\n([a-z])", r"\1\2", text)  # re-join hyphenated words
    out: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        para: list[str] = []
        for ln in lines:
            if is_heading(ln):
                if para:
                    out.append(" ".join(para))
                    para = []
                out.append("## " + ln.rstrip("."))
            else:
                para.append(ln)
        if para:
            out.append(" ".join(para))
    text = "\n\n".join(out)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


ABSTRACT_CUES = re.compile(
    r"\b(this (?:paper|article|chapter|essay)|we (?:show|argue|find|provide|examine|analyze|study|use|document)"
    r"|I (?:argue|show|find|examine))\b", re.I)


def extract_abstract(text: str) -> str:
    m = re.search(r"(?m)^## Abstract\n\n(.+)$", text)
    if not m:
        m = re.search(r"(?m)^Abstract[:.]?\s+([A-Z].+)$", text)
    if m:
        abstract = re.split(r"\b(?:Keywords?|JEL [Cc]lassification|JEL [Cc]odes?)\b", m.group(1))[0]
        abstract = re.sub(r"\s+", " ", abstract).strip()
        if len(abstract) > 80:
            return abstract
    for para in text.split("\n\n")[:10]:
        if para.startswith("## "):
            continue
        if 300 <= len(para) <= 2500 and ABSTRACT_CUES.search(para) and "@" not in para:
            return re.sub(r"\s+", " ", para).strip()
    return ""


def short_description(abstract: str, limit: int = 240) -> str:
    if not abstract:
        return ""
    desc = ""
    for s in re.split(r"(?<=[.!?])\s+", abstract):
        if desc and len(desc) + len(s) + 1 > limit:
            break
        desc = (desc + " " + s).strip()
        if len(desc) >= limit * 0.6:
            break
    if len(desc) > limit:
        desc = desc[: limit - 1].rsplit(" ", 1)[0] + "…"
    return desc


# ----------------------------------------------------------------------------
# citation helpers
# ----------------------------------------------------------------------------
def bibtex(pub: dict) -> str:
    first_word = next((w for w in re.findall(r"[a-z]+", pub["title"].lower()) if w not in STOPWORDS), "paper")
    key = f"rouanet{pub['year'] or 'nd'}{first_word}"
    authors = " and ".join(pub["authors"])
    fields = [f"  title = {{{pub['title']}}}", f"  author = {{{authors}}}"]
    if pub["type"] == "journal article":
        kind = "article"
        if pub["venue"]:
            fields.append(f"  journal = {{{pub['venue']}}}")
    elif pub["type"] == "book chapter":
        kind = "incollection"
        if pub["venue"]:
            fields.append(f"  booktitle = {{{pub['venue']}}}")
    else:
        kind = "unpublished"
        fields.append(f"  note = {{Working paper{'; ' + pub['venue'] if pub['venue'] else ''}}}")
    if pub["year"]:
        fields.append(f"  year = {{{pub['year']}}}")
    if pub.get("doi"):
        fields.append(f"  doi = {{{pub['doi']}}}")
    fields.append(f"  url = {{{pub['page_url']}}}")
    return f"@{kind}{{{key},\n" + ",\n".join(fields) + "\n}"


def citation_meta(pub: dict, base: str) -> str:
    tags = [("citation_title", pub["title"])]
    tags += [("citation_author", a) for a in pub["authors"]]
    if pub["year"]:
        tags.append(("citation_publication_date", pub["year"]))
        tags.append(("citation_date", pub["year"]))
    if pub["type"] == "journal article" and pub["venue"]:
        tags.append(("citation_journal_title", pub["venue"]))
    elif pub["type"] == "book chapter" and pub["venue"]:
        tags.append(("citation_inbook_title", pub["venue"]))
    if pub.get("doi"):
        tags.append(("citation_doi", pub["doi"]))
    tags.append(("citation_pdf_url", f"{base}/{pub['local_pdf']}"))
    tags.append(("citation_abstract_html_url", pub["page_url"]))
    tags.append(("citation_language", "en"))
    return "\n".join(f'<meta name="{k}" content="{esc(v)}">' for k, v in tags)


# ----------------------------------------------------------------------------
# writers: markdown
# ----------------------------------------------------------------------------
def front_matter(pub: dict, base: str) -> str:
    lines = ["---",
             f"title: {json.dumps(pub['title'], ensure_ascii=False)}",
             f"authors: {json.dumps(', '.join(pub['authors']), ensure_ascii=False)}",
             f"type: {pub['type']}"]
    if pub["venue"]:
        lines.append(f"venue: {json.dumps(pub['venue'], ensure_ascii=False)}")
    if pub["year"]:
        lines.append(f"year: {pub['year']}")
    if pub.get("doi"):
        lines.append(f"doi: {pub['doi']}")
    lines.append(f"pdf: {base}/{pub['local_pdf']}")
    lines.append(f"page: {pub['page_url']}")
    lines.append(f"markdown: {pub['md_url']}")
    lines.append(f"homepage: {base}/")
    if pub.get("abstract"):
        lines.append(f"abstract: {json.dumps(pub['abstract'], ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines)


def paper_markdown(pub: dict, base: str, body: str) -> str:
    out = [front_matter(pub, base), "", f"# {pub['title']}", ""]
    out.append(f"**Authors:** {', '.join(pub['authors'])}  ")
    if pub["venue"]:
        label = "Status" if pub["type"] == "working paper" else "Published in"
        out.append(f"**{label}:** {pub['venue']}  ")
    if pub["year"]:
        out.append(f"**Year:** {pub['year']}  ")
    if pub.get("doi"):
        out.append(f"**DOI:** https://doi.org/{pub['doi']}  ")
    out.append(f"**PDF (authoritative version):** {base}/{pub['local_pdf']}  ")
    out.append(f"**HTML version:** {pub['page_url']}  ")
    out.append(f"**More papers by {OWNER}:** {base}/llms.txt")
    out.append("")
    out.append("> Plain-text version extracted automatically from the PDF so that the paper is easy to "
               "read, search and cite for people and software (including AI assistants). Tables, "
               "equations and figures may be imperfect; the PDF is the authoritative version. Please "
               "cite the published version where one exists.")
    out.append("")
    if pub.get("abstract"):
        out += ["## Abstract", "", pub["abstract"], ""]
    out += ["## Full text", "", body, "", "## BibTeX", "", "```bibtex", bibtex(pub), "```", ""]
    return "\n".join(out)


PHONE_RE = re.compile(r"\+?\(?\d{1,3}\)?[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
STREET_RE = re.compile(r"^\s*(## )?\d+ .*\b(Drive|Dr|Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Court|Ct|Way)\b", re.I)


def redact_contact(body: str) -> str:
    """Drop street address and phone lines; the e-mail stays, the PDF keeps everything."""
    kept = []
    for line in body.split("\n"):
        if STREET_RE.match(line):
            continue
        kept.append(PHONE_RE.sub("", line).strip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def cv_markdown(base: str, body: str) -> str:
    return "\n".join([
        "---",
        f"title: \"Curriculum Vitae — {OWNER}\"",
        f"pdf: {base}/{CV_PATH}",
        f"homepage: {base}/",
        f"generated: {date.today().isoformat()}",
        "---",
        "",
        f"# {OWNER} — Curriculum Vitae",
        "",
        f"Plain-text version of the CV at {base}/{CV_PATH} (the PDF is authoritative; "
        "postal address and phone number are omitted here).",
        "",
        redact_contact(body),
        "",
    ])


# ----------------------------------------------------------------------------
# writers: paper HTML page
# ----------------------------------------------------------------------------
PAGE_CSS = """
:root{--teal:#1b4d61;--coral:#fb7967;--blue:#2176ff;--ink:#161414;--paper:#f9f9f9}
*{box-sizing:border-box}body{margin:0;background:#fff;color:var(--ink);font:500 16px/1.7 Montserrat,Arial,Helvetica,sans-serif}
a{color:var(--coral);text-decoration:none}a:hover{color:var(--blue)}
header{background:var(--teal);color:#fff}header nav{max-width:860px;margin:auto;padding:16px 20px;display:flex;gap:24px;align-items:center;justify-content:space-between}
header a{color:#fff}.brand{font-weight:700;font-size:18px}
main{max-width:860px;margin:auto;padding:40px 20px 60px}
h1{font-size:34px;line-height:1.15;font-weight:700;margin:0 0 14px}h2{font-size:22px;line-height:1.2;font-weight:700;margin:40px 0 12px}
.meta{margin:0 0 6px}.venue{font-size:13px;font-weight:700;text-transform:uppercase;font-style:italic;color:var(--blue);margin:0 0 20px}
.links{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 30px}
.button{display:inline-block;border-radius:999px;padding:9px 18px;background:var(--coral);color:#fff;font:700 13px/1 Montserrat,Arial,sans-serif;text-transform:uppercase}.button:hover{background:var(--blue);color:#fff}
.abstract{background:var(--paper);padding:20px 24px;border-left:4px solid var(--teal)}
.note{font-size:14px;color:#555;border-top:1px solid #e5e5e5;padding-top:12px;margin-top:40px}
details{margin-top:36px}summary{cursor:pointer;font-weight:700;font-size:22px;list-style:none}summary::-webkit-details-marker{display:none}summary::after{content:" ▸";color:var(--coral)}details[open] summary::after{content:" ▾"}
.fulltext p{margin:0 0 1em;overflow-wrap:anywhere}.fulltext h2{font-size:19px}
pre{background:var(--paper);padding:16px;overflow:auto;font-size:13px;line-height:1.5}
footer{text-align:center;padding:25px 20px;font-size:14px}
@media print{header,.links,.note{display:none}}
"""


def body_html(body: str) -> str:
    parts = []
    for block in body.split("\n\n"):
        if block.startswith("## "):
            parts.append(f"<h2>{esc(block[3:])}</h2>")
        elif block.strip():
            parts.append(f"<p>{esc(block)}</p>")
    return "\n".join(parts)


def article_json_ld(pub: dict, base: str) -> dict:
    w = {
        "@context": "https://schema.org",
        "@type": "ScholarlyArticle" if pub["type"] != "book chapter" else "Chapter",
        "@id": pub["page_url"],
        "name": pub["title"],
        "headline": pub["title"],
        "author": [{"@type": "Person", "@id": f"{base}/#person", "name": OWNER, "url": f"{base}/"}
                   if a == OWNER else {"@type": "Person", "name": a} for a in pub["authors"]],
        "url": pub["page_url"],
        "inLanguage": "en",
        "isAccessibleForFree": True,
        "encoding": [
            {"@type": "MediaObject", "encodingFormat": "application/pdf", "contentUrl": f"{base}/{pub['local_pdf']}"},
            {"@type": "MediaObject", "encodingFormat": "text/markdown", "contentUrl": pub["md_url"]},
        ],
    }
    if pub["year"]:
        w["datePublished"] = pub["year"]
    if pub["venue"]:
        if pub["type"] == "working paper":
            w["creativeWorkStatus"] = pub["venue"]
        else:
            w["isPartOf"] = {"@type": "Periodical" if pub["type"] == "journal article" else "Book",
                             "name": pub["venue"]}
    if pub.get("doi"):
        w["sameAs"] = f"https://doi.org/{pub['doi']}"
    if pub.get("abstract"):
        w["abstract"] = pub["abstract"]
    return w


def paper_html(pub: dict, base: str, body: str) -> str:
    desc = pub.get("description") or f"{pub['title']} by {', '.join(pub['authors'])}."
    meta_lines = [f"<p class=\"meta\">{esc(', '.join(pub['authors']))}</p>"]
    venue_bits = []
    if pub["venue"]:
        venue_bits.append(pub["venue"])
    if pub["year"]:
        venue_bits.append(pub["year"])
    if venue_bits:
        meta_lines.append(f"<p class=\"venue\">{esc(' · '.join(venue_bits))}</p>")
    links = [f'<a class="button" href="{esc(base + "/" + pub["local_pdf"])}">PDF</a>']
    if pub.get("doi"):
        links.append(f'<a class="button" href="https://doi.org/{esc(pub["doi"])}">Published version</a>')
    elif pub.get("published_url"):
        links.append(f'<a class="button" href="{esc(pub["published_url"])}">Published version</a>')
    links.append(f'<a class="button" href="{esc(pub["md_url"])}">Markdown</a>')
    links.append(f'<a class="button" href="{base}/#research">All papers</a>')
    abstract_html = (f'<h2 id="abstract">Abstract</h2>\n<div class="abstract"><p>{esc(pub["abstract"])}</p></div>'
                     if pub.get("abstract") else "")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(pub['title'])} — {OWNER}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{esc(pub['page_url'])}">
<link rel="alternate" type="text/markdown" href="{esc(pub['md_url'])}" title="Markdown version">
<link rel="alternate" type="application/pdf" href="{esc(base + '/' + pub['local_pdf'])}" title="PDF">
<link rel="author" href="{base}/">
{citation_meta(pub, base)}
<meta property="og:type" content="article">
<meta property="og:title" content="{esc(pub['title'])}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{esc(pub['page_url'])}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:ital,wght@0,500;0,700;1,500;1,700&amp;display=swap" rel="stylesheet">
<script type="application/ld+json">{json.dumps(article_json_ld(pub, base), ensure_ascii=False)}</script>
<style>{PAGE_CSS}</style>
</head>
<body>
<header><nav><a class="brand" href="{base}/">{OWNER}</a><div><a href="{base}/#research">Research</a> &nbsp; <a href="{base}/#teaching">Teaching</a> &nbsp; <a href="{base}/#contact">Contact</a></div></nav></header>
<main>
<article>
<h1>{esc(pub['title'])}</h1>
{chr(10).join(meta_lines)}
<div class="links">{''.join(links)}</div>
{abstract_html}
<details>
<summary>Full text</summary>
<p class="note">Text extracted automatically from the PDF for searchability and accessibility. Tables, equations and figures may be imperfect; the PDF above is the authoritative version. Please cite the published version where one exists.</p>
<div class="fulltext">
{body_html(body)}
</div>
</details>
<h2 id="bibtex">BibTeX</h2>
<pre>{esc(bibtex(pub))}</pre>
</article>
</main>
<footer>© {OWNER} · <a href="{base}/">louisrouanet.com</a></footer>
</body>
</html>
"""


# ----------------------------------------------------------------------------
# writers: site-wide files
# ----------------------------------------------------------------------------
def llms_txt(base: str, pubs: list[dict]) -> str:
    out = [f"# {OWNER}", "",
           f"> {SITE_SUMMARY}", "",
           BIO, "",
           "Every paper below that has a PDF on this site also has an HTML page (abstract, full text, "
           "BibTeX) and a markdown version with front matter (title, authors, venue, year, abstract, "
           "PDF link). The list links to the markdown; each entry also gives the HTML page and the PDF. "
           "`llms-full.txt` concatenates all the markdown files into a single document.", "",
           f"- [Home page]({base}/): biography, publication list, teaching and contact",
           f"- [Curriculum Vitae (text)]({base}/cv.md): CV in plain text; PDF at {base}/{CV_PATH}",
           f"- [All papers in one file]({base}/llms-full.txt): full text of every paper below",
           f"- [Google Scholar profile]({SCHOLAR_URL})",
           ""]
    for group in GROUP_TYPE:
        items = [p for p in pubs if p["group"] == group]
        if not items:
            continue
        out += [f"## {group}", ""]
        for p in items:
            meta = ", ".join(x for x in (p["coauthors"], p["venue"], p["year"]) if x)
            if p.get("md_url"):
                line = f"- [{p['title']}]({p['md_url']}): {meta}."
                if p.get("description"):
                    line += " " + p["description"]
                line += f" Page: {p['page_url']} — PDF: {base}/{p['local_pdf']}"
            else:
                line = f"- [{p['title']}]({p['external_url']}): {meta}. (external link; no text version on this site)"
            out.append(line)
        out.append("")
    out += ["## Optional", "",
            f"- [Sitemap]({base}/sitemap.xml)",
            "- [Source repository](https://github.com/louisrouanet-econ/louisrouanet-econ.github.io): "
            "static site; the text versions are generated by scripts/build_llm_text.py", ""]
    return "\n".join(out)


def sitemap_xml(base: str, pubs: list[dict]) -> str:
    today = date.today().isoformat()
    urls = [f"{base}/", f"{base}/llms.txt", f"{base}/llms-full.txt", f"{base}/cv.md", f"{base}/{CV_PATH}"]
    for p in pubs:
        if p.get("page_url"):
            urls += [p["page_url"], p["md_url"]]
        if p["local_pdf"]:
            urls.append(f"{base}/{p['local_pdf']}")
    body = "\n".join(f"  <url><loc>{esc(u)}</loc><lastmod>{today}</lastmod></url>" for u in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{body}\n</urlset>\n")


def robots_txt(base: str) -> str:
    return "\n".join([
        "# All crawlers, including AI/LLM crawlers, are welcome.",
        "# A machine-readable index of this site is at /llms.txt",
        "User-agent: *",
        "Allow: /",
        "",
        f"Sitemap: {base}/sitemap.xml",
        "",
    ])


def site_json_ld(base: str, pubs: list[dict]) -> str:
    person_id = f"{base}/#person"
    person = {
        "@type": "Person", "@id": person_id, "name": OWNER, "givenName": "Louis", "familyName": "Rouanet",
        "url": f"{base}/", "jobTitle": "Assistant Professor of Political Economy",
        "affiliation": {"@type": "CollegeOrUniversity", "name": "University of Florida",
                        "department": {"@type": "Organization", "name": "Hamilton School"}},
        "alumniOf": {"@type": "CollegeOrUniversity", "name": "George Mason University"},
        "knowsAbout": ["political economy", "economic history", "French Revolution", "public choice",
                       "inflationary finance", "military conscription"],
        "sameAs": [SCHOLAR_URL, "https://twitter.com/LouisROUANET", "https://github.com/louisrouanet-econ"],
    }
    works = []
    for p in pubs:
        w = {"@type": "ScholarlyArticle" if p["type"] != "book chapter" else "Chapter",
             "name": p["title"],
             "author": [{"@id": person_id}] + [{"@type": "Person", "name": a} for a in p["authors"][1:]],
             "url": p.get("page_url") or p["external_url"]}
        if p.get("page_url"):
            w["@id"] = p["page_url"]
        if p["year"]:
            w["datePublished"] = p["year"]
        if p["venue"] and p["type"] != "working paper":
            w["isPartOf"] = {"@type": "Periodical" if p["type"] == "journal article" else "Book", "name": p["venue"]}
        works.append(w)
    data = {"@context": "https://schema.org",
            "@graph": [person, {"@type": "WebSite", "url": f"{base}/", "name": OWNER, "about": {"@id": person_id}}]
            + works}
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def set_json_ld(page: str, ld: str) -> str:
    block = f'<!-- jsonld:start -->\n<script type="application/ld+json">\n{ld}\n</script>\n<!-- jsonld:end -->'
    if "<!-- jsonld:start -->" in page:
        return re.sub(r"<!-- jsonld:start -->.*?<!-- jsonld:end -->", lambda _: block, page, flags=re.S)
    return page.replace("</head>", block + "\n</head>", 1)


def set_llms_link(page: str) -> str:
    tag = '<link rel="alternate" type="text/plain" href="llms.txt" title="Machine-readable index (llms.txt)">'
    return page if 'href="llms.txt"' in page else page.replace("</head>", tag + "\n</head>", 1)


# ----------------------------------------------------------------------------
def main() -> int:
    base = base_url()
    index = ROOT / "index.html"
    page = index.read_text(encoding="utf-8")
    pubs = parse_publications(page)
    if not pubs:
        print("No publications found in index.html", file=sys.stderr)
        return 1
    overrides = load_overrides()

    if RESEARCH_DIR.is_dir():
        shutil.rmtree(RESEARCH_DIR)
    RESEARCH_DIR.mkdir()
    seen: set[str] = set()
    full_parts: list[str] = []
    print(f"Base URL: {base}", file=sys.stderr)
    for p in pubs:
        ov = overrides.get(p["local_pdf"] or p["external_url"], {})
        for k in ("authors", "doi", "published_url", "year"):
            if ov.get(k):
                p[k] = list(ov[k]) if k == "authors" else ov[k]
        if not p["local_pdf"]:
            print(f"  - {p['title'][:60]}: external link only", file=sys.stderr)
            continue
        pdf = ROOT / p["local_pdf"]
        if not pdf.is_file():
            print(f"  ! missing PDF {p['local_pdf']}", file=sys.stderr)
            continue
        slug = p["slug"]
        if slug in seen:
            slug = f"{slug}-{p['year'] or 'b'}"
        seen.add(slug)
        p["slug"] = slug
        raw = pdf_text(pdf)
        if not raw.strip():
            continue
        body = clean_text(raw)
        p["abstract"] = ov.get("abstract") or extract_abstract(body)
        p["description"] = ov.get("description") or short_description(p["abstract"])
        p["page_url"] = f"{base}/research/{slug}/"
        p["md_url"] = f"{base}/research/{slug}.md"
        md = paper_markdown(p, base, body)
        (RESEARCH_DIR / f"{slug}.md").write_text(md, encoding="utf-8")
        (RESEARCH_DIR / slug).mkdir()
        (RESEARCH_DIR / slug / "index.html").write_text(paper_html(p, base, body), encoding="utf-8")
        full_parts.append(md)
        print(f"  + research/{slug}/ ({len(body.split())} words)", file=sys.stderr)

    cv_pdf = ROOT / CV_PATH
    if cv_pdf.is_file():
        (ROOT / "cv.md").write_text(cv_markdown(base, clean_text(pdf_text(cv_pdf))), encoding="utf-8")

    (ROOT / "llms.txt").write_text(llms_txt(base, pubs), encoding="utf-8")
    (ROOT / "llms-full.txt").write_text(llms_txt(base, pubs) + "\n\n" + "\n\n---\n\n".join(full_parts),
                                        encoding="utf-8")
    (ROOT / "sitemap.xml").write_text(sitemap_xml(base, pubs), encoding="utf-8")
    (ROOT / "robots.txt").write_text(robots_txt(base), encoding="utf-8")

    page = set_llms_link(page)
    page = set_json_ld(page, site_json_ld(base, pubs))
    index.write_text(page, encoding="utf-8")
    print(f"Done: {len(full_parts)} paper pages, llms.txt, llms-full.txt, sitemap.xml, robots.txt",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
