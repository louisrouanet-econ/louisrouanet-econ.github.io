#!/usr/bin/env python3
"""Build the machine-readable layer of the website.

Reads the publication list in index.html, extracts the text of every paper
that has a local PDF, and writes:

  papers/<slug>.md     one markdown file per paper (front matter + full text)
  cv.md                plain-text version of the CV
  llms.txt             index for LLM crawlers (https://llmstxt.org)
  llms-full.txt        every paper concatenated into one file
  sitemap.xml          sitemap covering the page, PDFs and text versions
  robots.txt           allows all crawlers and points at the sitemap

It also updates index.html in place: a "Text" link is added to each paper
that has a text version, and the schema.org JSON-LD block between the
<!-- jsonld:start --> / <!-- jsonld:end --> markers is regenerated.

Run from the repository root:  python3 scripts/build_llm_text.py
Requires poppler-utils (pdftotext). For scanned PDFs it also needs
pdftoppm + tesseract; without them the scan is skipped with a warning.

The site URL is taken from a CNAME file when one exists (GitHub Pages
creates it when a custom domain is connected), otherwise from DEFAULT_BASE.
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
PAPERS_DIR = ROOT / "papers"

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


STOPWORDS = {"the", "of", "and", "in", "a", "an", "to", "for", "on", "is", "at", "by", "or", "its"}


def slugify(title: str) -> str:
    s = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[’'`]", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    words = s.split("-")
    # keep slugs readable but bounded, and never end on a stop word
    out: list[str] = []
    for w in words:
        if len("-".join(out + [w])) > 60:
            break
        out.append(w)
    while len(out) > 2 and out[-1] in STOPWORDS:
        out.pop()
    return "-".join(out) or "paper"


def load_overrides() -> dict:
    """Optional hand-written corrections keyed by the local PDF path:
    {"wp-content/...pdf": {"abstract": "...", "authors": [...], "description": "..."}}"""
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
    s = coauthors.strip()
    s = re.sub(r"^with\s+", "", s, flags=re.I)
    parts = re.split(r",\s*|\s+and\s+|\s*&\s*", s)
    return [p.strip() for p in parts if p.strip()]


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
            year = year_m.group(1) if year_m else ""
            co_m = re.search(r'<li class="coauthor">.*?<span>(.*?)</span>\s*</li>', body, re.S)
            coauthors = text_of(co_m.group(1)) if co_m else ""
            local = not re.match(r"https?://", href)
            pubs.append({
                "title": title,
                "slug": slugify(title),
                "href": href,
                "local_pdf": href if local else "",
                "external_url": "" if local else href,
                "venue": venue,
                "year": year,
                "coauthors": coauthors,
                "authors": [OWNER] + split_authors(coauthors),
                "group": group,
                "type": GROUP_TYPE.get(group, "paper"),
                "html": art.group(0),
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
        pages = sorted(Path(tmp).glob("p-*.png"))
        chunks = []
        for p in pages:
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
    if re.match(r"^(Appendix|Online Appendix)\b", s):
        return True
    return False


def clean_text(raw: str) -> str:
    text = unicodedata.normalize("NFKC", raw)  # ligatures such as ﬁ -> fi
    text = text.replace("\f", "\n\n")
    text = text.replace("­", "")  # soft hyphen
    text = re.sub(r"[ \t]+\n", "\n", text)
    # re-join words hyphenated across a line break
    text = re.sub(r"(\w)-\n([a-z])", r"\1\2", text)
    blocks = re.split(r"\n\s*\n", text)
    out: list[str] = []
    for block in blocks:
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
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
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


ABSTRACT_CUES = re.compile(
    r"\b(this (?:paper|article|chapter|essay)|we (?:show|argue|find|provide|examine|analyze|study|use|document)"
    r"|I (?:argue|show|find|examine))\b", re.I)


def extract_abstract(text: str) -> str:
    """Return the abstract: a labelled one if present, else the first
    abstract-like opening paragraph."""
    m = re.search(r"(?m)^## Abstract\n\n(.+)$", text)
    if not m:
        m = re.search(r"(?m)^Abstract[:.]?\s+([A-Z].+)$", text)
    if m:
        abstract = re.split(r"\b(?:Keywords?|JEL [Cc]lassification|JEL [Cc]odes?)\b", m.group(1))[0]
        abstract = re.sub(r"\s+", " ", abstract).strip()
        if len(abstract) > 80:
            return abstract
    # fallback: an early paragraph that reads like an abstract
    for para in text.split("\n\n")[:10]:
        if para.startswith("## "):
            continue
        if 300 <= len(para) <= 2500 and ABSTRACT_CUES.search(para) and para.count("@") == 0:
            return re.sub(r"\s+", " ", para).strip()
    return ""


def short_description(abstract: str, limit: int = 240) -> str:
    if not abstract:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", abstract)
    desc = ""
    for s in sentences:
        if desc and len(desc) + len(s) + 1 > limit:
            break
        desc = (desc + " " + s).strip()
        if len(desc) >= limit * 0.6:
            break
    if len(desc) > limit:
        desc = desc[: limit - 1].rsplit(" ", 1)[0] + "…"
    return desc


# ----------------------------------------------------------------------------
# writers
# ----------------------------------------------------------------------------
def front_matter(pub: dict, base: str, abstract: str) -> str:
    lines = ["---",
             f"title: {json.dumps(pub['title'], ensure_ascii=False)}",
             f"authors: {json.dumps(', '.join(pub['authors']), ensure_ascii=False)}",
             f"type: {pub['type']}"]
    if pub["venue"]:
        lines.append(f"venue: {json.dumps(pub['venue'], ensure_ascii=False)}")
    if pub["year"]:
        lines.append(f"year: {pub['year']}")
    lines.append(f"pdf: {base}/{pub['local_pdf']}")
    lines.append(f"text: {base}/papers/{pub['slug']}.md")
    lines.append(f"homepage: {base}/")
    if abstract:
        lines.append(f"abstract: {json.dumps(abstract, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines)


def paper_markdown(pub: dict, base: str, body: str, abstract: str) -> str:
    head = [front_matter(pub, base, abstract), "", f"# {pub['title']}", ""]
    head.append(f"**Authors:** {', '.join(pub['authors'])}  ")
    if pub["venue"]:
        label = "Status" if pub["type"] == "working paper" else "Published in"
        head.append(f"**{label}:** {pub['venue']}  ")
    if pub["year"]:
        head.append(f"**Year:** {pub['year']}  ")
    head.append(f"**PDF (authoritative version):** {base}/{pub['local_pdf']}  ")
    head.append(f"**More papers by {OWNER}:** {base}/llms.txt")
    head.append("")
    head.append("> This is an automatically extracted plain-text version of the PDF, provided so that "
                "the paper is easy to read, search and cite for people and software (including "
                "AI assistants). Tables, equations and figures may be imperfect; the PDF is the "
                "authoritative version. Please cite the published version where one exists.")
    head.append("")
    if abstract:
        head += ["## Abstract", "", abstract, ""]
    head += ["## Full text", "", body, ""]
    return "\n".join(head)


PHONE_RE = re.compile(r"\+?\(?\d{1,3}\)?[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
STREET_RE = re.compile(r"^\s*(## )?\d+ .*\b(Drive|Dr|Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Court|Ct|Way)\b", re.I)


def redact_contact(body: str) -> str:
    """Drop street address and phone lines; the e-mail stays, the PDF keeps everything."""
    kept = []
    for line in body.split("\n"):
        if STREET_RE.match(line):
            continue
        line = PHONE_RE.sub("", line).strip()
        kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def cv_markdown(base: str, body: str) -> str:
    body = redact_contact(body)
    today = date.today().isoformat()
    return "\n".join([
        "---",
        f"title: \"Curriculum Vitae — {OWNER}\"",
        f"pdf: {base}/{CV_PATH}",
        f"homepage: {base}/",
        f"generated: {today}",
        "---",
        "",
        f"# {OWNER} — Curriculum Vitae",
        "",
        f"Plain-text version of the CV at {base}/{CV_PATH} (the PDF is authoritative; "
        "postal address and phone number are omitted here).",
        "",
        body,
        "",
    ])


SITE_SUMMARY = ("Personal academic website of Louis Rouanet, assistant professor of political economy at "
                "the University of Florida's Hamilton Center: publications, working papers, book chapters, "
                "CV and teaching in political economy and economic history.")


def llms_txt(base: str, pubs: list[dict]) -> str:
    out = [f"# {OWNER}", "",
           f"> {SITE_SUMMARY}", "",
           f"Louis Rouanet is an Assistant Professor of Political Economy at the Hamilton School, "
           f"University of Florida (previously University of Texas at El Paso and Western Kentucky "
           f"University; PhD George Mason University, 2021) and an Associate Editor at *Public Choice*. "
           f"His research is in political economy and economic history, with a focus on the French "
           f"Revolution, revolutionary and Napoleonic France, inflationary finance, military "
           f"conscription, and the political economy of institutions.", "",
           "Every paper below that has a PDF on this site also has a plain-text markdown version "
           "(the `.md` link). Those text files carry front matter with title, authors, venue, year, "
           "an abstract, and the link to the authoritative PDF. `llms-full.txt` concatenates all of "
           "them into a single file.", "",
           f"- [Home page]({base}/): biography, publication list, teaching and contact",
           f"- [Curriculum Vitae (text)]({base}/cv.md): CV in plain text; PDF at {base}/{CV_PATH}",
           f"- [All papers in one file]({base}/llms-full.txt): full text of every paper below",
           f"- [Google Scholar profile](https://scholar.google.fr/citations?user=oHFKP-2njvsC)",
           ""]
    for group in GROUP_TYPE:
        items = [p for p in pubs if p["group"] == group]
        if not items:
            continue
        out.append(f"## {group}")
        out.append("")
        for p in items:
            meta = []
            if p["coauthors"]:
                meta.append(p["coauthors"])
            if p["venue"]:
                meta.append(p["venue"])
            if p["year"]:
                meta.append(p["year"])
            meta_s = ", ".join(meta)
            if p.get("text_path"):
                link = f"{base}/{p['text_path']}"
                line = f"- [{p['title']}]({link}): {meta_s}."
                if p.get("description"):
                    line += " " + p["description"]
                line += f" PDF: {base}/{p['local_pdf']}"
            else:
                line = f"- [{p['title']}]({p['external_url']}): {meta_s}. (external link; no text version on this site)"
            out.append(line)
        out.append("")
    out.append("## Optional")
    out.append("")
    out.append(f"- [Sitemap]({base}/sitemap.xml)")
    out.append(f"- [Source repository](https://github.com/louisrouanet-econ/louisrouanet-econ.github.io): "
               "static site; the text versions are generated by scripts/build_llm_text.py")
    out.append("")
    return "\n".join(out)


def sitemap_xml(base: str, pubs: list[dict]) -> str:
    today = date.today().isoformat()
    urls = [f"{base}/", f"{base}/llms.txt", f"{base}/llms-full.txt", f"{base}/cv.md", f"{base}/{CV_PATH}"]
    for p in pubs:
        if p.get("text_path"):
            urls.append(f"{base}/{p['text_path']}")
        if p["local_pdf"]:
            urls.append(f"{base}/{p['local_pdf']}")
    body = "\n".join(
        f"  <url><loc>{html.escape(u)}</loc><lastmod>{today}</lastmod></url>" for u in urls)
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


def json_ld(base: str, pubs: list[dict]) -> str:
    person_id = f"{base}/#person"
    person = {
        "@type": "Person",
        "@id": person_id,
        "name": OWNER,
        "givenName": "Louis",
        "familyName": "Rouanet",
        "url": f"{base}/",
        "jobTitle": "Assistant Professor of Political Economy",
        "affiliation": {"@type": "CollegeOrUniversity", "name": "University of Florida",
                        "department": {"@type": "Organization", "name": "Hamilton School"}},
        "alumniOf": {"@type": "CollegeOrUniversity", "name": "George Mason University"},
        "knowsAbout": ["political economy", "economic history", "French Revolution",
                       "public choice", "inflationary finance", "military conscription"],
        "sameAs": ["https://scholar.google.fr/citations?user=oHFKP-2njvsC",
                   "https://twitter.com/LouisROUANET",
                   "https://github.com/louisrouanet-econ"],
    }
    works = []
    for p in pubs:
        url = f"{base}/{p['text_path']}" if p.get("text_path") else p["external_url"]
        w = {
            "@type": "ScholarlyArticle" if p["type"] != "book chapter" else "Chapter",
            "name": p["title"],
            "headline": p["title"],
            "author": [{"@type": "Person", "@id": person_id, "name": OWNER}] +
                      [{"@type": "Person", "name": a} for a in p["authors"][1:]],
            "url": url,
            "inLanguage": "en",
        }
        if p["year"]:
            w["datePublished"] = p["year"]
        if p["venue"]:
            if p["type"] == "working paper":
                w["creativeWorkStatus"] = p["venue"]
            else:
                w["isPartOf"] = {"@type": "Periodical" if p["type"] == "journal article" else "Book",
                                 "name": p["venue"]}
        if p.get("abstract"):
            w["abstract"] = p["abstract"]
        encodings = []
        if p["local_pdf"]:
            encodings.append({"@type": "MediaObject", "encodingFormat": "application/pdf",
                              "contentUrl": f"{base}/{p['local_pdf']}"})
        if p.get("text_path"):
            encodings.append({"@type": "MediaObject", "encodingFormat": "text/markdown",
                              "contentUrl": f"{base}/{p['text_path']}"})
        if encodings:
            w["encoding"] = encodings
        works.append(w)
    data = {"@context": "https://schema.org",
            "@graph": [person, {"@type": "WebSite", "url": f"{base}/", "name": OWNER,
                                "about": {"@id": person_id}}] + works}
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


# ----------------------------------------------------------------------------
# index.html update
# ----------------------------------------------------------------------------
TEXT_ICON = ('<svg class="icon icon-text" aria-hidden="true" viewBox="0 0 24 24" fill="none" '
             'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
             '<path d="M6 3h8l5 5v13H6z"/><path d="M14 3v5h5M9 13h7M9 17h7"/></svg>')


def add_text_links(page: str, pubs: list[dict]) -> str:
    for p in pubs:
        if not p.get("text_path"):
            continue
        art = p["html"]
        if f'href="{p["text_path"]}"' in art:
            continue
        li = (f'<li class="textver">{TEXT_ICON}<span><a href="{p["text_path"]}" '
              f'aria-label="Plain-text version: {html.escape(p["title"], quote=True)}">Text version</a></span></li>')
        if '<ul class="details">' in art:
            new = art.replace("</ul>", li + "</ul>", 1)
        else:
            new = art.replace('<div class="publication-bottom">',
                              f'<div class="publication-bottom"><ul class="details">{li}</ul>', 1)
        page = page.replace(art, new, 1)
        p["html"] = new
    return page


def set_json_ld(page: str, ld: str) -> str:
    block = f'<!-- jsonld:start -->\n<script type="application/ld+json">\n{ld}\n</script>\n<!-- jsonld:end -->'
    if "<!-- jsonld:start -->" in page:
        return re.sub(r"<!-- jsonld:start -->.*?<!-- jsonld:end -->", lambda _: block, page, flags=re.S)
    return page.replace("</head>", block + "\n</head>", 1)


def set_llms_link(page: str) -> str:
    tag = '<link rel="alternate" type="text/plain" href="llms.txt" title="Machine-readable index (llms.txt)">'
    if 'href="llms.txt"' in page:
        return page
    return page.replace("</head>", tag + "\n</head>", 1)


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

    PAPERS_DIR.mkdir(exist_ok=True)
    seen: set[str] = set()
    full_parts: list[str] = []
    print(f"Base URL: {base}", file=sys.stderr)
    for p in pubs:
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
        ov = overrides.get(p["local_pdf"], {})
        if ov.get("authors"):
            p["authors"] = list(ov["authors"])
        abstract = ov.get("abstract") or extract_abstract(body)
        p["abstract"] = abstract
        p["description"] = ov.get("description") or short_description(abstract)
        p["text_path"] = f"papers/{slug}.md"
        md = paper_markdown(p, base, body, abstract)
        (ROOT / p["text_path"]).write_text(md, encoding="utf-8")
        full_parts.append(md)
        print(f"  + {p['text_path']} ({len(body.split())} words)", file=sys.stderr)

    cv_pdf = ROOT / CV_PATH
    if cv_pdf.is_file():
        cv_md = cv_markdown(base, clean_text(pdf_text(cv_pdf)))
        (ROOT / "cv.md").write_text(cv_md, encoding="utf-8")

    (ROOT / "llms.txt").write_text(llms_txt(base, pubs), encoding="utf-8")
    full = llms_txt(base, pubs) + "\n\n" + "\n\n---\n\n".join(full_parts)
    (ROOT / "llms-full.txt").write_text(full, encoding="utf-8")
    (ROOT / "sitemap.xml").write_text(sitemap_xml(base, pubs), encoding="utf-8")
    (ROOT / "robots.txt").write_text(robots_txt(base), encoding="utf-8")

    page = add_text_links(page, pubs)
    page = set_llms_link(page)
    page = set_json_ld(page, json_ld(base, pubs))
    index.write_text(page, encoding="utf-8")
    print(f"Done: {len(full_parts)} text versions, llms.txt, llms-full.txt, sitemap.xml, robots.txt",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
