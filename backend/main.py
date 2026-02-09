from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import xmltodict
import math
import asyncio
import re
import traceback

from database import Base, engine
import routers.myhand as myhand_router
import routers.knowledge_graph as knowledge_graph_router
import routers.bookshelf as bookshelf_router
from neo4j_driver import get_session

# DB初期化
Base.metadata.create_all(bind=engine)

app = FastAPI()

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

NDL_OPENSEARCH = "https://ndlsearch.ndl.go.jp/api/opensearch"

# ルータ登録
app.include_router(myhand_router.router)
app.include_router(knowledge_graph_router.router)
app.include_router(bookshelf_router.router)

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

GOOGLE_BOOKS_API_KEY = "AIzaSyAw_t2zWB_U5meGL7SENj929snUMEeR0-M"

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

# get_cover 関数の待機時間を少し伸ばして 403 を回避
async def get_cover(books: list, client: httpx.AsyncClient):
    semaphore = asyncio.Semaphore(3) 

    async def sem_fetch(isbn):
        async with semaphore:
            # 待機時間を 0.2 -> 0.5 秒に伸ばして Google の機嫌を伺う
            await asyncio.sleep(0.5) 
            return await fetch_single_book_cover(isbn, client)

    tasks = [sem_fetch(b["isbn"]) for b in books]
    results = await asyncio.gather(*tasks)
    
    for i, cover in enumerate(results):
        books[i]["cover"] = cover
    return books

# --- メインエンドポイント ---

@app.get("/search")
async def search_books(q: str = Query(...), page: int = 1, per_page: int = 20):
    FETCH_SIZE = 100 
    # NDL用のタイムアウト設定
    ndl_timeout = httpx.Timeout(20.0, connect=10.0)
    
    async def fetch_ndl_page(idx: int):
        async with httpx.AsyncClient(follow_redirects=True, timeout=ndl_timeout) as client:
            params = {"any": q, "cnt": FETCH_SIZE, "idx": idx}
            res = await client.get(NDL_OPENSEARCH, params=params)
            res.raise_for_status()
            return xmltodict.parse(res.text)

    def pre_process_items(raw_items):
        books = []
        for item in raw_items:
            ids_node = item.get("dc:identifier")
            isbn = extract_identifier(ids_node, "ISBN")
            if isbn:
                books.append({
                    "isbn": isbn,
                    "title": safe_field(item.get("dc:title")),
                    "authors": ensure_list(safe_field(item.get("dc:creator"))),
                    "publisher": safe_field(item.get("dc:publisher")),
                    "link": item.get("link"),
                    "cover": "/noimage.png"
                })
        return books

    try:
        # 1. NDLデータ取得
        raw_data = await fetch_ndl_page(1)
        channel = raw_data.get('rss', {}).get('channel', {})
        total_available = int(channel.get("openSearch:totalResults", 0))
        
        items = ensure_list(channel.get('item', []))
        collected_books = pre_process_items(items)

        if len(collected_books) < per_page and total_available > FETCH_SIZE:
            try:
                extra_data = await fetch_ndl_page(101)
                extra_items = ensure_list(extra_data.get('rss', {}).get('channel', {}).get('item', []))
                collected_books.extend(pre_process_items(extra_items))
            except:
                print("[DEBUG] NDL extra fetch failed (timeout or other).")

        # 2. ページング
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        target_page_books = collected_books[start_idx:end_idx]

        # 3. 書影取得 (一括処理)
        if target_page_books:
            # ここでも共通のタイムアウト設定を持つクライアントを使用
            async with httpx.AsyncClient(timeout=ndl_timeout) as client:
                target_page_books = await get_cover(target_page_books, client)

        return {
            "books": target_page_books,
            "page": page,
            "total_items_found": len(collected_books),
            "total_pages": math.ceil(len(collected_books) / per_page)
        }

    except Exception as e:
        print(f"[ERROR] {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Search timeout or failed")

@app.get("/")
def root():
    return {"message": "Stable Mode Running"}