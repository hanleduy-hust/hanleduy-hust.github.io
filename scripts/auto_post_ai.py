import os
import json
import datetime
import google.generativeai as genai

# 1. Kiểm tra API Key
API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("Lỗi: Thiếu biến môi trường GEMINI_API_KEY!")

genai.configure(api_key=API_KEY)

# 2. Tự động tìm và chọn model hỗ trợ sinh nội dung
available_models = []
try:
    for m in genai.list_models():
        if "generateContent" in m.supported_generation_methods:
            available_models.append(m.name)
except Exception as e:
    print(f"Cảnh báo khi dò tìm model: {e}")

# Danh sách các model ưu tiên
chosen_model = None
priorities = [
    "models/gemini-2.5-flash",
    "models/gemini-1.5-flash-latest",
    "models/gemini-1.5-flash",
    "models/gemini-pro",
    "gemini-1.5-flash",
    "gemini-pro"
]

for p in priorities:
    if p in available_models:
        chosen_model = p
        break

if not chosen_model:
    chosen_model = available_models[0] if available_models else "gemini-pro"

print(f"Đang sử dụng model: {chosen_model}")
model = genai.GenerativeModel(chosen_model)

# 3. Prompt yêu cầu AI viết bài
prompt = """
Bạn là TS. Hán Lê Duy, chuyên gia về Công nghệ Hàn & Ghép nối, Đóng gói Vi điện tử tại Trường Cơ khí - Đại học Bách khoa Hà Nội (HUST).
Hãy viết 01 bài chia sẻ học thuật/công nghệ chuyên sâu, hấp dẫn cho mục "Góc Chuyên gia" (Blog).

Chủ đề xoay quanh một trong các hướng sau:
- Công nghệ hàn tiên tiến (FSW, GTAW tấm mỏng, PAW, hàn laser, robot hàn)
- Đóng gói vi điện tử & vật liệu vảy hàn không chì (hệ In-Sn, SAC, nanoporous Cu bonding)
- Tiêu chuẩn và bảo đảm chất lượng mối hàn (ISO 3834, AWS, IIW)
- Xử lý nhiệt và luyện kim hàn thép chịu nhiệt P91, công nghệ hàn ống áp lực

Yêu cầu định dạng: Trả về DUY NHẤT một chuỗi JSON hợp lệ (không kèm markdown ```json bọc ngoài), có cấu trúc:
{
  "category_vi": "Tên danh mục tiếng Việt (ví dụ: Công nghệ Hàn, Đóng gói Vi điện tử, Tiêu chuẩn Quốc tế)",
  "category_en": "Tên danh mục tiếng Anh",
  "title_vi": "Tiêu đề tiếng Việt",
  "title_en": "Tiêu đề tiếng Anh",
  "summary_vi": "Tóm tắt 2 câu tiếng Việt",
  "summary_en": "Tóm tắt 2 câu tiếng Anh",
  "content_vi": "Nội dung bài viết chi tiết bằng các thẻ HTML như <p>, <h4>, <ul>, <li>, <strong>",
  "content_en": "Nội dung bài viết chi tiết bằng tiếng Anh định dạng HTML"
}
"""

print("Đang gửi yêu cầu tới AI...")
response = model.generate_content(prompt)
res_text = response.text.strip()

# Chuẩn hóa nếu AI trả về bọc trong markdown ```json
if res_text.startswith("```json"):
    res_text = res_text[7:]
elif res_text.startswith("```"):
    res_text = res_text[3:]
if res_text.endswith("```"):
    res_text = res_text[:-3]

post_data = json.loads(res_text.strip())

# 4. Thêm ảnh minh họa kỹ thuật chất lượng cao
image_url = "[https://images.unsplash.com/photo-1504917599217-d4dc5ebe6122?auto=format&fit=crop&w=800&q=80](https://images.unsplash.com/photo-1504917599217-d4dc5ebe6122?auto=format&fit=crop&w=800&q=80)"
image_tag = f'<div class="mb-4"><img src="{image_url}" alt="Welding Technology" class="rounded-xl w-full h-64 object-cover shadow-sm mb-2"></div>'

post_data["content_vi"] = image_tag + post_data.get("content_vi", "")
post_data["content_en"] = image_tag + post_data.get("content_en", "")

# 5. Cập nhật vào posts.json
POSTS_FILE = "posts.json"
posts = []
if os.path.exists(POSTS_FILE):
    try:
        with open(POSTS_FILE, "r", encoding="utf-8") as f:
            posts = json.load(f)
    except Exception:
        posts = []

today_str = datetime.date.today().strftime("%Y-%m-%d")
post_id = f"post-{today_str}-{int(datetime.datetime.now().timestamp())}"

new_entry = {
    "id": post_id,
    "date": today_str,
    "category_vi": post_data.get("category_vi", "Công nghệ Hàn"),
    "category_en": post_data.get("category_en", "Welding Technology"),
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

print(f"✅ Hoàn tất! Đã tạo bài viết: {post_data.get('title_vi')}")
