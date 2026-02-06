from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List
from models import ShelfLayout, ShelfDesign
from sqlalchemy.orm import Session
from database import get_db
from neo4j_driver import get_session
from neo4j_crud import update_shelf_layout_chain  # Neo4j更新ロジック
from routers.myhand import rebuild_shelf_layout

router = APIRouter(prefix="/bookshelf", tags=["bookshelf"])

# --- リクエストモデル ---
class BookPosition(BaseModel):
    isbn: str
    x: int
    y: int

BOOK_WIDTH = 80
BOOK_HEIGHT = 120
BOOKS_PER_SHELF = 10

SHELF_MARGIN_X = 40
SHELF_MARGIN_Y = 30
SHELF_HEIGHT = 150
SHELF_GAP = 30

def get_shelf_books(db: Session):
    layouts = db.query(ShelfLayout).all()
    
    # 1. 本棚全体の設定値（books_per_shelf）を取得
    # 全レコードに同じ値が入っている想定なので、最初の1件から取得
    current_books_per_shelf = 10  # デフォルト値
    if layouts:
        # モデルに books_per_shelf カラムがある前提
        current_books_per_shelf = layouts[0].books_per_shelf 

    layout_map = {
        l.isbn: {"row": l.x, "col": l.y}
        for l in layouts
    }

    if not layout_map:
        return {
            "books": [], 
            "books_per_shelf": current_books_per_shelf
        }
    
    with get_session() as session:
        result = session.run(
            """
            MATCH (b:Book)
            WHERE b.isbn IN $isbns
            RETURN
                b.isbn AS id,
                b.title AS title,
                b.cover AS cover
            """,
            isbns=list(layout_map.keys())
        )

        books = []
        for r in result:
            pos = layout_map.get(r["id"])
            if not pos: continue

            books.append({
                "isbn": r["id"], # 'id' ではなく 'isbn' で統一するとフロントで扱いやすい
                "title": r["title"],
                "cover": r["cover"],
                "x": pos["row"],
                "y": pos["col"]
            })

    # 2. レスポンスの形を整える
    return {
        "books": books,
        "books_per_shelf": current_books_per_shelf
    }
    
@router.get("/")
def fetch_bookshelf(db: Session = Depends(get_db)):
    # デザイン設定を取得
    design = db.query(ShelfDesign).first()
    if not design:
        design = ShelfDesign(books_per_shelf=0, total_shelves=0)
        db.add(design)
        db.commit()

    # 本の座標データを取得
    layouts = db.query(ShelfLayout).all()
    layout_map = {l.isbn: {"x": l.x, "y": l.y} for l in layouts}

    # Neo4jから本の詳細（タイトル・カバー）を結合
    books = []
    if layout_map:
        with get_session() as session:
            result = session.run(
                "MATCH (b:Book) WHERE b.isbn IN $isbns RETURN b.isbn, b.title, b.cover",
                isbns=list(layout_map.keys())
            )
            for r in result:
                pos = layout_map.get(r["b.isbn"])
                books.append({
                    "isbn": r["b.isbn"],
                    "title": r["b.title"],
                    "cover": r["b.cover"],
                    "x": pos["x"],
                    "y": pos["y"]
                })

    return {
        "books": books,
        "books_per_shelf": design.books_per_shelf,
        "total_shelves": design.total_shelves
    }

@router.post("/sync-layout")
async def sync_layout(layout_data: List[BookPosition], db: Session = Depends(get_db)):
    """
    ドラッグ終了時にフロントから呼ばれる。
    SQLiteの物理座標を更新し、Neo4jの隣接リレーションを再構築する。
    """
    try:
        # 1. SQLite側の座標更新
        for pos in layout_data:
            db.query(ShelfLayout).filter(ShelfLayout.isbn == pos.isbn).update({
                "x": pos.x,
                "y": pos.y
            })
        
        # 2. 段数の自動拡張チェック
        design = db.query(ShelfDesign).first()
        if design and layout_data:
            max_row_in_data = max(pos.x for pos in layout_data)
            if max_row_in_data >= design.total_shelves:
                design.total_shelves = max_row_in_data + 1

        db.commit()

        # 3. Neo4j側のチェーン更新
        # Pydanticモデルを辞書のリストに変換して渡す
        neo4j_payload = [item.model_dump() for item in layout_data]
        update_shelf_layout_chain(neo4j_payload)

        return {
            "status": "success", 
            "total_shelves": design.total_shelves if design else 0
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/reorganize")
async def reorganize(db: Session = Depends(get_db)):
    """
    現在のSQLiteの状態を正として、Neo4j側の繋がりをすべて作り直す
    """
    try:
        layouts = db.query(ShelfLayout).all()
        if layouts:
            neo4j_payload = [{"isbn": l.isbn, "x": l.x, "y": l.y} for l in layouts]
            update_shelf_layout_chain(neo4j_payload)
        return {"status": "success", "message": "Graph reconstructed from SQL data."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/add_shelves") # GETからPOSTに修正
async def add_shelves(db: Session = Depends(get_db)):
    design = db.query(ShelfDesign).first()
    if not design:
        design = ShelfDesign(books_per_shelf=10, total_shelves=3)
        db.add(design)
    
    try:
        design.total_shelves += 1
        # rebuild_shelf_layout(db, 
        #                     books_per_shelf=design.books_per_shelf, 
        #                     total_shelves=design.total_shelves)
        db.commit()
        return {"status": "success", "total_shelves": design.total_shelves}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/add_per_shelf")
async def add_per_shelf(books_per_shelf: int, db: Session = Depends(get_db)):
    design = db.query(ShelfDesign).first()
    if design:
        design.books_per_shelf = books_per_shelf
        rebuild_shelf_layout(db, books_per_shelf=books_per_shelf, total_shelves=design.total_shelves)
        db.commit()
    return {"status": "success"}

@router.post("/remove_shelves")
async def remove_shelves(db: Session = Depends(get_db)):
    design = db.query(ShelfDesign).first()
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    # 1段より少なくはできないように制限
    if design.total_shelves <= 1:
        raise HTTPException(status_code=400, detail="これ以上段数を減らせません")

    # 2. 削除対象となる「一番下の段」のIDを特定
    # 段のIDが1から始まると想定し、現在の最大段数が対象
    target_x = design.total_shelves -1

    # 3. その段に本があるかチェック
    book_on_shelf = db.query(ShelfLayout).filter(ShelfLayout.x == target_x).first()
    
    if book_on_shelf:
        # 対象の段（y座標）に1件でもデータがあればエラーを返す
        raise HTTPException(
            status_code=400, 
            detail=f"{design.total_shelves}段目（y={target_x}）に本が配置されているため、削除できません。"
        )
    
    try:
        design.total_shelves -= 1
        # rebuild_shelf_layout(db, 
        #                     books_per_shelf=design.books_per_shelf, 
        #                     total_shelves=design.total_shelves)
        db.commit()
        return {"status": "success", "total_shelves": design.total_shelves}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

class ConceptRequest(BaseModel):
    meaning: str
    isbns: List[str]

@router.post("/save-concept")
async def save_concept(data: ConceptRequest):
    if not data.meaning or not data.isbns:
        raise HTTPException(status_code=400, detail="Meaning and ISBNs are required")

    try:
        with get_session() as session:
            # 1. Conceptノードを作成 (または既存のものを取得)
            # 2. 指定されたISBNの本とリレーションを作成
            session.run("""
                MERGE (c:Concept {name: $meaning})
                WITH c
                UNWIND $isbns AS isbn
                MATCH (b:Book {isbn: isbn})
                MERGE (b)-[:BELONGS_TO]->(c)
            """, meaning=data.meaning, isbns=data.isbns)
            
        return {"status": "success", "message": f"Concept '{data.meaning}' linked to {len(data.isbns)} books."}
    except Exception as e:
        print(f"Neo4j Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
