"""
Đánh số lại tài liệu tham khảo của các bài ĐÃ TẠO theo thứ tự xuất hiện lần đầu trong bài
(trích dẫn đầu tiên là [1], tiếp theo là [2]…). Bài mới do auto_post_ai.py tạo đã tự làm việc này.

Cách dùng:  python scripts/renumber_refs.py posts/<id>.json [posts/<id2>.json ...]
"""

import json
import re
import sys

LINK = re.compile(r'href="#ref-(\d+)"')
PLAIN = re.compile(r"\[([1-9]\d*(?:\s*,\s*[1-9]\d*)*)\]")  # bỏ qua [001] (chỉ số Miller)
CAPTION = re.compile(r"<figcaption>.*?</figcaption>", re.S)
SUP = re.compile(r'<sup class="cite">\[(.*?)\]</sup>')


def renumber(post):
    n = len(post.get("references", []))
    order = []
    def plain_ok(m):
        return all(1 <= int(x) <= n for x in re.findall(r"\d+", m.group(1)))

    for key in ("content_vi", "content_en"):
        # số trong ngoặc dạng chữ thường chỉ là trích dẫn khi nằm trong chú thích hình
        text = CAPTION.sub(lambda c: PLAIN.sub(lambda m: f"@CITE{m.group(1)}@" if plain_ok(m) else m.group(0), c.group(0)),
                           post.get(key, ""))
        for m in re.finditer(LINK.pattern + r"|@CITE([\d, ]+)@", text):
            nums = [int(m.group(1))] if m.group(1) else [int(x) for x in re.findall(r"\d+", m.group(2))]
            for x in nums:
                if 1 <= x <= n and x not in order:
                    order.append(x)
    order += [x for x in range(1, n + 1) if x not in order]
    new = {old: i for i, old in enumerate(order, 1)}
    if all(old == i for old, i in new.items()):
        return False

    for key in ("content_vi", "content_en"):
        t = post.get(key, "")
        # dùng ký hiệu tạm để không đổi số hai lần
        t = re.sub(r'href="#ref-(\d+)">(\d+)</a>',
                   lambda m: f'href="#ref-@{new.get(int(m.group(1)), m.group(1))}">@{new.get(int(m.group(2)), m.group(2))}</a>', t)
        t = CAPTION.sub(lambda c: PLAIN.sub(
            lambda m: ("[" + ", ".join(f"@{new[int(x)]}" for x in re.findall(r"\d+", m.group(1))) + "]")
            if plain_ok(m) else m.group(0), c.group(0)), t)
        t = t.replace("#ref-@", "#ref-").replace(">@", ">").replace("[@", "[").replace(", @", ", ")
        # sắp xếp các số trong cùng một cụm trích dẫn: [5, 2] → [2, 5]
        t = SUP.sub(lambda m: '<sup class="cite">[' + ", ".join(
            f'<a href="#ref-{x}">{x}</a>' for x in sorted({int(v) for v in LINK.findall(m.group(1))})) + "]</sup>", t)
        post[key] = t

    refs = sorted(post["references"], key=lambda r: new[r["n"]])
    for r in refs:
        r["n"] = new[r["n"]]
    post["references"] = refs
    return True


if __name__ == "__main__":
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8") as f:
            post = json.load(f)
        changed = renumber(post)
        if changed:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(post, f, ensure_ascii=False, indent=2)
        print(("đã đánh số lại: " if changed else "giữ nguyên: ") + path)
