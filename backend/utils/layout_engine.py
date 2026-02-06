from sqlalchemy.orm import Session
from models import ShelfLayout, ShelfDesign
from neo4j_crud import groups_from_neo4j
import logging

logger = logging.getLogger(__name__)

# レイアウト計算関数
def calc_shelf_position(groups, books_per_shelf: int):
    """
    groups: [{"ndc": "913", "books": [...]}, ...] のリスト
    books_per_shelf: 1段に収める最大冊数
    """
    positioned_books = []
    seen_isbns = set()
    current_row = 0
    current_col = 0

    for group in groups:
        # group["books"] リストを取り出す
        books = group.get("books", [])
        
        # --- ここがポイント：入らない場合だけ段を増やすロジック ---
        # 1. 重複を除いた「このグループで実際に配置する本」の数を確認
        new_books_in_group = [b for b in books if b["isbn"] not in seen_isbns]
        count = len(new_books_in_group)

        if count == 0:
            continue

        # 2. 「今の段の残りスペース」より「グループの冊数」が多いかチェック
        # 残りスペース = books_per_shelf - current_col
        if count > (books_per_shelf - current_col):
            # 今の段に1冊でも本がある場合のみ、次の段へ（空の段ならそのまま使う）
            if current_col > 0:
                current_row += 1
                current_col = 0
        
        for book in new_books_in_group:
            isbn = book["isbn"]
            
            # 配置情報を追加
            positioned_books.append({
                "isbn": isbn,  # SQLiteのShelfLayoutと合わせる
                "row": current_row,
                "col": current_col,
                "books_per_shelf": books_per_shelf
            })

            seen_isbns.add(isbn)
            current_col += 1

            # 1段がいっぱいになったら次の段へ
            if current_col >= books_per_shelf:
                current_row += 1
                current_col = 0

    return positioned_books

# 本棚再構築関数
def rebuild_shelf_layout(db: Session, books_per_shelf: int = 5, total_shelves: int = 3):
    print(f"DEBUG: 再構築を開始します。1段あたりの冊数: {books_per_shelf}")
    try:
        # 1. Neo4j からグループ化された書籍データを取得
        # groups は { "NDC_913": [isbn1, isbn2...], "NDC_400": [...] } のような形式を想定
        groups = groups_from_neo4j()
        if not groups:
            logger.warning("Neo4jから書籍グループを取得できませんでした。")
            return
        
        design = db.query(ShelfDesign).first()

        # 2. 座標計算
        new_positions = calc_shelf_position(groups, design.books_per_shelf)
        # 重複排除（book_idをキーにして最新の座標を保持）
        unique_map = {pos["isbn"]: pos for pos in new_positions}
        
        # 3. SQLiteへの反映（トランザクション内で実行）
        # delete() と insert をセットで行う
        db.query(ShelfLayout).delete()

        # バルクインサート用のデータ準備
        bulk_data = [
            {
                "isbn": pos['isbn'],
                "x": pos['row'],  # 座標計算の結果をマッピング
                "y": pos['col'],
                "books_per_shelf": books_per_shelf,
                "total_shelves": total_shelves
            }
            for pos in unique_map.values()
        ]

        if bulk_data:
            db.bulk_insert_mappings(ShelfLayout, bulk_data)
        
        db.commit()
        logger.info(f"本棚レイアウトを再構築しました: {len(bulk_data)} 冊")

    except Exception as e:
        db.rollback()
        logger.error(f"本棚再構築中にエラーが発生しました: {e}")
        raise e