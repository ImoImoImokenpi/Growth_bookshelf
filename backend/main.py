from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import xmltodict
import math
import asyncio
import re
import os
import logging
import traceback

from database import Base, engine
import routers.myhand as myhand_router
import routers.knowledge_graph as knowledge_graph_router
import routers.bookshelf as bookshelf_router

# ─── ログ設定 ───────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ─── DB初期化 ───────────────────────────────────────────
Base.metadata.create_all(bind=engine)

app = FastAPI()

# ─── CORS設定 ───────────────────────────────────────────
# ❶ CORS_ORIGINも環境変数に移動
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("CORS_ORIGIN", "http://localhost:3000")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── ルータ登録 ─────────────────────────────────────────
app.include_router(myhand_router.router)
app.include_router(knowledge_graph_router.router)
app.include_router(bookshelf_router.router)

# ─── 環境変数から設定読み込み ───────────────────────────
NDL_OPENSEARCH = "https://ndlsearch.ndl.go.jp/api/opensearch"

# ❶ APIキーは必ず環境変数から読む（ハードコード禁止）
GOOGLE_BOOKS_API_KEY = os.getenv("GOOGLE_BOOKS_API_KEY", "")

if not GOOGLE_BOOKS_API_KEY:
    logger.warning("GOOGLE_BOOKS_API_KEY が設定されていません。Google Books フォールバックは無効になります。")


# ─── ユーティリティ関数 ─────────────────────────────────

def safe_field(field) -> str:
    """単一フィールドを安全に文字列に変換する。"""
    if field is None:
        return ""
    if isinstance(field, dict):
        return field.get("#text", "")
    if isinstance(field, list):
        for item in field:
            val = safe_field(item)
            if val:
                return val
        return ""
    return str(field)


def safe_field_list(field) -> list[str]:
    """❷ リストフィールドを全て文字列リストとして返す（著者複数対応）。"""
    if field is None:
        return []
    items = field if isinstance(field, list) else [field]
    return [v for item in items if (v := safe_field(item))]


def _is_valid_isbn(isbn: str) -> bool:
    """❸ ISBNが正しい桁数かチェックする。"""
    digits = re.sub(r'[^0-9Xx]', '', isbn)
    return len(digits) in (10, 13)


def extract_identifier(identifiers, target_type: str) -> str | None:
    """dc:identifierからISBNを抽出し、検証して返す。"""
    if not identifiers:
        return None

    ids = identifiers if isinstance(identifiers, list) else [identifiers]
    found_values: list[str] = []

    for i in ids:
        if not isinstance(i, dict):
            continue
        id_type = i.get("@xsi:type", "")
        if target_type.upper() not in id_type.upper():
            continue

        val = i.get("#text", "")
        if not val:
            continue

        # ハイフンやスペースのみ除去（数字・Xのみ残す）
        clean_val = re.sub(r'[^0-9Xx]', '', val.upper())

        # ❸ 桁数の検証を追加：無効なISBNをスキップ
        if _is_valid_isbn(clean_val):
            found_values.append(clean_val)

    if not found_values:
        return None

    # 13桁があればそちらを優先
    found_values.sort(key=len, reverse=True)
    return found_values[0]


def ensure_list(value) -> list:
    """値がNoneなら空リスト、リストでなければリストにする。"""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


# ─── 書影取得（openBD → Google Books の順にフォールバック）─

async def _fetch_openbd_cover(isbn: str, client: httpx.AsyncClient) -> str:
    """openBDから書影URLを取得する。"""
    url = f"https://api.openbd.jp/v1/get?isbn={isbn}"
    resp = await client.get(url, timeout=3.0)
    if resp.status_code == 200:
        data = resp.json()
        if data and data[0] and "summary" in data[0]:
            cover = data[0]["summary"].get("cover", "")
            if cover:
                logger.info(f"[openBD] 書影取得成功: ISBN={isbn}")
                return cover
    return ""


async def _fetch_google_cover(isbn: str, client: httpx.AsyncClient) -> str:
    """❹ Google Books APIから書影を取得する。403時はスキップ。"""
    if not GOOGLE_BOOKS_API_KEY:
        return ""

    url = "https://www.googleapis.com/books/v1/volumes"
    params = {"q": f"isbn:{isbn}", "key": GOOGLE_BOOKS_API_KEY}

    resp = await client.get(url, params=params, timeout=5.0)

    if resp.status_code == 403:
        logger.warning(f"[Google Books] Rate limit (403): ISBN={isbn} → スキップ")
        return ""  # リトライせずスキップ

    if resp.status_code == 200:
        data = resp.json()
        items = data.get("items", [])
        if items:
            img = items[0].get("volumeInfo", {}).get("imageLinks", {}).get("thumbnail", "")
            if img:
                return img.replace("http://", "https://")
    return ""


async def fetch_single_book_cover(isbn: str, client: httpx.AsyncClient) -> str:
    """1冊分の書影を取得する。openBD優先、なければGoogle Books。"""
    # 1. openBD
    try:
        cover = await _fetch_openbd_cover(isbn, client)
        if cover:
            return cover
    except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
        logger.debug(f"[openBD] 失敗: ISBN={isbn}, error={e}")

    # Google 呼び出し前に少し待機
    await asyncio.sleep(0.3)

    # 2. Google Books
    try:
        cover = await _fetch_google_cover(isbn, client)
        if cover:
            return cover
    except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
        logger.debug(f"[Google Books] 失敗: ISBN={isbn}, error={e}")

    return ""


async def get_covers(books: list[dict], client: httpx.AsyncClient) -> list[dict]:
    """❺ 複数本の書影を並列に取得する（セマフォで同時実行を制限）。"""
    semaphore = asyncio.Semaphore(3)

    async def _sem_fetch(isbn: str) -> str:
        async with semaphore:
            await asyncio.sleep(0.5)  # レートリミット対策
            return await fetch_single_book_cover(isbn, client)

    tasks = [_sem_fetch(b["isbn"]) for b in books]
    results = await asyncio.gather(*tasks)

    for book, cover in zip(books, results):
        book["cover"] = cover or "/noimage.png"

    return books


# ─── NDL データ処理 ─────────────────────────────────────

def _parse_ndl_items(raw_items: list[dict]) -> list[dict]:
    """NDLのitem配列を標準化されたbookリストに変換する。"""
    books: list[dict] = []
    for item in raw_items:
        ids_node = item.get("dc:identifier")
        isbn = extract_identifier(ids_node, "ISBN")
        if not isbn:
            continue

        books.append({
            "isbn": isbn,
            "title": safe_field(item.get("dc:title")),
            "authors": safe_field_list(item.get("dc:creator")),  # ❷ 複数著者に対応
            "publisher": safe_field(item.get("dc:publisher")),
            "link": item.get("link", ""),
            "cover": "/noimage.png",
        })
    return books


# ─── メインエンドポイント ───────────────────────────────

@app.get("/search")
async def search_books(
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),  # ❻ 上限キャップを追加
):
    """書籍検索エンドポイント。NDLから検索し、書影を付与して返す。"""
    FETCH_SIZE = 100
    ndl_timeout = httpx.Timeout(20.0, connect=10.0)

    async def _fetch_ndl_page(idx: int) -> dict:
        async with httpx.AsyncClient(follow_redirects=True, timeout=ndl_timeout) as client:
            params = {"any": q, "cnt": FETCH_SIZE, "idx": idx}
            res = await client.get(NDL_OPENSEARCH, params=params)
            res.raise_for_status()
            return xmltodict.parse(res.text)

    try:
        # ── 1. NDL 1ページ目取得 ──
        raw_data = await _fetch_ndl_page(1)
        channel = raw_data.get("rss", {}).get("channel", {})
        total_available = int(channel.get("openSearch:totalResults", 0))

        items = ensure_list(channel.get("item", []))
        collected_books = _parse_ndl_items(items)

        # ── 2. 必要に応じて2ページ目も取得 ──
        if len(collected_books) < per_page and total_available > FETCH_SIZE:
            try:
                extra_data = await _fetch_ndl_page(FETCH_SIZE + 1)
                extra_items = ensure_list(
                    extra_data.get("rss", {}).get("channel", {}).get("item", [])
                )
                collected_books.extend(_parse_ndl_items(extra_items))
            except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
                logger.warning(f"[NDL] 2ページ目取得失敗: {e}")

        # ── 3. ページング ──
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        page_books = collected_books[start_idx:end_idx]

        # ── 4. 書影取得 ──
        if page_books:
            async with httpx.AsyncClient(timeout=ndl_timeout) as client:
                page_books = await get_covers(page_books, client)

        # ── 5. レスポンス返却 ──
        return {
            "books": page_books,
            "page": page,
            "total_items_found": len(collected_books),
            "total_pages": math.ceil(len(collected_books) / per_page),
        }

    except httpx.TimeoutException:
        logger.error(f"[NDL] タイムアウト: q={q}")
        raise HTTPException(status_code=504, detail="検索リクエストがタイムアウトしました")
    except httpx.HTTPStatusError as e:
        logger.error(f"[NDL] HTTPエラー: {e.response.status_code}, q={q}")
        raise HTTPException(status_code=502, detail="検索APIからエラーが返されました")
    except Exception:
        logger.error(f"[NDL] 未予期エラー:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="検索に失敗しました")


@app.get("/")
def root():
    return {"message": "Stable Mode Running"}