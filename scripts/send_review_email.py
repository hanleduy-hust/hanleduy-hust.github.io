"""
Gửi email bản nháp bài blog để duyệt (Gmail SMTP).

Biến môi trường:
  MAIL_USERNAME      địa chỉ Gmail dùng để gửi (ví dụ hanleduy@gmail.com)
  MAIL_APP_PASSWORD  App Password 16 ký tự của Gmail (không phải mật khẩu đăng nhập)
  MAIL_TO            người duyệt (mặc định = MAIL_USERNAME); nhiều địa chỉ cách nhau bằng dấu phẩy
  PR_URL             link Pull Request bản nháp
  DRAFT_DIR          thư mục chứa post.json (mặc định .draft)
"""

import html
import json
import os
import re
import smtplib
import ssl
import sys
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRAFT_DIR = Path(os.environ.get("DRAFT_DIR", ROOT / ".draft"))
SITE = "https://hanleduy-hust.github.io"


def refs_html(refs):
    items = []
    for r in refs:
        authors = ", ".join(r["authors"]) + (" et al." if r.get("more_authors") else "")
        items.append(
            f'<li id="ref-{r["n"]}" style="margin-bottom:6px">{html.escape(authors)} ({r["date"][:4]}). '
            f'{html.escape(r["title"])}. <em>{html.escape(r["journal"])}</em>. '
            f'<a href="{r["url"]}">doi:{html.escape(r["doi"])}</a>'
            f'{" · " + r["license"].upper() if r.get("license") else ""}</li>')
    return '<ol style="font-size:13px;color:#475569;padding-left:20px">' + "".join(items) + "</ol>"


def email_body(content):
    """Đổi đường dẫn hình sang cid: và thêm style inline cho email."""
    content = re.sub(r'src="images/blog/[^"]+/fig(\d+)\.jpg"', r'src="cid:fig\1" style="max-width:100%;height:auto;border-radius:8px"', content)
    content = content.replace('<figure class="post-figure">', '<div style="margin:20px 0;text-align:center">')
    content = content.replace("</figure>", "</div>")
    content = content.replace("<figcaption>", '<div style="font-size:13px;color:#64748b;text-align:left;margin-top:6px">')
    content = content.replace("</figcaption>", "</div>")
    content = content.replace('<p class="lead">', '<p style="font-size:16px;color:#0f172a;font-weight:500">')
    content = content.replace("<h3>", '<h3 style="color:#b91c1c;margin-top:24px">')
    return content


def main():
    user = os.environ.get("MAIL_USERNAME", "").strip()
    pwd = os.environ.get("MAIL_APP_PASSWORD", "").replace(" ", "").strip()
    to = [a.strip() for a in (os.environ.get("MAIL_TO") or user).split(",") if a.strip()]
    pr_url = os.environ.get("PR_URL", "").strip() or f"https://github.com/hanleduy-hust/hanleduy-hust.github.io/pulls"
    if not user or not pwd:
        sys.exit("Thiếu MAIL_USERNAME hoặc MAIL_APP_PASSWORD")

    post = json.loads((DRAFT_DIR / "post.json").read_text(encoding="utf-8"))
    n_figs = len(re.findall(r'class="post-figure"', post["content_vi"]))

    button = ('display:inline-block;padding:12px 22px;border-radius:10px;font-weight:600;'
              'text-decoration:none;font-size:14px;margin:4px 6px 4px 0;')
    body = f"""<!doctype html><html><body style="margin:0;background:#f1f5f9;font-family:Arial,Helvetica,sans-serif;color:#334155">
<div style="max-width:760px;margin:0 auto;background:#fff;padding:28px">
  <p style="font-size:13px;color:#64748b;margin:0">📝 BẢN NHÁP CHỜ DUYỆT · Góc Chuyên gia · {post['date']}</p>
  <h1 style="font-size:22px;color:#0f172a;margin:8px 0">{html.escape(post['title_vi'])}</h1>
  <p style="font-size:14px;color:#64748b;margin:0 0 4px"><em>{html.escape(post['title_en'])}</em></p>
  <p style="font-size:14px">{html.escape(post['summary_vi'])}</p>
  <p style="font-size:13px;color:#64748b">{len(post['references'])} bài báo nguồn · {n_figs} hình minh họa</p>

  <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:12px;padding:16px;margin:16px 0">
    <p style="margin:0 0 10px;font-size:14px"><strong>Thầy duyệt bài này thế nào?</strong> Mở link bên dưới (đăng nhập GitHub):</p>
    <a href="{pr_url}" style="{button}background:#15803d;color:#fff">✅ Duyệt &amp; đăng → bấm “Merge pull request”</a>
    <a href="{pr_url}" style="{button}background:#e2e8f0;color:#334155">❌ Không đăng → bấm “Close pull request”</a>
    <p style="margin:10px 0 0;font-size:12px;color:#64748b">Muốn sửa câu chữ: trong Pull Request chọn tab <em>Files changed</em> → file
    <code>posts/{post['id']}.json</code> → biểu tượng ✏️ để sửa, rồi Merge. Sau khi Merge khoảng 1–2 phút bài sẽ hiện ở
    <a href="{SITE}/blog.html">{SITE}/blog.html</a>. Nếu không duyệt, bản nháp sẽ được thay bằng bản tin của ngày hôm sau.</p>
  </div>

  <hr style="border:none;border-top:1px solid #e2e8f0;margin:24px 0">
  <h2 style="font-size:18px;color:#0f172a">🇻🇳 Bản tiếng Việt</h2>
  <div style="font-size:15px;line-height:1.65">{email_body(post['content_vi'])}</div>
  <h3 style="color:#0f172a">Tài liệu tham khảo</h3>
  {refs_html(post['references'])}

  <hr style="border:none;border-top:1px solid #e2e8f0;margin:24px 0">
  <h2 style="font-size:18px;color:#0f172a">🇬🇧 English version</h2>
  <h3 style="color:#0f172a;margin-top:0">{html.escape(post['title_en'])}</h3>
  <div style="font-size:15px;line-height:1.65">{email_body(post['content_en'])}</div>
  <p style="font-size:12px;color:#94a3b8;margin-top:32px">Email tự động từ hệ thống blog {SITE} (GitHub Actions + Gemini).
  Nội dung do AI tổng hợp từ tóm tắt bài báo – cần được kiểm tra trước khi đăng.</p>
</div></body></html>"""

    msg = MIMEMultipart("related")
    msg["Subject"] = f"[Duyệt bài blog] {post['title_vi']}"
    msg["From"] = f"Blog TS. Hán Lê Duy <{user}>"
    msg["To"] = ", ".join(to)
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(f"{post['title_vi']}\n\n{post['summary_vi']}\n\nDuyệt bài (Merge) hoặc hủy (Close) tại: {pr_url}\n", "plain", "utf-8"))
    alt.attach(MIMEText(body, "html", "utf-8"))
    msg.attach(alt)

    for img in sorted((ROOT / "images" / "blog" / post["id"]).glob("fig*.jpg")):
        part = MIMEImage(img.read_bytes(), _subtype="jpeg")
        part.add_header("Content-ID", f"<{img.stem}>")
        part.add_header("Content-Disposition", "inline", filename=img.name)
        msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(user, pwd)
        s.sendmail(user, to, msg.as_string())
    print(f"Đã gửi email duyệt tới {', '.join(to)}")


if __name__ == "__main__":
    main()
