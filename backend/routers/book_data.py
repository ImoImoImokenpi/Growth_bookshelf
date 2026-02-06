import httpx
import xmltodict
import traceback
from typing import Dict, Any, Optional, List

# --- 定数 ---
NDL_OPENSEARCH = "https://ndlsearch.ndl.go.jp/api/opensearch"
GOOGLE_BOOKS_API = "https://www.googleapis.com/books/v1/volumes"

# --- 内部ユーティリティ (XMLパース時のエラー防止用) ---

def safe_field(field):
    if field is None: return ""
    if isinstance(field, list):
        fields = [safe_field(f) for f in field if f]
        return fields[0] if fields else ""
    if isinstance(field, dict): return field.get("#text", "")
    return str(field)

def ensure_list(value):
    if value is None: return []
    return value if isinstance(value, list) else [value]

def extract_identifier(identifiers, target_type):
    if not identifiers: return None
    ids = ensure_list(identifiers)
    found_values = []
    for i in ids:
        if isinstance(i, dict):
            id_type = i.get("@xsi:type", "")
            if id_type and target_type.upper() in id_type.upper():
                val = i.get("#text", "")
                if val: found_values.append(val.replace("-", "").strip())
    if not found_values: return None
    if target_type.upper() == "ISBN":
        found_values.sort(key=len, reverse=True) # 長いISBNを優先
    return found_values[0]

def extract_ndc(subjects):
    if not subjects: return None
    subs = ensure_list(subjects)
    for s in subs:
        if isinstance(s, dict):
            stype = s.get("@xsi:type", "")
            if stype and "NDC" in stype.upper():
                return s.get("#text", "")
    return None

# --- API連携関数 ---

async def fetch_cover_by_isbn(isbn: str, client: httpx.AsyncClient) -> Optional[str]:
    """Google Books APIから非同期で書影を取得"""
    params = {"q": f"isbn:{isbn}", "maxResults": 1}
    try:
        res = await client.get(GOOGLE_BOOKS_API, params=params, timeout=5.0)
        if res.status_code == 200:
            data = res.json()
            items = data.get("items", [])
            if items:
                img_links = items[0].get("volumeInfo", {}).get("imageLinks", {})
                url = img_links.get("thumbnail") or img_links.get("smallThumbnail")
                return url.replace("http://", "https://") if url else None
    except Exception:
        pass
    return None

async def fetch_book_metadata(
    isbn: Optional[str] = None,
    title: Optional[str] = None,
    author: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """NDLからメタデータを取得し、書影をGoogleから補完するメイン関数"""
    
    params = {"cnt": 1}
    if isbn:
        params["isbn"] = isbn.replace("-", "")
    if title:
        params["title"] = title
    if author:
        params["any"] = author
    
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            res = await client.get(NDL_OPENSEARCH, params=params, timeout=10.0)
            res.raise_for_status()
            raw_data = xmltodict.parse(res.text)

            channel = raw_data.get("rss", {}).get("channel", {})
            item = channel.get("item")

            if not item:
                return None
            if isinstance(item, list):
                item = item[0]

            # 1. 識別子とNDCの抽出
            ids_node = item.get("dc:identifier")
            isbn_value = isbn or extract_identifier(ids_node, "ISBN")
            raw_ndc = extract_ndc(item.get("dc:subject"))

            # 2. 書影の取得 (Google Books)
            cover = await fetch_cover_by_isbn(isbn_value, client) if isbn_value else None

            # 3. データの整形
            return {
                "isbn": isbn_value,
                "title": safe_field(item.get("dc:title")),
                "authors": ensure_list(safe_field(item.get("dc:creator"))),
                "publisher": safe_field(item.get("dc:publisher")),
                "published_year": safe_field(item.get("dcterms:issued")),
                "language": safe_field(item.get("dc:language")),
                "description": safe_field(item.get("description")),
                "ndc": {
                    "ndc_full": raw_ndc,
                    "ndc_level1": raw_ndc[0] if raw_ndc else None,
                    "ndc_level2": raw_ndc[:2] if raw_ndc and len(raw_ndc) >= 2 else None,
                    "ndc_level3": raw_ndc[:3] if raw_ndc and len(raw_ndc) >= 3 else None,
                } if raw_ndc else None,
                "subjects": ensure_list(safe_field(item.get("dc:subject"))),
                "cover": cover or "/noimage.png",
            }
        
    except Exception:
        print(f"[ERROR] {traceback.format_exc()}")
        return None