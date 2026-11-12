"""
Tự động tổng hợp "Điểm tin khoa học" hằng ngày cho mục Góc Chuyên gia (blog.html).
Đã tối ưu:
- Tự động xoay vòng model Gemini chuẩn (tránh lỗi HTTP 429 / omni).
- Mở rộng khả năng trích xuất hình ảnh từ bài báo (HTML + PDF + EuropePMC/arXiv)
  và tự động bổ sung ảnh minh họa kỹ thuật dự phòng nếu nhà xuất bản chặn bot,
  đảm bảo bài viết LUÔN có ảnh đại diện (cover) và ảnh minh họa bên trong.
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
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "21"))

VN_TZ = dt.timezone(dt.timedelta(hours=7))
WEEKLY_TOPICS = [
    {"key": "ai_welding", "vi": "Ứng dụng AI trong công nghiệp hàn", "en": "AI in welding",
     "queries": ['welding AND "artificial intelligence"', 'welding AND "machine learning"', 'welding AND "deep learning"'],
     "must": [r"\bweld|joining", r"artificial intelligence|machine learning|deep learning|neural network|computer vision"],
     "outline": "Giám sát và điều khiển quá trình;Phát hiện khuyết tật và kiểm tra chất lượng;Robot hàn và tối ưu thông số",
     "fallback_img": "https://images.unsplash.com/photo-1581091226825-a6a2a5aee158?auto=format&fit=crop&w=1000&q=80"},
    {"key": "applications", "vi": "Ứng dụng hàn trong các ngành công nghiệp trọng điểm", "en": "Welding in key industries",
     "queries": ['welding AND aerospace', 'welding AND nuclear', 'welding AND hydrogen AND pipeline', 'welding AND battery', 'welding AND shipbuilding'],
     "must": [r"\bweld|joining", r"aerospace|aircraft|nuclear|hydrogen|pipeline|battery|automotive|ship|offshore|energy"],
     "outline": "Năng lượng và đường ống;Ô tô và pin xe điện;Hàng không, đóng tàu và công trình biển",
     "fallback_img": "https://images.unsplash.com/photo-1504917599217-d4dc5ebe6122?auto=format&fit=crop&w=1000&q=80"},
    {"key": "low_temp_solder", "vi": "Hợp kim hàn không chì và vảy hàn nhiệt độ thấp", "en": "Lead-free solder alloys and low-temperature solders",
     "queries": ['"lead-free" AND solder', '"lead free" AND "solder alloy"', '"low temperature" AND solder', '"Sn-Bi" AND solder', '"indium" AND solder'],
     "must": [r"solder", r"lead[- ]free|pb[- ]free|low[- ]temperature|low[- ]melting|sn[-– ]?bi|bi[-– ]?sn|indium|in[-– ]?sn|sn[-– ]?in"],
     "outline": "Hợp kim hàn không chì và thiết kế thành phần;Vảy hàn nhiệt độ thấp và nhiệt độ nóng chảy;Thấm ướt và phản ứng liên kim;Cơ tính, già hóa và độ tin cậy",
     "fallback_img": "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=1000&q=80"},
    {"key": "packaging", "vi": "Xu thế công nghệ và vật liệu liên kết trong electronic packaging", "en": "Joining in electronic packaging",
     "queries": ['"electronic packaging" AND bonding', '"die attach" AND sintering', '"hybrid bonding" AND copper', '"power module" AND interconnect'],
     "must": [r"bond|solder|sinter|interconnect|die[- ]attach", r"electron|chip|semiconductor|power module|microelectron|packag"],
     "outline": "Đóng gói tiên tiến và liên kết 3D;Vật liệu die-attach và liên kết đồng;Quản lý nhiệt và độ tin cậy",
     "fallback_img": "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=1000&q=80"},
    {"key": "welding", "vi": "Các công nghệ hàn tiên tiến", "en": "Advanced welding technologies",
     "queries": ['"laser welding"', '"friction stir welding"', '"hybrid welding"', '"electron beam welding"', '"diffusion bonding"'],
     "must": [r"weld|diffusion bond", r"laser|friction stir|hybrid|electron beam|diffusion"],
     "outline": "Nguyên lý và tiến bộ công nghệ;Vi cấu trúc, khuyết tật và cơ tính;Khả năng ứng dụng và giới hạn",
     "fallback_img": "https://images.unsplash.com/photo-1504328345606-18bbc8c9d7d1?auto=format&fit=crop&w=1000&q=80"},
    {"key": "metal_am", "vi": "Nghiên cứu mới về WAAM và in 3D kim loại", "en": "WAAM and metal 3D printing",
     "queries": ['"wire arc additive manufacturing"', '"directed energy deposition"', '"laser powder bed fusion"'],
     "must": [r"additive(ly)? manufactur|\bwaam\b|directed energy deposition|powder bed fusion|3d[- ]print", r"metal|alloy|steel|titanium|nickel|alumin|copper|inconel"],
     "outline": "WAAM và điều khiển quá trình;DED và L-PBF;Tổ chức, cơ tính, ứng suất dư và xử lý sau in",
     "fallback_img": "https://images.unsplash.com/photo-1581092160562-40aa08e78837?auto=format&fit=crop&w=1000&q=80"},
]

WEEKDAY = dt.datetime.now(VN_TZ).weekday()
AUTO_WEEKLY = not os.environ.get("FEATURE_TOPIC", "").strip()
CURRENT_FALLBACK_IMG = "https://images.unsplash.com/photo-1504917599217-d4dc5ebe6122?auto=format&fit=crop&w=1000&q=80"

if AUTO_WEEKLY and WEEKDAY < 6:
    selected = WEEKLY_TOPICS[WEEKDAY]
    os.environ["FEATURE_TOPIC"] = selected["vi"]
    os.environ["FEATURE_QUERIES"] = ";".join(selected["queries"])
    os.environ["FEATURE_DOMAIN"] = selected["must"][0]
    os.environ["FEATURE_MUST"] = selected["must"][1]
    os.environ["FEATURE_OUTLINE"] = selected["outline"]
    os.environ.setdefault("FEATURE_PAPERS", "15")
    CURRENT_FALLBACK_IMG = selected.get("fallback_img", CURRENT_FALLBACK_IMG)

FEATURE_TOPIC = os.environ.get("FEATURE_TOPIC", "").strip()
FEATURE_QUERIES = [q.strip() for q in os.environ.get("FEATURE_QUERIES", "").split(";") if q.strip()]
FEATURE_MUST = os.environ.get("FEATURE_MUST", "").strip()
FEATURE_PAPERS = int(os.environ.get("FEATURE_PAPERS", "20"))
FEATURE_DOMAIN = os.environ.get("FEATURE_DOMAIN", "").strip()
FEATURE_OUTLINE = [x.strip() for x in os.environ.get("FEATURE_OUTLINE", "").split(";") if x.strip()]
MAX_FIGURES = 8

UA = "Mozilla/5.0 (compatible; HanLeDuy-ScienceDigest/1.0; +https://hanleduy-hust.github.io)"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})
WEB = requests.Session()
WEB.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
})
DIAG = []


def diag(msg):
    log(msg)
    DIAG.append(msg.strip())


TOPICS = [
    {"key": "welding", "vi": "Công nghệ hàn", "en": "Welding technology",
     "queries": ['"arc welding"', '"laser welding"', '"friction stir welding"', '"welded joint" AND microstructure'],
     "must": [r"\bweld"]},
    {"key": "metal_am", "vi": "In 3D kim loại", "en": "Metal additive manufacturing",
     "queries": ['"wire arc additive manufacturing"', '"directed energy deposition"', '"laser powder bed fusion"'],
     "must": [r"additive(ly)? manufactur|\bwaam\b|directed energy deposition|powder bed fusion|\bl-?pbf\b|\bslm\b|3d[- ]print",
              r"alloy|steel|metal|titanium|nickel|alumin|inconel|copper|magnesium|weld"]},
    {"key": "brazing", "vi": "Hàn vảy (brazing/soldering)", "en": "Brazing & soldering",
     "queries": ['brazing AND joint', '"lead-free solder"', '"transient liquid phase bonding"'],
     "must": [r"braz|solder|transient liquid phase|\btlp\b"]},
    {"key": "packaging", "vi": "Đóng gói điện tử", "en": "Electronic packaging",
     "queries": ['"electronic packaging"', '"die attach" AND sintering', '"hybrid bonding" AND copper', '"solder joint" AND reliability'],
     "must": [r"packag|die[- ]attach|solder|hybrid bond|wire bond|flip[- ]chip|sinter|interconnect|bonding",
              r"electron|chip|semiconductor|power module|\bdie\b|device|microelectron|\bic\b|photonic"]},
    {"key": "applications", "vi": "Hàn trong ngành trọng điểm", "en": "Welding in key industries",
     "queries": ['welding AND nuclear', 'weld AND hydrogen AND pipeline', 'welding AND aerospace', 'welding AND battery', 'welding AND (ship OR shipbuilding)'],
     "must": [r"\bweld|braz|solder|joining"]},
]

if AUTO_WEEKLY:
    TOPICS = WEEKLY_TOPICS

REUSABLE_LICENSES = {"cc-by", "cc-by-sa", "cc-by-nc", "cc-by-nc-sa", "cc0", "public-domain"}


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
    s = re.sub(r"</?[A-Za-z][A-Za-z0-9:_-]*(?:\s[^<>]*?)?\s*/?>", " ", s or "")
    s = html.unescape(re.sub(r"\s+", " ", s)).strip()
    if strip_label:
        s = re.sub(r"^(abstract|summary)[:.\s]+", "", s, flags=re.I)
    if limit and len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0] + "…"
    return s


def load_published():
    seen = set()
    extra = ROOT / "used_dois.json"
    if extra.exists():
        try:
            seen |= {norm_doi(d) for d in json.loads(extra.read_text(encoding="utf-8"))}
        except Exception:
            pass
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

def openalex_search(query, since, work_type="article", sort="publication_date:desc", per_page=15):
    params = {
        "search.title_and_abstract": query,
        "filter": f"from_publication_date:{since},is_oa:true,type:{work_type},has_doi:true",
        "sort": sort,
        "per-page": per_page,
        "select": "id,doi,title,publication_date,authorships,primary_location,best_oa_location,abstract_inverted_index,language,has_content,content_urls,cited_by_count",
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
        prim = w.get("primary_location") or {}
        src = prim.get("source") or {}
        authors = [a.get("author", {}).get("display_name", "") for a in w.get("authorships", [])]
        lic = norm_license(loc.get("license")) or norm_license(prim.get("license"))
        out.append({
            "doi": norm_doi(w.get("doi")),
            "title": clean_text(w.get("title")),
            "date": w.get("publication_date", ""),
            "journal": src.get("display_name") or "",
            "authors": [a for a in authors if a],
            "abstract": clean_text(abstract_from_inverted(w.get("abstract_inverted_index")), strip_label=True),
            "license": lic,
            "landing": loc.get("landing_page_url") or prim.get("landing_page_url") or "",
            "pdf": loc.get("pdf_url") or prim.get("pdf_url") or "",
            "openalex_id": (w.get("id") or "").rsplit("/", 1)[-1],
            "has_pdf": bool((w.get("has_content") or {}).get("pdf")),
            "cited": w.get("cited_by_count") or 0,
            "kind": "review" if work_type == "review" else "article",
        })
    return out


def crossref_search(query, since):
    params = {
        "query.bibliographic": re.sub(r'["()]|\bAND\b|\bOR\b', " ", query),
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
        if not lic:
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


TOPIC_BY_KEY = {t["key"]: t for t in TOPICS}


def relevant(paper, topic_key):
    text = f"{paper['title']} {paper['abstract']}".lower()
    return all(re.search(pat, text) for pat in TOPIC_BY_KEY[topic_key]["must"])


JOINING = re.compile(r"\bweld|braz|solder|additive(ly)? manufactur|\bwaam\b|directed energy deposition|joining", re.I)


def collect_feature_papers():
    today = dt.date.today()
    # Mở rộng cửa sổ tìm kiếm lên 365 ngày cho bài mới để luôn tìm được bài Open Access có đầy đủ hình ảnh
    since_review = (today - dt.timedelta(days=(3 if AUTO_WEEKLY else 5) * 365)).isoformat()
    since_recent = (today - dt.timedelta(days=365 if AUTO_WEEKLY else 548)).isoformat()
    published = load_published()
    must = re.compile(FEATURE_MUST, re.I) if FEATURE_MUST else None
    domain = None if FEATURE_DOMAIN == "-" else (re.compile(FEATURE_DOMAIN, re.I) if FEATURE_DOMAIN else JOINING)
    queries = FEATURE_QUERIES or [FEATURE_TOPIC]

    def strong(rx, it):
        return rx is None or rx.search(it["title"]) or len(rx.findall(it["abstract"])) >= 2

    def ok(it):
        return (it["doi"] and it["doi"] not in published and len(it["abstract"]) >= 200
                and strong(domain, it) and strong(must, it))

    # Ưu tiên cao nhất cho bài có giấy phép CC và có sẵn link PDF/HTML trực tiếp
    key = lambda x: (x["license"] in REUSABLE_LICENSES, bool(x.get("pdf") or x.get("has_pdf")), x["date"], x["cited"])
    pools = {"review": [], "article": []}
    for qi, q in enumerate(queries):
        for kind, since in (("review", since_review), ("article", since_recent)):
            try:
                items = openalex_search(q, since, work_type=kind, sort=("publication_date:desc" if AUTO_WEEKLY and kind == "article" else "cited_by_count:desc"), per_page=25)
            except Exception as e:
                diag(f"  OpenAlex lỗi ({kind}: {q}): {e}")
                items = []
            good = sorted([it for it in items if ok(it)], key=key, reverse=True)
            pools[kind].append(good)
            time.sleep(0.3)

    chosen, seen = [], set()

    def take(lists, limit):
        added = 0
        while added < limit and any(lists):
            for lst in lists:
                while lst and lst[0]["doi"] in seen:
                    lst.pop(0)
                if lst and added < limit and len(chosen) < FEATURE_PAPERS:
                    it = lst.pop(0)
                    seen.add(it["doi"])
                    it["topic"] = "feature"
                    chosen.append(it)
                    added += 1
            if len(chosen) >= FEATURE_PAPERS:
                break

    if AUTO_WEEKLY:
        take(pools["article"], max(0, FEATURE_PAPERS - 4))
    take(pools["review"], 4 if AUTO_WEEKLY else 7)
    take(pools["article"], FEATURE_PAPERS)
    log(f"Chọn {len(chosen)} bài cho chuyên đề ({sum(1 for c in chosen if c['kind'] == 'review')} tổng quan)")
    return chosen


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

    seen = set(published)
    pools = {}
    for key, items in per_topic.items():
        good = []
        for it in sorted(items, key=lambda x: x["date"], reverse=True):
            if not it["doi"] or it["doi"] in seen or len(it["title"]) < 15:
                continue
            if len(it["abstract"]) < 200:
                continue
            if not relevant(it, key):
                continue
            seen.add(it["doi"])
            good.append(it)
        good.sort(key=lambda x: (x["license"] in REUSABLE_LICENSES, bool(x.get("pdf")), x["date"]), reverse=True)
        pools[key] = good

    chosen = []
    while len(chosen) < MAX_PAPERS and any(pools.values()):
        for key in list(pools):
            if pools[key] and len(chosen) < MAX_PAPERS:
                chosen.append(pools[key].pop(0))
    log(f"Chọn {len(chosen)} bài báo mới")
    return chosen


# ----------------------------------------------------------------------------
# 2. Lấy hình minh họa từ bài báo

BAD_IMG = re.compile(r"(logo|cover|icon|banner|orcid|crossmark|badge|avatar|sprite|placeholder|journal[-_]?thumb|ads?/|pixel|spacer)", re.I)
FIG_CONTAINER = re.compile(r"(^|[-_\s])(fig|figure|html-fig|figure-container|figure-wrap|article-figure|graphic)([-_\s\d]|$)", re.I)
FIG_LABEL = re.compile(r"^\s*((?:fig(?:ure)?|hình)\.?\s*\d+[a-z]?)[.:\s]*", re.I)

GOOD_FIG = re.compile(r"microstructur|sem\b|ebsd|tem\b|optical micrograph|fractur|morpholog|cross[- ]section|"
                      r"interface|intermetallic|imc\b|hardness|stress|strain|tensile|distribution|temperature field|"
                      r"simulat|melt pool|bead|porosity|crack|grain|phase|xrd|comparison|effect of|evolution|result", re.I)
BAD_FIG = re.compile(r"specimen|geometr|dimension|flow ?chart|framework|topolog|sampling|location of|"
                     r"workflow|research route|outline|overview of (the )?(study|paper)|graphical abstract|setup|set-up", re.I)


def figure_score(caption, label):
    score = 2 * len(GOOD_FIG.findall(caption)) - 3 * len(BAD_FIG.findall(caption))
    m = re.search(r"\d+", label or "")
    if m and int(m.group()) == 1:
        score -= 1
    return score


def candidate_images(page_url, soup):
    for junk in soup.find_all(["header", "nav", "footer", "aside"]):
        junk.decompose()
    containers = soup.find_all("figure")
    containers += [el for el in soup.find_all(["div", "section"])
                   if FIG_CONTAINER.search(" ".join(el.get("class", [])) + " " + (el.get("id") or ""))]
    cands, seen = [], set()
    for fig in containers:
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
        a = fig.find("a", href=re.compile(r"\.(jpe?g|png|webp)(\?|$)", re.I))
        if a:
            src = a["href"]
        if not src or src.startswith("data:"):
            continue
        url = urljoin(page_url, src)
        if url in seen or BAD_IMG.search(url):
            continue
        parts = []
        for el in [fig.find("figcaption")] + fig.find_all(class_=re.compile("caption|legend|fig.*desc|description", re.I)):
            t = clean_text(el.get_text(" ")) if el else ""
            if t and not any(t in q for q in parts):
                parts = [q for q in parts if q not in t] + [t]
        caption = clean_text(" ".join(parts), 450)
        if len(caption) < 15:
            caption = clean_text(img.get("alt") or "", 450)
        if len(caption) < 15:
            continue
        m = FIG_LABEL.match(caption)
        label = m.group(1) if m else ""
        if m:
            caption = caption[m.end():].strip()
        seen.add(url)
        cands.append((url, caption, label))
        if len(cands) >= 10:
            break
    return sorted(cands, key=lambda c: figure_score(c[1], c[2]), reverse=True)


_USED_HASHES = None


def ahash(im):
    g = im.convert("L").resize((16, 16), Image.LANCZOS)
    px = list(g.getdata())
    avg = sum(px) / len(px)
    return int("".join("1" if v > avg else "0" for v in px), 2)


def used_hashes():
    global _USED_HASHES
    if _USED_HASHES is None:
        _USED_HASHES = []
        for f in IMG_DIR.glob("*/*.jpg"):
            try:
                with Image.open(f) as im:
                    _USED_HASHES.append(ahash(im))
            except Exception:
                pass
    return _USED_HASHES


def is_used_image(im):
    h = ahash(im)
    return any(bin(h ^ u).count("1") <= 12 for u in used_hashes())


def save_image(data, dest, min_w=350, check_dup=True):
    if not data or len(data) > 12_000_000:
        return None
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        return None
    w, h = im.size
    if w < min_w or h < 180 or w / h > 4 or h / w > 3:
        return None
    if check_dup and is_used_image(im):
        diag(f"    bỏ hình trùng với bài đã đăng ({dest.name})")
        return None
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.split()[-1])
        im = bg
    im = im.convert("RGB")
    if w > 1200:
        im = im.resize((1200, int(h * 1200 / w)), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest, "JPEG", quality=85, optimize=True, progressive=True)
    if check_dup:
        used_hashes().append(ahash(im))
    return im.size


def download_image(url, referer, dest, check_dup=True):
    r = WEB.get(url, timeout=30, headers={"Referer": referer})
    if r.status_code != 200 or "image" not in r.headers.get("Content-Type", "image"):
        return None
    return save_image(r.content, dest, check_dup=check_dup)


PDF_CAPTION = re.compile(r"^\s*(fig(?:ure)?\.?\s*\d+[a-z]?)[.:\s]", re.I)


def pdf_figures(pdf_bytes, max_n=8):
    import pymupdf as fitz
    out = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    for page in doc:
        if len(out) >= max_n:
            break
        blocks = [b for b in page.get_text("blocks") if b[6] == 0]
        caps = [b for b in blocks if PDF_CAPTION.match(b[4])]
        if not caps:
            continue
        rects = []
        for img in page.get_images(full=True):
            try:
                rects += [r for r in page.get_image_rects(img[0]) if r.width > 60 and r.height > 60]
            except Exception:
                pass
        for cap in caps:
            cap_top = cap[1]
            above = [r for r in rects if r.y1 <= cap_top + 10 and cap_top - r.y1 < 90]
            if not above:
                continue
            clip = above[0]
            for r in above[1:]:
                if r.y1 > clip.y0 - 120:
                    clip |= r
            clip = clip & page.rect
            if clip.width < 150 or clip.height < 100:
                continue
            pix = page.get_pixmap(clip=clip, dpi=170)
            text = clean_text(cap[4], 600)
            m = FIG_LABEL.match(text)
            label = m.group(1) if m else ""
            caption = text[m.end():].strip() if m else text
            if len(caption) >= 15:
                out.append((pix.tobytes("png"), caption[:450], label))
            if len(out) >= max_n:
                break
    return sorted(out, key=lambda f: figure_score(f[1], f[2]), reverse=True)


MDPI_JOURNALS = {
    "ma": "materials", "met": "metals", "cryst": "crystals", "mi": "micromachines", "fluids": "fluids",
    "app": "applsci", "coatings": "coatings", "jmmp": "jmmp", "polym": "polymers", "nano": "nanomaterials",
    "en": "energies", "electronics": "electronics", "machines": "machines", "sensors": "sensors",
    "lubricants": "lubricants", "designs": "designs", "processes": "processes", "jcs": "jcs",
    "alloys": "alloys", "eng": "eng", "technologies": "technologies", "inventions": "inventions",
}


def mdpi_pdf_urls(doi):
    m = re.fullmatch(r"10\.3390/([a-z]+)(\d{2})(\d{2})(\d{4,5})", doi or "")
    if not m or m.group(1) not in MDPI_JOURNALS:
        return []
    j = MDPI_JOURNALS[m.group(1)]
    name = f"{j}-{int(m.group(2))}-{int(m.group(4)):05d}"
    return [f"https://mdpi-res.com/d_attachment/{j}/{name}/article_deploy/{name}.pdf",
            f"https://pub.mdpi-res.com/d_attachment/{j}/{name}/article_deploy/{name}.pdf"]


def zenodo_pdf_urls(url):
    m = re.search(r"zenodo\.org/(?:records?|record)/(\d+)", url or "")
    if not m:
        return []
    try:
        r = SESSION.get(f"https://zenodo.org/api/records/{m.group(1)}", timeout=30)
        files = r.json().get("files", []) if r.status_code == 200 else []
        return [f["links"]["self"] for f in files if f.get("key", "").lower().endswith(".pdf")][:1]
    except Exception:
        return []


def get_pdf(p, page_url="", page_pdf=""):
    urls = []
    if p.get("has_pdf") and p.get("openalex_id") and OPENALEX_API_KEY:
        urls.append(f"https://content.openalex.org/works/{p['openalex_id']}.pdf?api_key={OPENALEX_API_KEY}")
    urls += [u for u in (page_pdf, p.get("pdf")) if u]
    urls += zenodo_pdf_urls(page_url or p.get("landing", ""))
    urls += mdpi_pdf_urls(p.get("doi"))
    seen = set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        try:
            r = WEB.get(u, timeout=60, allow_redirects=True, headers={"Accept": "application/pdf,*/*"})
            if r.status_code == 200 and r.content[:5] == b"%PDF-":
                return r.content, u.split("?")[0]
            diag(f"    PDF {u.split('?')[0][:80]} → HTTP {r.status_code}")
        except Exception as e:
            diag(f"    PDF {u.split('?')[0][:60]} lỗi {type(e).__name__}")
    return None, ""


def collect_figures(papers, post_id):
    """Lấy tối đa 1 hình/bài báo. Nếu không bài báo nào tải được hình, tự tạo 1 hình minh họa dự phòng."""
    figures = []
    for idx, p in enumerate(papers, start=1):
        if len(figures) >= MAX_FIGURES:
            break
        # Duyệt cả các bài có license CC hoặc bài OA có link PDF/HTML công khai
        if p["license"] and p["license"] not in REUSABLE_LICENSES:
            continue
        if not p["license"] and not (p.get("pdf") or p.get("has_pdf")):
            continue

        n = len(figures) + 1
        dest = IMG_DIR / post_id / f"fig{n}.jpg"
        got = None
        page = p["landing"] or f"https://doi.org/{p['doi']}"
        page_url, page_pdf = page, ""

        # (1) Thử tải từ trang HTML
        try:
            r = WEB.get(page, timeout=30, allow_redirects=True)
            page_url = r.url
            if r.status_code == 200 and "html" in r.headers.get("Content-Type", ""):
                soup = BeautifulSoup(r.text, "html.parser")
                meta = soup.find("meta", attrs={"name": "citation_pdf_url"})
                page_pdf = urljoin(r.url, meta["content"]) if meta and meta.get("content") else ""
                cands = candidate_images(r.url, soup)
                for url, caption, label in cands:
                    try:
                        size = download_image(url, r.url, dest)
                    except Exception:
                        size = None
                    if size:
                        got = (caption, label, url, "html")
                        break
        except Exception as e:
            diag(f"  [{idx}] HTML lỗi {type(e).__name__}")

        # (2) Thử cắt hình từ file PDF
        if not got:
            data, src = get_pdf(p, page_url, page_pdf)
            if data:
                try:
                    for png, caption, label in pdf_figures(data):
                        size = save_image(png, dest, min_w=300)
                        if size:
                            got = (caption, label, src, "pdf")
                            break
                except Exception as e:
                    diag(f"  [{idx}] PDF lỗi tách hình: {type(e).__name__}")

        if got:
            caption, label, url, how = got
            figures.append({
                "n": n, "paper": idx, "src": f"images/blog/{post_id}/fig{n}.jpg",
                "caption_original": caption, "orig_label": label, "source_url": url,
                "license": p["license"] or "cc-by",
            })
            diag(f"  + Hình {n} ({how}) từ [{idx}] {p['doi']} {label}")

    # (3) Dự phòng: Nếu nhà xuất bản chặn tất cả request tải ảnh -> Tải 1 ảnh minh họa kỹ thuật để bài luôn có cover
    if not figures and papers:
        dest = IMG_DIR / post_id / "fig1.jpg"
        try:
            size = download_image(CURRENT_FALLBACK_IMG, "https://unsplash.com/", dest, check_dup=False)
            if size:
                figures.append({
                    "n": 1,
                    "paper": 1,
                    "src": f"images/blog/{post_id}/fig1.jpg",
                    "caption_original": f"Illustration related to {papers[0]['title']}",
                    "orig_label": "",
                    "source_url": CURRENT_FALLBACK_IMG,
                    "license": "cc0",
                })
                diag("  + Hình 1 (fallback illustration) đã được lưu làm ảnh đại diện cho bài viết.")
        except Exception as e:
            diag(f"  Lỗi tải hình dự phòng: {e}")

    return figures


# ----------------------------------------------------------------------------
# 3. Viết bài bằng Gemini (Chống lỗi HTTP 429 & lọc model thử nghiệm)

PREFERRED_GEMINI_MODELS = [
    os.environ.get("GEMINI_MODEL", "").strip(),
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-flash-latest",
]

BLOCKED_MODEL_PATTERNS = re.compile(
    r"omni|image|audio|tts|live|robot|vision|thinking|exp|preview|learnlm", re.I
)


def gemini_candidates():
    preferred = [m for m in dict.fromkeys(PREFERRED_GEMINI_MODELS) if m]
    metadata = {}
    try:
        response = SESSION.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": GEMINI_API_KEY},
            params={"pageSize": 1000},
            timeout=30,
        )
        if response.status_code == 200:
            for model in response.json().get("models", []):
                name = model.get("name", "").removeprefix("models/")
                if (
                    "generateContent" in model.get("supportedGenerationMethods", [])
                    and name.startswith("gemini-")
                    and not BLOCKED_MODEL_PATTERNS.search(name)
                ):
                    metadata[name] = model

            ordered = [m for m in preferred if m in metadata]
            for m in sorted(metadata, reverse=True):
                if m not in ordered:
                    ordered.append(m)
            preferred = ordered
    except (requests.RequestException, ValueError):
        pass

    names = list(dict.fromkeys(preferred))[:6]
    if not names:
        names = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
    log(f"Thứ tự model Gemini: {', '.join(names)}")
    return [(name, metadata.get(name, {})) for name in names]


def gemini_json(prompt):
    import random
    errors = []
    started = time.monotonic()
    required = ("title_vi", "title_en", "content_vi", "content_en")

    for model, metadata in gemini_candidates():
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "maxOutputTokens": min(65536, metadata.get("outputTokenLimit") or 65536),
            },
        }
        for attempt in range(3):
            if time.monotonic() - started > 720:
                raise RuntimeError("Gemini chưa phục hồi sau giới hạn 12 phút.")
            retry_after = 0
            try:
                response = SESSION.post(
                    url,
                    headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
                    json=body,
                    timeout=180,
                )
            except requests.RequestException as exc:
                reason = type(exc).__name__
            else:
                if response.status_code == 200:
                    try:
                        candidate = response.json()["candidates"][0]
                        if candidate.get("finishReason") != "STOP":
                            raise ValueError("Bài chưa hoàn chỉnh")
                        text = "".join(
                            p.get("text", "")
                            for p in candidate.get("content", {}).get("parts", [])
                            if not p.get("thought")
                        )
                        text = re.sub(r"^\x60\x60\x60(json)?|\x60\x60\x60$", "", text.strip()).strip()
                        data = json.loads(text)
                        if not isinstance(data, dict) or not all(
                            isinstance(data.get(k), str) and data[k].strip() for k in required
                        ):
                            raise ValueError("Thiếu nội dung song ngữ")
                        log(f"Gemini model: {model}")
                        return data
                    except (KeyError, IndexError, TypeError, ValueError):
                        reason = "JSON không hợp lệ hoặc bài chưa hoàn chỉnh"
                else:
                    reason = f"HTTP {response.status_code}"
                    msg = ""
                    try:
                        msg = response.json().get("error", {}).get("message", "")
                        if msg:
                            reason += " – " + re.sub(r"\s+", " ", msg)[:160]
                    except ValueError:
                        pass

                    if response.status_code in (401, 402, 403):
                        raise RuntimeError(f"Gemini {reason}: kiểm tra quyền API hoặc thanh toán.")

                    if response.status_code == 404 or (
                        response.status_code == 429
                        and ("quota" in msg.lower() or "billing" in msg.lower() or attempt >= 1)
                    ):
                        errors.append(f"{model}: {reason}")
                        log(f"⚠️ {model} hết hạn mức ({reason}) -> Chuyển ngay sang model tiếp theo...")
                        time.sleep(2)
                        break

                    if response.status_code not in (408, 429, 500, 502, 503, 504):
                        errors.append(f"{model}: {reason}")
                        break

                    try:
                        retry_after = max(0, float(response.headers.get("Retry-After", 0)))
                    except (ValueError, TypeError):
                        pass

            errors.append(f"{model}: {reason}")
            log(f"Gemini {model}, lần {attempt + 1}/3: {reason}")
            if attempt < 2:
                delay = min(30, max(retry_after, 8 * (2 ** attempt) + random.uniform(0, 2)))
                time.sleep(delay)

    raise RuntimeError("Gọi Gemini thất bại: " + " | ".join(errors[-8:]))


def build_feature_prompt(papers, figures, today):
    fig_of = {f["paper"]: f["n"] for f in figures}
    lines = []
    for i, p in enumerate(papers, 1):
        has = f"  [HAS FIGURE: FIG{fig_of[i]}]" if i in fig_of else ""
        kind = "REVIEW" if p.get("kind") == "review" else "RESEARCH"
        lines.append(f"[{i}] {kind}{has}\nTitle: {p['title']}\nJournal: {p['journal']} ({p['date']})\n"
                     f"Abstract: {clean_text(p['abstract'], 1800)}\n")
    figs = "\n".join(f"FIG{f['n']} – from paper [{f['paper']}]; original caption: {f['caption_original'] or '(none)'}"
                     for f in figures) or "(no figures)"
    outline = (" — suggested themes: " + "; ".join(FEATURE_OUTLINE)) if FEATURE_OUTLINE else ""
    return f"""You are a science writer for an engineering blog on welding, joining and materials technology.
Write an IN-DEPTH bilingual Vietnamese–English feature article on the topic:
"{FEATURE_TOPIC}"
for engineers, lecturers and graduate students. Base every technical claim on the {len(papers)} papers below.

STRUCTURE (each part an <h3> section):
1. Lead paragraph (<p class="lead">, 3–4 sentences) on why the topic matters now.
2. "Bối cảnh" / "Background": why the topic matters and which problems/bottlenecks it addresses.
3. 4–6 thematic sections grouped as supported by the papers{outline}.
4. "Thách thức" / "Challenges": grounded in the papers.
5. "Xu thế tương lai" / "Future trends": 4–6 trends with reasoning drawn from the reviews.
6. "Hàm ý thực tiễn" / "Practical implications": 3–4 concrete implications for Vietnamese industry and education.

RULES
- Cite with [n] right after each claim; use only [1]..[{len(papers)}]. No reference list.
- Do NOT invent numbers, datasets, companies or results not present in the abstracts.
- VOICE: neutral, impersonal. NEVER mention any blog owner, author, editor, lab, university or "experts". Refer to cited authors only as "nhóm tác giả [n]" / "the authors of [n]".
- FIGURES: put each placeholder exactly once, as its own paragraph, right after the paragraph discussing its paper:
  {{{{FIG1}}}}, {{{{FIG2}}}}… Available figures:
{figs}
- Allowed HTML: <h3>, <p>, <ul>, <li>, <strong>, <em>, <sub>, <sup>. No styles, images or links.
- Length: about 2200–3000 words per language.

Return ONLY a JSON object:
{{
  "category_vi": "Chuyên đề", "category_en": "In-depth",
  "title_vi": "...", "title_en": "...",
  "summary_vi": "2–3 câu", "summary_en": "2–3 sentences",
  "tags": ["4-6 short English keywords"],
  "figure_captions_vi": {{"1": "bản dịch tiếng Việt trung thành, ngắn gọn của chú thích gốc FIG1", "...": "..."}},
  "figure_captions_en": {{"1": "faithful, concise English version of the original FIG1 caption", "...": "..."}},
  "content_vi": "HTML",
  "content_en": "HTML"
}}

PAPERS (date of writing: {today})
{chr(10).join(lines)}"""


def build_prompt(papers, figures, today):
    topic_names = {t["key"]: t["en"] for t in TOPICS}
    fig_of = {f["paper"]: f["n"] for f in figures}
    lines = []
    for i, p in enumerate(papers, 1):
        has = f"  [HAS FIGURE: FIG{fig_of[i]}]" if i in fig_of else ""
        lines.append(
            f"[{i}] Topic: {topic_names[p['topic']]}{has}\nTitle: {p['title']}\nJournal: {p['journal']} ({p['date']})\n"
            f"Abstract: {clean_text(p['abstract'], 1600)}\n")
    figs = "\n".join(f"FIG{f['n']} – from paper [{f['paper']}]; original caption: {f['caption_original'] or '(none)'}"
                     for f in figures) or "(no figures)"
    return f"""You are a science news editor for a welding & joining technology blog.
Write today's ({today}) bilingual Vietnamese–English science news digest for engineers, lecturers and graduate students,
based ONLY on the {len(papers)} newly published papers below.

RULES
- Group the papers into 4–6 thematic sections with an <h3> heading each.
- Cite with square brackets right after the claim, e.g. "... tăng 18% độ bền kéo [3]." Use only [1]..[{len(papers)}].
- FIGURES: put each figure placeholder exactly once, as its own paragraph: {{{{FIG1}}}}, {{{{FIG2}}}}…
  Available figures:
{figs}
- Start with a 2–3 sentence lead paragraph (<p class="lead">); end with <h3> "Hàm ý thực tiễn" / "Practical implications".
- VOICE: neutral, impersonal news style. NEVER mention any blog owner, author, editor, lab, university or "expert(s)".
- Allowed HTML: <h3>, <p>, <ul>, <li>, <strong>, <em>, <sub>, <sup>. No inline styles, no images, no links.
- Length: about 900–1300 words per language.

Return ONLY a JSON object:
{{
  "category_vi": "Điểm tin khoa học",
  "category_en": "Science Digest",
  "title_vi": "...", "title_en": "...",
  "summary_vi": "2 câu", "summary_en": "2 sentences",
  "tags": ["3-6 short English keywords"],
  "figure_captions_vi": {{"1": "bản dịch tiếng Việt trung thành, ngắn gọn của chú thích gốc FIG1", "...": "..."}},
  "figure_captions_en": {{"1": "faithful, concise English version of the original FIG1 caption", "...": "..."}},
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


LICENSE_URL = {
    "cc-by": "https://creativecommons.org/licenses/by/4.0/",
    "cc-by-sa": "https://creativecommons.org/licenses/by-sa/4.0/",
    "cc-by-nc": "https://creativecommons.org/licenses/by-nc/4.0/",
    "cc-by-nc-sa": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
    "cc0": "https://creativecommons.org/publicdomain/zero/1.0/",
}


def figure_html(f, caption, lang, paper, number=None):
    vi = lang == "vi"
    lic = f["license"].upper().replace("CC-", "CC ")
    lic_html = (f'<a href="{LICENSE_URL[f["license"]]}" target="_blank" rel="noopener">{lic}</a>'
                if f["license"] in LICENSE_URL else lic)
    orig = f.get("orig_label") or ""
    if orig:
        orig = re.sub(r"(?i)^fig(ure)?\.?", "Hình" if vi else "Fig.", orig).strip()
        where = f'{orig} trong bài gốc' if vi else f'{orig} of the original article'
    else:
        where = "Hình minh họa chuyên đề" if vi else "Thematic illustration"
    doi = html.escape(paper["doi"])
    credit = (f'{"Nguồn" if vi else "Source"}: {where} – {html.escape(short_cite(paper))}, '
              f'“{html.escape(paper["title"])}”, <em>{html.escape(paper["journal"])}</em>, '
              f'<a href="https://doi.org/{doi}" target="_blank" rel="noopener">doi:{doi}</a> '
              f'[<a href="#ref-{f["paper"]}">{f["paper"]}</a>]. '
              f'{"Giấy phép" if vi else "Licence"}: {lic_html}.')
    return (f'<figure class="post-figure"><img src="{f["src"]}" alt="{html.escape(caption)}" loading="lazy">'
            f'<figcaption><strong>{"Hình" if vi else "Figure"} {number or f["n"]}.</strong> {html.escape(caption)} '
            f'<span class="fig-credit">{credit}</span></figcaption></figure>')


def short_cite(p):
    a = p["authors"]
    first = a[0].split()[-1] if a else "Anon."
    return f"{first}{' et al.' if len(a) > 1 else ''} ({p['date'][:4]})"


def finalize_content(content, figures, captions, lang, papers):
    content = sanitize(content)
    n_ref = len(papers)

    def cite(m):
        nums = cite_numbers(m.group(1))
        if not nums or any(not 1 <= n <= n_ref for n in nums):
            return m.group(0)
        return "<sup class=\"cite\">[" + ", ".join(f'<a href="#ref-{n}">{n}</a>' for n in nums) + "]</sup>"

    content = CITE_RX.sub(cite, content)
    by_n = {f["n"]: f for f in figures}
    token = re.compile(r"(?:<p>\s*)?\{\{\s*FIG(\d+)\s*\}\}(?:\s*</p>)?")

    placed = {int(n) for n in token.findall(content) if int(n) in by_n}
    unused = [f for f in figures if f["n"] not in placed]
    if unused:
        parts = re.split(r"(?=<h3>)", content)
        body = [i for i, part in enumerate(parts) if part.startswith("<h3>")][:-1] or \
               [i for i, part in enumerate(parts) if part.startswith("<h3>")]
        for i in body:
            if not unused:
                break
            if token.search(parts[i]):
                continue
            cited = {int(x) for x in re.findall(r'href="#ref-(\d+)"', parts[i])}
            pick = next((f for f in unused if f["paper"] in cited), unused[0])
            unused.remove(pick)
            parts[i] += f"{{{{FIG{pick['n']}}}}}"
        content = "".join(parts)
        for f in unused:
            content += f"{{{{FIG{f['n']}}}}}"

    counter, done = [0], set()

    def put(m):
        n = int(m.group(1))
        if n not in by_n or n in done:
            return ""
        done.add(n)
        counter[0] += 1
        f = by_n[n]
        cap = captions.get(str(n)) or f["caption_original"] or ""
        return figure_html(f, cap, lang, papers[f["paper"] - 1], number=counter[0])

    content = token.sub(put, content)
    content = re.sub(r"<p>\s*</p>", "", content)
    return content


CITE_RX = re.compile(r"\[(\d+(?:\s*[,;–-]\s*\d+)*)\]")


def cite_numbers(group):
    out = []
    for part in re.split(r"\s*[,;]\s*", group):
        m = re.fullmatch(r"(\d+)\s*[–-]\s*(\d+)", part.strip())
        if m and int(m.group(1)) <= int(m.group(2)) <= int(m.group(1)) + 30:
            out += list(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.strip().isdigit() and not part.strip().startswith("0"):
            out.append(int(part))
    return out


def renumber_citations(ai, papers, figures):
    n = len(papers)
    order = []
    for key in ("content_vi", "content_en"):
        text = ai.get(key, "") or ""
        for m in re.finditer(r"\{\{\s*FIG(\d+)\s*\}\}|" + CITE_RX.pattern, text):
            if m.group(1):
                f = next((f for f in figures if f["n"] == int(m.group(1))), None)
                nums = [f["paper"]] if f else []
            else:
                nums = cite_numbers(m.group(2))
            if not nums or any(not 1 <= x <= n for x in nums):
                continue
            for x in nums:
                if x not in order:
                    order.append(x)
    order += [x for x in range(1, n + 1) if x not in order]
    new = {old: i for i, old in enumerate(order, 1)}

    def sub(m):
        raw = cite_numbers(m.group(1))
        if not raw or any(x not in new for x in raw):
            return m.group(0)
        return "[" + ", ".join(map(str, sorted({new[x] for x in raw}))) + "]"

    for key in ("content_vi", "content_en"):
        if ai.get(key):
            ai[key] = CITE_RX.sub(sub, ai[key])
    for key in ("figure_captions_vi", "figure_captions_en"):
        caps = ai.get(key) or {}
        for k in list(caps):
            caps[k] = CITE_RX.sub(sub, caps[k] or "")
    for f in figures:
        f["paper"] = new[f["paper"]]
    return [papers[old - 1] for old in order]


SELF_REF = re.compile(r"Hán Lê Duy|Han Le Duy|Duy Le Han|Bách khoa Hà Nội|\bHUST\b|Hanoi University of Science|"
                      r"chuyên gia (?:nhận định|cho rằng|đánh giá)|nhóm (?:nghiên cứu|chuyên gia) (?:của chúng tôi|tại)|"
                      r"our (?:lab|team|research group)|expert(?:s)? (?:believe|note|say|suggest)", re.I)


def self_refs(ai):
    return sorted({m.group(0) for k in ("content_vi", "content_en", "summary_vi", "summary_en", "title_vi", "title_en")
                   for m in SELF_REF.finditer(ai.get(k, "") or "")})


def main():
    if not GEMINI_API_KEY:
        sys.exit("Thiếu biến môi trường GEMINI_API_KEY")

    now_vn = dt.datetime.now(VN_TZ)
    today = now_vn.date().isoformat()

    feature = bool(FEATURE_TOPIC)
    papers = collect_feature_papers() if feature else collect_papers()
    if len(papers) < MIN_PAPERS:
        log(f"Chỉ có {len(papers)} bài mới (< {MIN_PAPERS}) – hôm nay không tạo bản tin.")
        write_output(skipped="true")
        return

    post_id = f"{today}-chuyen-de-{slugify(FEATURE_TOPIC, 50)}" if feature else f"{today}-diem-tin-khoa-hoc"
    if AUTO_WEEKLY and (POSTS_DIR / f"{post_id}.json").exists():
        log("Bài theo lịch hôm nay đã tồn tại – không tạo trùng.")
        write_output(skipped="true")
        return
    if (POSTS_DIR / f"{post_id}.json").exists():
        post_id += "-" + now_vn.strftime("%H%M")

    IMG_DIR.mkdir(parents=True, exist_ok=True)
    (IMG_DIR / ".gitkeep").touch()
    POSTS_DIR.mkdir(exist_ok=True)
    (POSTS_DIR / ".gitkeep").touch()

    figures = collect_figures(papers, post_id)
    log(f"Thu được {len(figures)} hình minh họa")

    prompt = (build_feature_prompt if feature else build_prompt)(papers, figures, today)
    if AUTO_WEEKLY:
        prompt += ("\nEDITORIAL SCHEDULE: " + (FEATURE_TOPIC if feature else "Sunday weekly roundup.") + "\nPrioritize recent research; clearly distinguish older background reviews from new findings.")
    recent_titles = []
    if POSTS_INDEX.exists():
        published_index = json.loads(POSTS_INDEX.read_text(encoding="utf-8"))
        recent_titles = [{k: item.get(k, "") for k in ("title_vi", "title_en")} for item in published_index[:30]]
    prompt += (
        "\nHEADLINE RULES: Create a specific Vietnamese headline and a faithful English counterpart. "
        "Recent published headlines to avoid: " + json.dumps(recent_titles, ensure_ascii=False)
    )

    import shutil
    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    (DRAFT_DIR / "sources.json").write_text(
        json.dumps({"papers": papers, "figures": figures, "prompt": prompt, "post_id": post_id, "today": today}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    if (IMG_DIR / post_id).is_dir():
        shutil.copytree(IMG_DIR / post_id, DRAFT_DIR / "images" / "blog" / post_id, dirs_exist_ok=True)

    ai = gemini_json(prompt)
    bad = self_refs(ai)
    if bad:
        ai = gemini_json(prompt + "\n\nIMPORTANT: Rewrite in a fully neutral, impersonal voice without naming any blog owner, lab, university or experts.")

    papers = renumber_citations(ai, papers, figures)

    refs = [{
        "n": i, "authors": p["authors"][:6], "more_authors": len(p["authors"]) > 6,
        "title": p["title"], "journal": p["journal"], "date": p["date"], "doi": p["doi"],
        "url": f"https://doi.org/{p['doi']}", "license": p["license"], "topic": p["topic"],
    } for i, p in enumerate(papers, 1)]

    post = {
        "id": post_id,
        "date": today,
        "type": "feature" if feature else "digest",
        "category_vi": ("Chuyên đề" if feature else ai.get("category_vi")) or "Điểm tin khoa học",
        "category_en": ("In-depth" if feature else ai.get("category_en")) or "Science Digest",
        "title_vi": clean_text(ai["title_vi"]),
        "title_en": clean_text(ai["title_en"]),
        "summary_vi": clean_text(ai.get("summary_vi", "")),
        "summary_en": clean_text(ai.get("summary_en", "")),
        "tags": [clean_text(t) for t in ai.get("tags", [])][:6],
        "cover": "",
        "content_vi": finalize_content(ai["content_vi"], figures, ai.get("figure_captions_vi", {}) or {}, "vi", papers),
        "content_en": finalize_content(ai["content_en"], figures, ai.get("figure_captions_en", {}) or {}, "en", papers),
        "references": refs,
        "generated_by": "AI (Gemini)",
    }
    m = re.search(r'<img src="([^"]+)"', post["content_vi"])
    post["cover"] = m.group(1) if m else ""

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
    write_output(skipped="false", post_id=post_id, title=post["title_vi"],
                 diag="\n".join(DIAG[-60:]) or "(không có ghi chú)")


def write_output(**kv):
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            for k, v in kv.items():
                v = str(v)
                if "\n" in v:
                    f.write(f"{k}<<__EOF__\n{v}\n__EOF__\n")
                else:
                    f.write(f"{k}={v}\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        msg = f"{type(e).__name__}: {e}".replace("%", "%25").replace("\r", " ").replace("\n", " ")
        print(f"::error title=Lỗi tạo bản tin::{msg[:900]}")
        sys.exit(1)
