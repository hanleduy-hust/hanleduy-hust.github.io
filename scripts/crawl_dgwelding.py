import os
import json
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

URL = "https://dgwelding.vn/blogs/han-1-1-lich-su-phat-trien-han-nong-chay-fusion-welding/lich-su-phat-trien-cong-nghe-han-va-ky-thuat-han"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# 1. Thư mục lưu ảnh bài viết
IMG_DIR = "images/posts/lich-su-phat-trien-han"
os.makedirs(IMG_DIR, exist_ok=True)

print("Đang tải dữ liệu bài viết...")
res = requests.get(URL, headers=HEADERS)
res.encoding = "utf-8"
soup = BeautifulSoup(res.text, "html.parser")

# 2. Lấy tiêu đề bài viết
title_elem = soup.find("h1")
title = title_elem.get_text(strip=True) if title_elem else "Lịch sử phát triển công nghệ hàn và kỹ thuật hàn"

# 3. Tìm vùng chứa nội dung chính
# Các trang blog Haravan/Shopify thường để class: content-detail, article-content, post-content, hoặc blog-content
content_elem = (
    soup.find("div", class_="content-detail") or 
    soup.find("div", class_="article-content") or 
    soup.find("div", class_="post-content") or
    soup.find("article")
)

if not content_elem:
    print("Không tìm thấy vùng nội dung tự động, vui lòng kiểm tra lại class của trang nguồn!")
    exit()

# 4. Tải tất cả ảnh và thay đổi src sang đường dẫn nội bộ
images = content_elem.find_all("img")
print(f"Tìm thấy {len(images)} hình ảnh trong bài. Đang tải...")

for idx, img in enumerate(images, start=1):
    src = img.get("src") or img.get("data-src")
    if not src:
        continue
    img_url = urljoin(URL, src)
    
    # Lấy đuôi mở rộng (.jpg, .png, ...)
    ext = os.path.splitext(img_url.split("?")[0])[1]
    if not ext or len(ext) > 5:
        ext = ".jpg"
        
    local_img_name = f"image_{idx}{ext}"
    local_img_path = os.path.join(IMG_DIR, local_img_name)
    
    # Tải file ảnh
    try:
        r_img = requests.get(img_url, headers=HEADERS, timeout=15)
        if r_img.status_code == 200:
            with open(local_img_path, "wb") as f:
                f.write(r_img.content)
            # Cập nhật lại đường dẫn ảnh trong thẻ HTML
            img["src"] = f"{IMG_DIR}/{local_img_name}"
            if img.get("data-src"):
                del img["data-src"]
    except Exception as e:
        print(f"Lỗi khi tải ảnh {img_url}: {e}")

# 5. Lấy chuỗi HTML nội dung đã chuẩn hóa
content_html = str(content_elem)

# Thêm nguồn trích dẫn ở cuối bài
source_credit = f'<p><em>Nguồn tham khảo: <a href="{URL}" target="_blank" rel="noopener noreferrer">DGwelding</a></em></p>'
content_html += source_credit

# 6. Cập nhật vào posts.json
POSTS_FILE = "posts.json"
posts = []
if os.path.exists(POSTS_FILE):
    with open(POSTS_FILE, "r", encoding="utf-8") as f:
        try:
            posts = json.load(f)
        except Exception:
            posts = []

new_post = {
    "id": "lich-su-phat-trien-cong-nghe-han",
    "title": title,
    "date": "2026-10-16",
    "summary": "Tổng quan lịch sử hình thành và phát triển của công nghệ hàn từ thời cổ đại đến kỹ thuật hàn hồ quang nóng chảy hiện đại.",
    "thumbnail": f"{IMG_DIR}/image_1.jpg" if os.path.exists(f"{IMG_DIR}/image_1.jpg") else "",
    "content": content_html,
    "source": URL
}

# Đưa bài mới lên đầu danh sách
posts.insert(0, new_post)

with open(POSTS_FILE, "w", encoding="utf-8") as f:
    json.dump(posts, f, ensure_ascii=False, indent=2)

print("Hoàn tất! Đã lưu ảnh vào thư mục và cập nhật bài viết vào posts.json.")
