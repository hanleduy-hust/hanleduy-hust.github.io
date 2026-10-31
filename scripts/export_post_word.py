"""Export an approved post snapshot to bilingual Word. No Drive credentials required.
Usage: python scripts/export_post_word.py POST.json OUTPUT.docx --root CHECKOUT
Optional --asset-base https://raw.githubusercontent.com/OWNER/REPO/MERGE_SHA/
Drive upload is performed by the ChatGPT merge-triggered automation.
"""
import argparse
import io
import json
from pathlib import Path
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup, NavigableString
from PIL import Image
from docx import Document
from docx.shared import Cm, Pt, RGBColor


def export_post(post, output, root, asset_base=""):
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.top_margin = sec.bottom_margin = Cm(2)
    sec.left_margin = sec.right_margin = Cm(2.3)
    for name in ("Normal", "Title", "Heading 1", "Heading 2", "Caption"):
        style = doc.styles[name]
        style.font.name = "Times New Roman"
        style.font.color.rgb = RGBColor(0, 0, 0)
    doc.styles["Normal"].font.size = Pt(12)
    doc.styles["Normal"].paragraph_format.space_after = Pt(6)
    doc.styles["Normal"].paragraph_format.line_spacing = 1.15

    def inline(paragraph, node, bold=False, italic=False, sub=False, sup=False):
        if isinstance(node, NavigableString):
            run = paragraph.add_run(str(node))
            run.bold, run.italic = bold, italic
            run.font.subscript, run.font.superscript = sub, sup
            return
        if node.name == "br":
            paragraph.add_run().add_break()
            return
        for child in node.children:
            inline(paragraph, child, bold or node.name in ("b", "strong"),
                   italic or node.name in ("i", "em"), sub or node.name == "sub",
                   sup or node.name == "sup")

    def picture(src):
        # Only repository-relative images; never fetch arbitrary HTML-provided hosts.
        if urlsplit(src).scheme or urlsplit(src).netloc:
            raise ValueError("Expected repository-relative figure: " + src)
        rel = src.lstrip("/")
        if ".." in Path(rel).parts:
            raise ValueError("Unsafe figure path")
        local = (root / rel).resolve()
        if not local.is_relative_to(root.resolve()):
            raise ValueError("Figure outside repository")
        if local.is_file():
            return io.BytesIO(local.read_bytes())
        if not asset_base:
            raise FileNotFoundError(rel)
        response = requests.get(asset_base.rstrip("/") + "/" + rel, timeout=45)
        response.raise_for_status()
        return io.BytesIO(response.content)

    def blocks(node):
        for child in node.children:
            if isinstance(child, NavigableString):
                if str(child).strip():
                    inline(doc.add_paragraph(), child)
                continue
            if child.name in ("script", "style"):
                continue
            if child.name in ("h2", "h3", "h4"):
                p = doc.add_paragraph(style="Heading 1" if child.name != "h4" else "Heading 2")
                inline(p, child)
            elif child.name == "figure":
                for img in child.find_all("img"):
                    p = doc.add_paragraph()
                    p.paragraph_format.keep_with_next = True
                    image_data = picture(img["src"])
                    with Image.open(image_data) as image:
                        width, height = image.size
                    image_data.seek(0)
                    p.add_run().add_picture(image_data, width=Cm(min(16, 21 * width / height)))
                caption = child.find("figcaption")
                if caption:
                    inline(doc.add_paragraph(style="Caption"), caption)
            elif child.name in ("p", "li"):
                p = doc.add_paragraph(style="List Bullet" if child.name == "li" else "Normal")
                inline(p, child)
            else:
                blocks(child)

    for lang in ("vi", "en"):
        if lang == "en":
            doc.add_page_break()
        doc.add_paragraph(post["title_" + lang], style="Title")
        doc.add_paragraph(("Ngày đăng: " if lang == "vi" else "Published: ") + post["date"])
        doc.add_paragraph("https://hanleduy-hust.github.io/blog.html?post=" + post["id"])
        if post.get("summary_" + lang):
            doc.add_paragraph(post["summary_" + lang])
        blocks(BeautifulSoup(post["content_" + lang], "html.parser"))
        doc.add_paragraph("Tài liệu tham khảo" if lang == "vi" else "References", style="Heading 1")
        for ref in post.get("references", []):
            authors = ", ".join(ref.get("authors", []))
            if ref.get("more_authors"):
                authors += " et al."
            doc.add_paragraph(
                f'[{ref["n"]}] {authors}. {ref["title"]}. {ref.get("journal", "")}, '
                f'{ref.get("date", "")}. {ref.get("url") or ref.get("doi", "")}'
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("post", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--asset-base", default="")
    args = parser.parse_args()
    export_post(json.loads(args.post.read_text(encoding="utf-8")),
                args.output, args.root, args.asset_base)
