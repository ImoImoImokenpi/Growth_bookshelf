import httpx
import xmltodict
import traceback
import asyncio
import re
from typing import Dict, Any, Optional, List

# --- 定数 ---
NDL_OPENSEARCH = "https://ndlsearch.ndl.go.jp/api/opensearch"
GOOGLE_BOOKS_API = "https://www.googleapis.com/books/v1/volumes"

# --- ユーティリティ関数 ---

def safe_field(field):
    if field is None: return ""
    if isinstance(field, list):
        fields = [safe_field(f) for f in field if f]
        return fields[0] if fields else ""
    if isinstance(field, dict): return field.get("#text", "")
    return str(field)

def extract_identifier(identifiers, target_type):
    if not identifiers: return None
    ids = identifiers if isinstance(identifiers, list) else [identifiers]
    found_values = []
    for i in ids:
        if isinstance(i, dict):
            id_type = i.get("@xsi:type", "")
            if id_type and target_type.upper() in id_type.upper():
                val = i.get("#text", "")
                if val:
                    clean_val = re.sub(r'[^0-9X]', '', val.upper())
                    found_values.append(clean_val)
    
    if not found_values: return None
    found_values.sort(key=len, reverse=True)
    return found_values[0]

def ensure_list(value):
    if value is None: return []
    return value if isinstance(value, list) else [value]

def extract_ndc(subjects):
    if not subjects: return None
    subs = ensure_list(subjects)
    for s in subs:
        if isinstance(s, dict):
            stype = s.get("@xsi:type", "")
            if stype and "NDC" in stype.upper():
                return s.get("#text", "")
    return None

GOOGLE_BOOKS_API_KEY = "AIzaSyAw_t2zWB_U5meGL7SENj929snUMEeR0-M"

# --- API連携関数 ---
async def fetch_single_book_cover(isbn: str, client: httpx.AsyncClient):
    # 1. openBD を最優先（制限が緩く、日本の本に強い）
    try:
        openbd_url = f"https://api.openbd.jp/v1/get?isbn={isbn}"
        resp = await client.get(openbd_url, timeout=3.0)
        if resp.status_code == 200:
            data = resp.json()
            if data and data[0] and "summary" in data[0]:
                cover = data[0]["summary"].get("cover")
                if cover:
                    print(f"[DEBUG] Found via openBD: {isbn}")
                    return cover
    except Exception: pass

    # 2. Google Books API (openBDで見つからなかった場合のみ)
    await asyncio.sleep(0.3)
    try:
        # APIキーなしの方が制限が緩い場合があるため、一旦 key を外した状態で試す
        url = "https://www.googleapis.com/books/v1/volumes"
        params = {
            "q": f"isbn:{isbn}",
            "key": GOOGLE_BOOKS_API_KEY
        }
        resp = await client.get(url, params=params, timeout=5.0)
        
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("items", [])
            if items:
                img = items[0].get("volumeInfo", {}).get("imageLinks", {}).get("thumbnail")
                if img:
                    return img.replace("http://", "https://")
        elif resp.status_code == 403:
            print(f"[DEBUG] Google 403 Forbidden for ISBN: {isbn} (Rate Limit)")
    except Exception: pass

    return ""

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
            cover = await fetch_single_book_cover(isbn_value, client) if isbn_value else None

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