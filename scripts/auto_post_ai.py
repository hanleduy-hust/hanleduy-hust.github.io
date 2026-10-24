"""
Tự động tổng hợp "Điểm tin khoa học" hằng ngày cho mục Góc Chuyên gia (blog.html).

Quy trình:
  1. Lấy 10–20 bài báo mới nhất (open access) từ OpenAlex (dự phòng: Crossref)
     theo các chủ đề: hàn, in 3D kim loại, hàn vảy, đóng gói điện tử, hàn ứng dụng.
  2. Lấy hình minh họa từ các bài có giấy phép Creative Commons (CC BY…) kèm chú thích + nguồn.
  3. Gửi danh sách bài cho Gemini để viết bản tin song ngữ Việt–Anh, có trích dẫn [n].
  4. Ghi bài vào posts/<id>.json, cập nhật posts.json, lưu hình vào images/blog/<id>/.
  5. Ghi bản xem trước vào thư mục DRAFT_DIR để workflow gửi email duyệt.

Biến môi trường:
  GEMINI_API_KEY   (bắt buộc)
  GEMINI_MODEL     (tùy chọn, ví dụ gemini-3.8-flash)
  OPENALEX_API_KEY (tùy chọn, tăng hạn mức miễn phí của OpenAlex)
  CONTACT_EMAIL    (tùy chọn, dùng cho "polite pool" của OpenAlex/Crossref)
  MAX_PAPERS       (mặc định 15), LOOKBACK_DAYS (mặc định 14)
  DRAFT_DIR        (mặc định .draft)
"""

import datetime as dt
import html
import io
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
POSTS_INDEX = ROOT / "posts.json"
POSTS_DIR = ROOT / "posts"
IMG_DIR = ROOT / "images" / "blog"
DRAFT_DIR = Path(os.environ.get("DRAFT_DIR", ROOT / ".draft"))

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
OPENALEX_API_KEY = os.environ.get("OPENALEX_API_KEY", "").strip()
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "").strip()
MAX_PAPERS = int(os.environ.get("MAX_PAPERS", "15"))
MIN_PAPERS = 5
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "14"))
MAX_FIGURES = 4

UA = "Mozilla/5.0 (compatible; HanLeDuy-ScienceDigest/1.0; +https://hanleduy-hust.github.io)"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})

# Chủ đề và truy vấn tìm kiếm (tiếng Anh, vì gần như toàn bộ bài báo quốc tế bằng tiếng Anh)
TOPICS = [
    {"key": "welding", "vi": "Công nghệ hàn", "en": "Welding technology",
     "queries": ["arc welding", "laser welding", "friction stir welding", "weld microstructure mechanical properties"]},
    {"key": "metal_am", "vi": "In 3D kim loại", "en": "Metal additive manufacturing",
     "queries": ["wire arc additive manufacturing", "directed energy deposition", "laser powder bed fusion"]},
    {"key": "brazing", "vi": "Hàn vảy (brazing/soldering)", "en": "Brazing & soldering",
     "queries": ["brazing joint", "lead-free solder", "transient liquid phase bonding"]},
    {"key": "packaging", "vi": "Đóng gói điện tử", "en": "Electronic packaging",
     "queries": ["electronic packaging interconnect", "sintered silver die attach", "hybrid bonding copper", "solder joint reliability"]},
    {"key": "applications", "vi": "Hàn trong ngành trọng điểm", "en": "Welding in key industries",
     "queries": ["welding nuclear", "welding hydrogen pipeline", "welding aerospace alloy", "welding battery electric vehicle", "shipbuilding welding"]},
]

# Giấy phép cho phép tái sử dụng hình (có ghi nguồn). Không dùng biến thể ND để an toàn.
REUSABLE_LICENSES = {"cc-by", "cc-by-sa", "cc-by-nc", "cc-by-nc-sa", "cc0", "public-domain"}

# ----------------------------------------------------------------------------
# Tiện ích


def log(*a):
    print(*a, flush=True)


def slugify(text, maxlen=60):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:maxlen].strip("-") or "post"


def norm_doi(doi):
    if not doi:
        return ""
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", doi.strip(), flags=re.I).lower()


def norm_license(lic):
    if not lic:
        return ""
    lic = lic.lower()
    if "creativecommons.org" in lic:
        if "publicdomain" in lic:
            return "cc0"
        m = re.search(r"licenses/([a-z\-]+)/", lic + "/")
        return ("cc-" + m.group(1)) if m else ""
    return lic.replace("_", "-")


def http_get(url, params=None, timeout=30, tries=3, **kw):
    last = None
    for i in range(tries):
        try:
            r = SESSION.get(url, params=params, timeout=timeout, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                time.sleep(2 + 3 * i)
                continue
            return r
        except requests.RequestException as e:
            last = str(e)
            time.sleep(2 + 3 * i)
    raise RuntimeError(f"GET {url} thất bại: {last}")


def abstract_from_inverted(inv):
    if not inv:
        return ""
    pos = []
    for word, idxs in inv.items():
        for i in idxs:
            pos.append((i, word))
    return " ".join(w for _, w in sorted(pos))


def clean_text(s, limit=None, strip_label=False):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(re.sub(r"\s+", " ", s)).strip()
    if strip_label:
        s = re.sub(r"^(abstract|summary)[:.\s]+", "", s, flags=re.I)
    if limit and len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0] + "…"
    return s


def load_published():
    """Các DOI đã đăng (để không lặp lại)."""
    seen = set()
    for f in POSTS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for ref in data.get("references", []):
                if ref.get("doi"):
                    seen.add(norm_doi(ref["doi"]))
        except Exception:
            pass
    return seen


# ----------------------------------------------------------------------------
# 1. Thu thập bài báo


def openalex_search(query, since):
    params = {
        "search": query,
        "filter": f"from_publication_date:{since},is_oa:true,type:article,has_doi:true",
        "sort": "publication_date:desc",
        "per-page": 15,
        "select": "id,doi,title,publication_date,authorships,primary_location,best_oa_location,abstract_inverted_index,language",
    }
    if OPENALEX_API_KEY:
        params["api_key"] = OPENALEX_API_KEY
    if CONTACT_EMAIL:
        params["mailto"] = CONTACT_EMAIL
    r = http_get("https://api.openalex.org/works", params=params)
    r.raise_for_status()
    out = []
    for w in r.json().get("results", []):
        if w.get("language") not in (None, "en"):
            continue
        loc = w.get("best_oa_location") or {}
        src = ((w.get("primary_location") or {}).get("source") or {})
        authors = [a.get("author", {}).get("display_name", "") for a in w.get("authorships", [])]
        out.append({
            "doi": norm_doi(w.get("doi")),
            "title": clean_text(w.get("title")),
            "date": w.get("publication_date", ""),
            "journal": src.get("display_name") or "",
            "authors": [a for a in authors if a],
            "abstract": clean_text(abstract_from_inverted(w.get("abstract_inverted_index")), strip_label=True),
            "license": norm_license(loc.get("license")),
            "landing": loc.get("landing_page_url") or "",
            "pdf": loc.get("pdf_url") or "",
        })
    return out


def crossref_search(query, since):
    params = {
        "query.bibliographic": query,
        "filter": f"from-pub-date:{since},type:journal-article,has-license:true",
        "sort": "published",
        "order": "desc",
        "rows": 15,
    }
    if CONTACT_EMAIL:
        params["mailto"] = CONTACT_EMAIL
    r = http_get("https://api.crossref.org/works", params=params)
    r.raise_for_status()
    out = []
    for w in r.json().get("message", {}).get("items", []):
        lic = ""
        for l in w.get("license", []):
            n = norm_license(l.get("URL"))
            if n.startswith("cc"):
                lic = n
                break
        if not lic:  # chỉ lấy bài open access CC
            continue
        dp = (w.get("published") or w.get("issued") or {}).get("date-parts", [[None]])[0]
        date = "-".join(f"{x:02d}" if i else str(x) for i, x in enumerate(dp) if x) if dp and dp[0] else ""
        authors = [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in w.get("author", [])]
        out.append({
            "doi": norm_doi(w.get("DOI")),
            "title": clean_text((w.get("title") or [""])[0]),
            "date": date,
            "journal": (w.get("container-title") or [""])[0],
            "authors": [a for a in authors if a],
            "abstract": clean_text(w.get("abstract", ""), strip_label=True),
            "license": lic,
            "landing": w.get("URL") or "",
            "pdf": "",
        })
    return out


def collect_papers():
    since = (dt.date.today() - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    published = load_published()
    per_topic = {}
    for t in TOPICS:
        found = []
        for q in t["queries"]:
            try:
                items = openalex_search(q, since)
            except Exception as e:
                log(f"  OpenAlex lỗi ({q}): {e} → thử Crossref")
                try:
                    items = crossref_search(q, since)
                except Exception as e2:
                    log(f"  Crossref lỗi ({q}): {e2}")
                    items = []
            for it in items:
                it["topic"] = t["key"]
            found.extend(items)
            time.sleep(0.3)
        per_topic[t["key"]] = found
        log(f"[{t['key']}] {len(found)} kết quả thô")

    # Lọc + khử trùng lặp
    seen = set(published)
    pools = {}
    for key, items in per_topic.items():
        good = []
        for it in sorted(items, key=lambda x: x["date"], reverse=True):
            if not it["doi"] or it["doi"] in seen or len(it["title"]) < 15:
                continue
            if len(it["abstract"]) < 200:  # cần tóm tắt đủ dài để AI viết chính xác
                continue
            seen.add(it["doi"])
            good.append(it)
        pools[key] = good

    # Lấy xen kẽ giữa các chủ đề để bản tin đa dạng
    chosen = []
    while len(chosen) < MAX_PAPERS and any(pools.values()):
        for key in list(pools):
            if pools[key] and len(chosen) < MAX_PAPERS:
                chosen.append(pools[key].pop(0))
    log(f"Chọn {len(chosen)} bài báo mới")
    return chosen


# ----------------------------------------------------------------------------
# 2. Lấy hình minh họa từ bài báo có giấy phép CC

BAD_IMG = re.compile(r"(logo|cover|icon|banner|orcid|crossmark|badge|avatar|sprite|placeholder|journal[-_]?thumb|ads?/)", re.I)


def candidate_images(page_url, soup):
    """Trả về [(url, caption)] theo thứ tự ưu tiên: <figure> trong bài, rồi og:image."""
    cands = []
    for fig in soup.find_all("figure"):
        img = fig.find("img")
        if not img:
            continue
        src = None
        for attr in ("data-large", "data-lsrc", "data-src", "data-original", "data-lazy-src", "src"):
            if img.get(attr):
                src = img[attr]
                break
        if not src and img.get("srcset"):
            src = img["srcset"].split(",")[-1].strip().split(" ")[0]
        # nhiều trang có link tới bản hình lớn
        a = fig.find("a", href=re.compile(r"\.(jpe?g|png|webp)(\?|$)", re.I))
        if a:
            src = a["href"]
        cap_el = fig.find("figcaption") or fig.find(class_=re.compile("caption", re.I))
        caption = clean_text(cap_el.get_text(" ") if cap_el else img.get("alt", ""), 400)
        if src and not src.startswith("data:"):
            cands.append((urljoin(page_url, src), caption))
        if len(cands) >= 3:
            break
    og = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "twitter:image"})
    if og and og.get("content"):
        cands.append((urljoin(page_url, og["content"]), ""))
    return [(u, c) for u, c in cands if not BAD_IMG.search(u)]


def download_image(url, dest):
    r = http_get(url, timeout=30, tries=2)
    if r.status_code != 200 or "image" not in r.headers.get("Content-Type", "image"):
        return None
    if len(r.content) > 8_000_000:
        return None
    try:
        im = Image.open(io.BytesIO(r.content))
        im.load()
    except Exception:
        return None
    w, h = im.size
    if w < 400 or h < 200 or w / h > 4 or h / w > 3:  # bỏ ảnh nhỏ / quá dài (logo, banner)
        return None
    im = im.convert("RGB")
    if w > 1200:
        im = im.resize((1200, int(h * 1200 / w)), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest, "JPEG", quality=82, optimize=True, progressive=True)
    return im.size


def collect_figures(papers, post_id):
    figures = []
    for idx, p in enumerate(papers, start=1):
        if len(figures) >= MAX_FIGURES:
            break
        if p["license"] not in REUSABLE_LICENSES:
            continue
        page = p["landing"] or f"https://doi.org/{p['doi']}"
        try:
            r = http_get(page, timeout=30, tries=2, allow_redirects=True)
            if r.status_code != 200 or "html" not in r.headers.get("Content-Type", ""):
                continue
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            log(f"  Không mở được trang bài {p['doi']}: {e}")
            continue
        for url, caption in candidate_images(r.url, soup):
            n = len(figures) + 1
            dest = IMG_DIR / post_id / f"fig{n}.jpg"
            try:
                size = download_image(url, dest)
            except Exception:
                size = None
            if size:
                figures.append({
                    "n": n, "paper": idx, "src": f"images/blog/{post_id}/fig{n}.jpg",
                    "caption_original": caption, "source_url": url, "license": p["license"],
                })
                log(f"  + Hình {n} từ [{idx}] {p['doi']} ({size[0]}×{size[1]})")
                break
    return figures


# ----------------------------------------------------------------------------
# 3. Viết bài bằng Gemini

GEMINI_MODELS = [m for m in [os.environ.get("GEMINI_MODEL", "").strip(),
                             "gemini-flash-latest", "gemini-3.8-flash", "gemini-3.6-flash", "gemini-2.5-flash"] if m]


def gemini_json(prompt):
    last = None
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json", "maxOutputTokens": 16384},
        }
        for attempt in range(2):
            try:
                r = SESSION.post(url, headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
                                 json=body, timeout=240)
            except requests.RequestException as e:
                last = str(e)
                continue
            if r.status_code == 200:
                try:
                    cand = r.json()["candidates"][0]
                    text = "".join(p.get("text", "") for p in cand["content"]["parts"])
                    text = re.sub(r"^```(json)?|```$", "", text.strip()).strip()
                    log(f"Gemini model: {model}")
                    return json.loads(text)
                except Exception as e:
                    last = f"{model}: không đọc được JSON ({e})"
                    continue
            last = f"{model}: HTTP {r.status_code} {r.text[:300]}"
            if r.status_code in (400, 403, 404):
                break  # model không dùng được → thử model kế tiếp
            time.sleep(5)
    raise RuntimeError(f"Gọi Gemini thất bại: {last}")


def build_prompt(papers, figures, today):
    topic_names = {t["key"]: t["en"] for t in TOPICS}
    lines = []
    for i, p in enumerate(papers, 1):
        lines.append(
            f"[{i}] Topic: {topic_names[p['topic']]}\nTitle: {p['title']}\nJournal: {p['journal']} ({p['date']})\n"
            f"Abstract: {clean_text(p['abstract'], 1600)}\n")
    figs = "\n".join(f"FIG{f['n']} – from paper [{f['paper']}]; original caption: {f['caption_original'] or '(none)'}"
                     for f in figures) or "(no figures)"
    return f"""You are the science editor of the blog of Dr. Han Le Duy (TS. Hán Lê Duy), welding & joining expert
at the School of Mechanical Engineering, Hanoi University of Science and Technology (HUST).
Write today's ({today}) bilingual Vietnamese–English science news digest for engineers, lecturers and graduate students,
based ONLY on the {len(papers)} newly published papers below. Fields: welding, metal 3D printing (WAAM/DED/L-PBF),
brazing & soldering, electronic packaging, and welding in key industries.

RULES
- Group the papers into 3–5 thematic sections with an <h3> heading each. In each section, explain in plain but precise
  technical language what each study did, the key quantitative findings (only numbers that appear in the abstracts)
  and why it matters for industry/practice. Mention every paper at least once.
- Cite with square brackets right after the claim, e.g. "... tăng 18% độ bền kéo [3]." Use only the numbers [1]..[{len(papers)}].
- Do NOT invent results, authors, numbers or institutions not in the abstracts. Do not write a reference list (it is added automatically).
- Place each figure placeholder exactly once, on its own line, in the section discussing its paper: {{{{FIG1}}}}, {{{{FIG2}}}}…
  Available figures:
{figs}
- Start with a 2–3 sentence lead paragraph (<p class="lead">) summarising the day's highlights; end with a short
  <h3> section "Góc nhìn chuyên gia" / "Expert takeaway" with 2–3 practical implications for Vietnamese industry/education.
- Allowed HTML: <h3>, <p>, <ul>, <li>, <strong>, <em>, <sub>, <sup>. No inline styles, no images, no links.
- Vietnamese must be natural, using standard Vietnamese welding terminology (e.g. "vùng ảnh hưởng nhiệt (HAZ)",
  "kim loại mối hàn", "hàn vảy", "in 3D kim loại bằng hồ quang dây (WAAM)"), keeping English abbreviations in parentheses.
- Length: about 900–1300 words per language.

Return ONLY a JSON object:
{{
  "category_vi": "Điểm tin khoa học",
  "category_en": "Science Digest",
  "title_vi": "...", "title_en": "...",
  "summary_vi": "2 câu", "summary_en": "2 sentences",
  "tags": ["3-6 short English keywords"],
  "figure_captions_vi": {{"1": "chú thích tiếng Việt ngắn cho FIG1", "...": "..."}},
  "figure_captions_en": {{"1": "short English caption for FIG1", "...": "..."}},
  "content_vi": "HTML",
  "content_en": "HTML"
}}

PAPERS
{chr(10).join(lines)}"""


# ----------------------------------------------------------------------------
# 4. Hậu xử lý & ghi file

ALLOWED_TAGS = {"h3", "h4", "p", "ul", "ol", "li", "strong", "em", "b", "i", "sub", "sup", "br"}


def sanitize(html_text):
    soup = BeautifulSoup(html_text or "", "html.parser")
    for tag in soup.find_all(True):
        if tag.name in ("script", "style", "iframe", "img", "a", "figure"):
            tag.unwrap() if tag.name == "a" else tag.decompose()
        elif tag.name not in ALLOWED_TAGS:
            tag.unwrap()
        else:
            keep_class = tag.get("class") == ["lead"] and tag.name == "p"
            tag.attrs = {"class": "lead"} if keep_class else {}
    return str(soup)


def figure_html(f, caption, lang, paper):
    lic = f["license"].upper().replace("CC-", "CC ")
    src_label = "Nguồn" if lang == "vi" else "Source"
    return (f'<figure class="post-figure"><img src="{f["src"]}" alt="{html.escape(caption)}" loading="lazy">'
            f'<figcaption><strong>{"Hình" if lang == "vi" else "Figure"} {f["n"]}.</strong> {html.escape(caption)} '
            f'<span class="fig-credit">{src_label}: {html.escape(short_cite(paper))} '
            f'[<a href="#ref-{f["paper"]}">{f["paper"]}</a>], {lic}.</span></figcaption></figure>')


def short_cite(p):
    a = p["authors"]
    first = a[0].split()[-1] if a else "Anon."
    return f"{first}{' et al.' if len(a) > 1 else ''} ({p['date'][:4]})"


def finalize_content(content, figures, captions, lang, papers):
    content = sanitize(content)
    n_ref = len(papers)

    def cite(m):
        nums = [int(x) for x in re.findall(r"\d+", m.group(1))]
        nums = [n for n in nums if 1 <= n <= n_ref]
        if not nums:
            return ""
        return "<sup class=\"cite\">[" + ", ".join(f'<a href="#ref-{n}">{n}</a>' for n in nums) + "]</sup>"

    content = re.sub(r"\[(\d+(?:\s*[,–-]\s*\d+)*)\]", cite, content)
    used = set()
    for f in figures:
        cap = captions.get(str(f["n"])) or f["caption_original"] or ""
        block = figure_html(f, cap, lang, papers[f["paper"] - 1])
        pattern = re.compile(r"(<p>\s*)?\{\{\s*FIG" + str(f["n"]) + r"\s*\}\}(\s*</p>)?")
        if pattern.search(content):
            content = pattern.sub(lambda _: block, content, count=1)
            used.add(f["n"])
        else:
            content += block
    content = re.sub(r"\{\{\s*FIG\d+\s*\}\}", "", content)
    content = re.sub(r"<p>\s*</p>", "", content)
    return content


def main():
    if not GEMINI_API_KEY:
        sys.exit("Thiếu biến môi trường GEMINI_API_KEY")

    now_vn = dt.datetime.utcnow() + dt.timedelta(hours=7)
    today = now_vn.date().isoformat()

    papers = collect_papers()
    if len(papers) < MIN_PAPERS:
        log(f"Chỉ có {len(papers)} bài mới (< {MIN_PAPERS}) – hôm nay không tạo bản tin.")
        write_output(skipped="true")
        return

    post_id = f"{today}-diem-tin-khoa-hoc"
    if (POSTS_DIR / f"{post_id}.json").exists():
        post_id += "-" + now_vn.strftime("%H%M")

    figures = collect_figures(papers, post_id)
    log(f"Thu được {len(figures)} hình minh họa")

    ai = gemini_json(build_prompt(papers, figures, today))

    refs = [{
        "n": i, "authors": p["authors"][:6], "more_authors": len(p["authors"]) > 6,
        "title": p["title"], "journal": p["journal"], "date": p["date"], "doi": p["doi"],
        "url": f"https://doi.org/{p['doi']}", "license": p["license"], "topic": p["topic"],
    } for i, p in enumerate(papers, 1)]

    post = {
        "id": post_id,
        "date": today,
        "type": "digest",
        "category_vi": ai.get("category_vi") or "Điểm tin khoa học",
        "category_en": ai.get("category_en") or "Science Digest",
        "title_vi": clean_text(ai["title_vi"]),
        "title_en": clean_text(ai["title_en"]),
        "summary_vi": clean_text(ai.get("summary_vi", "")),
        "summary_en": clean_text(ai.get("summary_en", "")),
        "tags": [clean_text(t) for t in ai.get("tags", [])][:6],
        "cover": figures[0]["src"] if figures else "",
        "content_vi": finalize_content(ai["content_vi"], figures, ai.get("figure_captions_vi", {}) or {}, "vi", papers),
        "content_en": finalize_content(ai["content_en"], figures, ai.get("figure_captions_en", {}) or {}, "en", papers),
        "references": refs,
        "generated_by": "AI (Gemini) – biên tập & duyệt bởi TS. Hán Lê Duy",
    }

    POSTS_DIR.mkdir(exist_ok=True)
    (POSTS_DIR / f"{post_id}.json").write_text(json.dumps(post, ensure_ascii=False, indent=2), encoding="utf-8")

    index = json.loads(POSTS_INDEX.read_text(encoding="utf-8")) if POSTS_INDEX.exists() else []
    index = [p for p in index if p.get("id") != post_id]
    index.insert(0, {k: post[k] for k in ("id", "date", "type", "category_vi", "category_en", "title_vi",
                                          "title_en", "summary_vi", "summary_en", "tags", "cover")}
                 | {"file": f"posts/{post_id}.json"})
    POSTS_INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    (DRAFT_DIR / "post.json").write_text(json.dumps(post, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Đã tạo bài: {post['title_vi']}")
    write_output(skipped="false", post_id=post_id, title=post["title_vi"])


def write_output(**kv):
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            for k, v in kv.items():
                f.write(f"{k}={str(v).replace(chr(10), ' ')}\n")


if __name__ == "__main__":
    main()
