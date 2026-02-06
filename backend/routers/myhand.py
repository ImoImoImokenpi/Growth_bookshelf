from fastapi import APIRouter, Depends, HTTPException, Body
from schemas import AddFromHandRequest
from sqlalchemy.orm import Session
from database import get_db
from models import MyHand
from neo4j_crud import add_book_with_meaning
from routers.book_data import fetch_book_metadata
from utils.layout_engine import rebuild_shelf_layout

# from routers.knowledge_graph import rebuild_knowledge_graph

router = APIRouter(prefix="/books", tags=["books"])

GOOGLE_BOOKS_API = "https://www.googleapis.com/books/v1/volumes"

@router.get("/myhand")
def get_myhand(db: Session = Depends(get_db)):
    items = db.query(MyHand).all()

    results = []
    for item in items:
        results.append({
            "isbn": item.isbn,
            "title": item.title,
            "authors": item.authors,
            "cover": item.cover,
        })

    return results

@router.post("/add_to_hand")
def add_to_hand(
    data: dict = Body(...),
    db: Session = Depends(get_db)
):
    # -------------------------
    # ① ISBN チェック & 正規化
    # -------------------------
    isbn = data.get("isbn")
    if not isbn:
        raise HTTPException(status_code=400, detail="ISBN is required")

    # -------------------------
    # ② 既存チェック
    # -------------------------
    existing = db.query(MyHand).filter(MyHand.isbn == isbn).first()
    if existing:
        return {
            "message": "already exists",
            "isbn": isbn,
            "id": existing.id
        }

    # -------------------------
    # ③ メタデータ取得
    # -------------------------
    authors = data.get("authors") or []
    authors_str = ",".join(authors) if isinstance(authors, list) else str(authors)

    # -------------------------
    # ④ MyHand に保存
    # -------------------------
    new_book = MyHand(
        isbn=isbn,
        title=data.get("title"),
        authors=authors_str,
        cover=data.get("cover"),
    )

    db.add(new_book)
    db.commit()
    db.refresh(new_book)

    return {
        "message": "added",
        "isbn": isbn,
        "id": new_book.id
    }

@router.post("/add_from_hand")
async def add_from_hand(req: AddFromHandRequest, db: Session = Depends(get_db)):
    if not req.isbns:
        raise HTTPException(status_code=400, detail="No books selected")

    for isbn in req.isbns:
        # ① メタデータ取得
        book = await fetch_book_metadata(isbn)
        
        if book:
            # ② Neo4j に保存（意味・概念）
            add_book_with_meaning(book)

            # SQLite (MyHand) から削除
            db.query(MyHand).filter(MyHand.isbn == isbn).delete()

    # レイアウト再構築
    rebuild_shelf_layout(db)
    db.commit()

    return {"message": "Success"}

# 本を手元から削除
@router.delete("/remove_from_hand/{isbn}")
def remove_from_hand(isbn: str, db: Session = Depends(get_db)):
    # DBから該当本を検索
    book = db.query(MyHand).filter(MyHand.isbn == isbn).first()
    if not book:
        raise HTTPException(status_code=404, detail="本が見つかりません")

    db.delete(book)
    db.commit()
    return {"status": "success", "deleted_book_id": isbn}

# @router.post("/add_from_hand")
# def add_from_hand(data: dict = Body(...), db: Session = Depends(get_db)):
#     book_ids = data.get("book_ids", [])

#     if not book_ids:
#         raise HTTPException(status_code=400, detail="book_ids is empty")

#     hand_books = (
#         db.query(MyHand)
#         .filter(MyHand.book_id.in_(book_ids))
#         .all()
#     )

#     for h in hand_books:
#         # すでに本棚にあるか確認
#         exists = db.query(MyBookshelf).filter(MyBookshelf.book_id == h.book_id).first()
#         if not exists:
#             db.add(MyBookshelf(
#                 book_id=h.book_id,
#                 title=h.title,
#                 author=h.author,
#                 cover=h.cover,
#             ))

#         # 手元から削除
#         db.delete(h)

#     db.commit()
#     rebuild_knowledge_graph(db)
#     db.commit()

#     return {"message": "added", "count": len(hand_books)}