# neo4j_crud.py
from neo4j_driver import get_session

def add_book_with_meaning(book: dict, meaning_text: str = None):
    ndc_data = book.get("ndc") or {}
    
    with get_session() as session:
        session.run(
            """
            // 1. 本の基本情報を保存
            MERGE (b:Book {isbn: $isbn})
            SET
                b.title = $title,
                b.authors = $authors,
                b.publisher = $publisher,
                b.published_year = $published_year,
                b.language = $language,
                b.description = $description,
                b.cover = $cover,
                b.updatedAt = datetime()

            // 2. NDC階層を MERGE で作成（上から順に作成していく）
            // Level 1
            WITH b
            CALL {
                WITH b
                UNWIND CASE WHEN $ndc_l1 IS NOT NULL THEN [$ndc_l1] ELSE [] END AS code
                MERGE (n1:NDC {code: code}) SET n1.level = 1
                RETURN n1
            }
            // Level 2
            CALL {
                WITH b
                UNWIND CASE WHEN $ndc_l2 IS NOT NULL THEN [$ndc_l2] ELSE [] END AS code
                MERGE (n2:NDC {code: code}) SET n2.level = 2
                WITH n2
                MATCH (n1:NDC {code: $ndc_l1})
                MERGE (n2)-[:BROADER]->(n1)
                RETURN n2
            }
            // Level 3
            CALL {
                WITH b
                UNWIND CASE WHEN $ndc_l3 IS NOT NULL THEN [$ndc_l3] ELSE [] END AS code
                MERGE (n3:NDC {code: code}) SET n3.level = 3
                WITH n3
                MATCH (n2:NDC {code: $ndc_l2})
                MERGE (n3)-[:BROADER]->(n2)
                RETURN n3
            }

            // 3. 本と具体的な分類(ndc_full)の紐付け
            WITH b
            CALL {
                WITH b
                UNWIND CASE WHEN $ndc_full IS NOT NULL AND $ndc_full <> "" THEN [$ndc_full] ELSE [] END AS code
                MERGE (nf:NDC {code: code})
                MERGE (b)-[:CLASSIFIED_AS]->(nf)
                RETURN nf
            }

            // 4. Subjects（NDLSH）の紐付け
            WITH b
            UNWIND $subjects AS sName
            MERGE (sub:Subject {name: sName})
            MERGE (b)-[:HAS_SUBJECT]->(sub)

            // 5. Meaning（ユーザーの意味付け）の作成
            WITH b
            WHERE $meaning IS NOT NULL AND $meaning <> ""
            CREATE (m:Meaning {
                text: $meaning,
                createdAt: datetime()
            })
            MERGE (b)-[:HAS_MEANING]->(m)
            """,
            isbn=book.get("isbn"),
            title=book.get("title"),
            authors=", ".join(book.get("authors", [])) if isinstance(book.get("authors"), list) else book.get("authors"),
            publisher=book.get("publisher"),
            published_year=book.get("published_year"),
            language=book.get("language"),
            description=book.get("description"),
            cover=book.get("cover"),
            ndc_full=ndc_data.get("ndc_full"),
            ndc_l1=ndc_data.get("ndc_level1"),
            ndc_l2=ndc_data.get("ndc_level2"),
            ndc_l3=ndc_data.get("ndc_level3"),
            subjects=book.get("subjects") or [], # None の場合は空リストに
            meaning=meaning_text
        )

def groups_from_neo4j():
    with get_session() as session:
        result = session.run(
            """
            MATCH (b:Book)
            // OPTIONAL MATCH にすることで、NDCがない本も落とさない
            OPTIONAL MATCH (b)-[:CLASSIFIED_AS]->(n:NDC)
            WITH 
                coalesce(n.code, "未分類") AS ndc, 
                b
            ORDER BY ndc, b.title
            RETURN
                ndc,
                collect({
                    isbn: b.isbn,
                    title: b.title,
                    cover: b.cover
                }) AS books
            """
        )
        groups = []
        for record in result:
            if record["books"]:
                groups.append({
                    "ndc": record["ndc"],
                    "books": record["books"]
                })
        return groups

def get_shelf_books():
    """フロントエンドの棚表示用データを取得"""
    with get_session() as session:
        # SQLiteにある座標(ShelfLayout)と、Neo4jにある本情報を結合して返す設計が理想ですが、
        # Neo4j単体で座標を管理している場合は以下になります
        result = session.run(
            """
            MATCH (b:Book)
            RETURN
                b.isbn AS id,  // book_idをisbnに統一
                b.title AS title,
                b.cover AS cover,
                coalesce(b.shelfRow, 0) AS row,
                coalesce(b.shelfCol, 0) AS col
            ORDER BY row, col
            """
        )

        return {
            "rows": 5,
            "cols": 10,
            "cells": [
                {
                    "row": r["row"],
                    "col": r["col"],
                    "book": {
                        "id": r["id"],
                        "title": r["title"],
                        "cover": r["cover"]
                    }
                }
                for r in result
            ]
        }

def update_shelf_layout_chain(layout_data: list):
    """
    本棚の物理的な並び順を Neo4j の [:NEXT] 関係として構築する
    layout_data: [{"isbn": "...", "x": 0, "y": 0}, ...] のリスト
    """
    with get_session() as session:
        # 1. まずは既存の [:NEXT] リレーションをすべて削除（再構築のため）
        # ※ 蔵書そのものを消すわけではなく、隣接関係だけをリセットします
        session.run(
            """
            MATCH (:Book)-[r:NEXT]->(:Book)
            DELETE r
            """
        )

        # 2. 段（x）ごとにグループ化して、列（y）の昇順にソート
        shelves = {}
        for item in layout_data:
            x = item['x']
            if x not in shelves:
                shelves[x] = []
            shelves[x].append(item)
        
        for x in shelves:
            # y座標（左から右）でソート
            sorted_books = sorted(shelves[x], key=lambda b: b['y'])
            
            # 隣り合う本を繋ぐ
            for i in range(len(sorted_books) - 1):
                book_a = sorted_books[i]
                book_b = sorted_books[i+1]
                
                session.run(
                    """
                    MATCH (a:Book {isbn: $isbn_a})
                    MATCH (b:Book {isbn: $isbn_b})
                    MERGE (a)-[:NEXT]->(b)
                    """,
                    isbn_a=book_a['isbn'],
                    isbn_b=book_b['isbn']
                )

        # 3. 座標情報そのものも各ノードに同期させておく（念のため）
        for item in layout_data:
            session.run(
                """
                MATCH (b:Book {isbn: $isbn})
                SET b.shelfRow = $x, b.shelfCol = $y
                """,
                isbn=item['isbn'],
                x=item['x'],
                y=item['y']
            )
