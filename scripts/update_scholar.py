import json
import os
from scholarly import scholarly

# Mã định danh Google Scholar của TS. Hán Lê Duy
SCHOLAR_ID = "VmBCs30AAAAJ"

def update_publications():
    print("Đang kết nối tới Google Scholar...")
    author = scholarly.search_author_id(SCHOLAR_ID)
    author = scholarly.fill(author, sections=['publications'])
    
    pubs = []
    for pub in author.get('publications', []):
        filled_pub = scholarly.fill(pub)
        bib = filled_pub.get('bib', {})
        
        title = bib.get('title', '')
        year = bib.get('pub_year', '2026')
        venue = bib.get('journal', bib.get('conference', 'Google Scholar'))
        publisher = bib.get('publisher', '')
        url = filled_pub.get('pub_url', '#')
        
        # Lấy danh sách tác giả
        authors = bib.get('author', 'Duy Le Han')
        
        pubs.append({
            "year": str(year),
            "title": title,
            "authors": authors,
            "venue": f"{venue} ({publisher})" if publisher else venue,
            "url": url
        })
    
    # Sắp xếp bài báo mới nhất lên đầu
    pubs.sort(key=lambda x: x['year'], reverse=True)
    
    # Lưu ra file scholar_pubs.json để trang web đọc tự động
    with open("scholar_pubs.json", "w", encoding="utf-8") as f:
        json.dump(pubs[:15], f, ensure_ascii=False, indent=2)
    print(f"Đã đồng bộ thành công {len(pubs)} công trình từ Google Scholar!")

if __name__ == "__main__":
    update_publications()
