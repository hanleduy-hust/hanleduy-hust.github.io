import os
import json
import datetime
import requests

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("Thiếu biến môi trường GEMINI_API_KEY")

prompt_text = """
Bạn là TS. Hán Lê Duy, chuyên gia về Công nghệ Hàn & Ghép nối, Vi điện tử tại Trường Cơ khí - ĐHBK Hà Nội (HUST).
Hãy viết 01 bài chia sẻ học thuật/công nghệ chuyên sâu cho mục Góc Chuyên gia (Blog).

Chủ đề xoay quanh:
- Công nghệ hàn tiên tiến (FSW, GTAW tấm mỏng, PAW, robot hàn)
- Đóng gói vi điện tử & vật liệu vảy hàn không chì (In-Sn, SAC, đồng xốp nano)
- Đảm bảo chất lượng mối hàn theo ISO 3834, AWS, IIW
- Xử lý nhiệt và luyện kim hàn thép chịu nhiệt P91

Yêu cầu trả về DUY NHẤT một chuỗi JSON hợp lệ (không kèm markdown ```json), đúng cấu trúc:
{
  "category_vi": "Công nghệ Hàn",
  "category_en": "Welding Technology",
  "title_vi": "Tiêu đề tiếng Việt",
  "title_en": "Tiêu đề tiếng Anh",
  "summary_vi": "Tóm tắt 2 câu tiếng Việt",
  "summary_en": "Tóm tắt 2 câu tiếng Anh",
  "content_vi": "Nội dung bài viết chi tiết định dạng HTML có thẻ <p>, <h4>, <ul>, <li>",
  "content_en": "Detailed article content in HTML format"
}
"""

url = f"[https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key=](https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key=){API_KEY}"
headers = {"Content-Type": "application/json"}
payload = {
    "contents": [{"parts": [{"text": prompt_text}]}]
}

print("Đang gửi yêu cầu trực tiếp tới Gemini API...")
res = requests.post(url, headers=headers, json=payload, timeout=60)

if res.status_code != 200:
    # Nếu bản latest không hỗ trợ, thử fallback sang gemini-pro
    print(f"Thử lại với model gem
