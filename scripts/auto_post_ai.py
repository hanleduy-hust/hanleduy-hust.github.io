import os
import json
import datetime
import urllib.parse
import urllib.request
import google.generativeai as genai

# 1. Cấu hình Gemini API
API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("Thiếu biến môi trường GEMINI_API_KEY")

genai.configure(api_key=API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# 2. Yêu cầu AI sáng tạo bài viết học thuật chuyên sâu về ngành Hàn & Vật liệu
prompt = """
Bạn là TS. Hán Lê Duy, chuyên gia về Công nghệ Hàn & Ghép nối, Vi điện tử và Vật liệu tại Đại học Bách khoa Hà Nội (HUST).
Hãy viết 01 bài chia sẻ học thuật/công nghệ hấp dẫn, chuyên nghiệp cho mục Blog Góc Chuyên gia.

Chủ đề xoay quanh một trong các hướng sau:
- Công nghệ hàn tiên tiến (FSW, GTAW tấm mỏng, PAW, hàn laser, robot hàn, WAAM, DED, Soldering, electronic packaging...)
- Đóng gói vi điện tử & vật liệu vảy hàn không chì (In-Sn, SAC, thiêu kết đồng nano...)
- Đảm bảo chất lượng hàn theo tiêu chuẩn quốc tế ISO 3834, AWS, IIW...
- Xử lý nhiệt, luyện kim mối hàn thép chịu nhiệt P91, công nghệ hàn ống áp lực...

Yêu cầu trả về DUY NHẤT một chuỗi JSON hợp lệ (không kèm markdown ```json bọc ngoài), có cấu trúc:
{
  "category_vi": "Tên danh mục tiếng Việt (ví dụ: Công nghệ Hàn, Đóng gói Vi điện tử, Tiêu chuẩn Quốc tế)",
  "category_en": "Category in English",
  "title_vi": "Tiêu đề bài viết tiếng Việt",
  "title_en": "Article Title in English",
  "summary_vi": "Tóm tắt ngắn gọn 2 câu tiếng Việt",
  "summary_en": "Short summary in 2 sentences in English",
  "image_keyword": "Từ khóa tiếng Anh mô tả ảnh (ví dụ: welding, robotics, microchip, metallurgy)",
  "content_vi": "Nội dung bài viết chi tiết bằng mã HTML (dùng các thẻ <p>, <h4>, <ul>, <li>, <strong>)",
  "content_en": "Detailed article content in HTML (using <p>, <h4>, <ul>, <li>, <strong>)"
}
"""

print("Đang yêu cầu Gemini tạo nội dung bài viết...")
response = model.generate_content(prompt)
res_text = response.text.strip()
if res_text.startswith("```json"):
    res_text = res_text[7:]
if res_text.endswith("```"):
    res_text = res_text[:-3]
post_data = json.loads(res_text.strip())

# 3. Tạo ngày tháng và ID bài viết
today_str = datetime.date.today().strftime("%Y-%m-%d")
post_id = f"post-{today_str}-{int(datetime.datetime.now().timestamp())}"

# 4. Tự động lấy ảnh chất lượng cao bản quyền mở theo chủ đề
keyword = post_data.get("image_keyword", "welding")
img_url = f"[https://source.unsplash.com/featured/800x450/](https://source.unsplash.com/featured/800x450/)?{urllib.parse.quote(keyword)}"

# Chèn ảnh vào đầu bài viết HTML
img_tag = f'<div class="mb-4"><img src="{img_url}" alt="{post_data.get("title_en")}" class="rounded-xl w-full h-64 object-cover shadow-sm mb-2"><p class="text-xs text-slate-400 italic text-center">Ảnh minh họa chủ đề: {keyword}</p></div>'

post_data["content_vi"] = img_tag + post_data["content_vi"]
post_data["content_en"] = img_tag + post_data["content_en"]

# 5. Cập nhật vào posts.json
POSTS_FILE = "posts.json"
posts = []
if os.path.exists(POSTS_FILE):
    try:
        with open(POSTS_FILE, "r", encoding="utf-8") as f:
            posts = json.load(f)
    except Exception:
        posts = []

new_entry = {
    "id": post_id,
    "date": today_str,
    "category_vi": post_data.get("category_vi", "Khoa học Công nghệ"),
    "category_en": post_data.get("category_en", "Technology"),
    "title_vi": post_data.get("title_vi"),
    "title_en": post_data.get("title_en"),
    "summary_vi": post_data.get("summary_vi"),
    "summary_en": post_data.get("summary_en"),
    "content_vi": post_data.get("content_vi"),
    "content_en": post_data.get("content_en")
}

posts.insert(0, new_entry)

with open(POSTS_FILE, "w", encoding="utf-8") as f:
    json.dump(posts, f, ensure_ascii=False, indent=2)

print(f"✅ Đã tạo thành công bài viết: {post_data.get('title_vi')}")
