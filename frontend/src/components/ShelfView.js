import React, { useEffect, useRef, useContext, useMemo, useState, useCallback } from "react";
import * as d3 from "d3";
import { MyBookshelfContext } from "../context/MyBookshelfContext";

function ShelfView() {
  const { myBookshelf, fetchBookshelf, updateShelfLayout, addShelfRow, removeShelfRow } =
    useContext(MyBookshelfContext);
  const svgRef = useRef();

  // --- 1. 定数設定 ---
  const BOOK_WIDTH = 80;
  const BOOK_HEIGHT = 120;
  const TOP_GAP = 30;
  const FRAME_THICKNESS = 18;
  const WOOD_URL = "/sources/wood_texture.jpg";
  const DARK_WOOD_URL = "/sources/dark_wood_texture.jpg";

  // --- 2. State管理 ---
  const [inputValue, setInputValue] = useState("");
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedIsbns, setSelectedIsbns] = useState([]);
  const [meaning, setMeaning] = useState("");

  const books = myBookshelf?.books || [];
  const currentBooksPerShelf = myBookshelf?.books_per_shelf || 5;
  const shelfCount = myBookshelf?.total_shelves || 3;

  // --- 3. 座標計算ロジック ---
  const unitShelfHeight = TOP_GAP + BOOK_HEIGHT;
  const WIDTH = currentBooksPerShelf * BOOK_WIDTH + FRAME_THICKNESS * 2;
  const HEIGHT = unitShelfHeight * shelfCount + FRAME_THICKNESS * (shelfCount + 1);
  const modalTopOffset = Math.max(100, HEIGHT / 2 + 100);

  const getPhysPos = useCallback(
    (row, col) => ({
      x: FRAME_THICKNESS + col * BOOK_WIDTH,
      y: FRAME_THICKNESS + row * (unitShelfHeight + FRAME_THICKNESS) + TOP_GAP,
    }),
    [unitShelfHeight]
  );

  const getGridPos = useCallback(
    (pxX, pxY) => ({
      row: Math.max(
        0,
        Math.min(
          Math.floor((pxY - FRAME_THICKNESS) / (unitShelfHeight + FRAME_THICKNESS)),
          shelfCount - 1
        )
      ),
      col: Math.max(
        0,
        Math.min(Math.floor((pxX - FRAME_THICKNESS) / BOOK_WIDTH), currentBooksPerShelf - 1)
      ),
    }),
    [unitShelfHeight, shelfCount, currentBooksPerShelf]
  );

  const booksWithPosition = useMemo(() => {
    return books.map((book) => ({
      ...book,
      ...getPhysPos(book.x || 0, book.y || 0),
    }));
  }, [books, getPhysPos]);

  // --- 4. ライフサイクル ---
  useEffect(() => {
    fetchBookshelf();
  }, []);
  useEffect(() => {
    setInputValue(currentBooksPerShelf);
  }, [currentBooksPerShelf]);

  // --- 5. ハンドラ ---
  const handleCancelSelection = useCallback(() => {
    setIsModalOpen(false);
    setMeaning("");
    setSelectedIsbns([]);
  }, []);

  const handleRemoveRow = async () => {
    try {
      await removeShelfRow();
    } catch (err) {
      alert(err.message || "その段には本があるため削除できません");
    }
  };

  const handleSaveMeaning = async () => {
    if (!meaning.trim()) return alert("意味を入力してください");
    try {
      const res = await fetch("http://localhost:8000/bookshelf/save-concept", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ meaning, isbns: selectedIsbns }),
      });
      if (res.ok) handleCancelSelection();
    } catch (err) {
      console.error("Save Error:", err);
    }
  };

  // --- 6. D3 描画ロジック ---
  useEffect(() => {
    if (!svgRef.current) return;
    const svg = d3.select(svgRef.current).attr("width", WIDTH).attr("height", HEIGHT);
    svg.selectAll("*").remove();

    // 背景・パターン
    const defs = svg.append("defs");
    const createPattern = (id, url, size) => {
      defs
        .append("pattern")
        .attr("id", id)
        .attr("patternUnits", "userSpaceOnUse")
        .attr("width", size)
        .attr("height", size)
        .append("image")
        .attr("href", url)
        .attr("width", size)
        .attr("height", size)
        .attr("preserveAspectRatio", "xMidYMid slice");
    };
    createPattern("woodPattern", WOOD_URL, 300);
    createPattern("darkWoodPattern", DARK_WOOD_URL, 500);
    svg.append("rect").attr("width", WIDTH).attr("height", HEIGHT).attr("fill", "url(#darkWoodPattern)");
    svg.append("rect").attr("width", WIDTH).attr("height", HEIGHT).attr("fill", "rgba(0,0,0,0.2)");

    // 棚板
    for (let i = 0; i <= shelfCount; i++) {
      const yPos = i * (unitShelfHeight + FRAME_THICKNESS);
      svg
        .append("rect")
        .attr("x", 0)
        .attr("y", yPos)
        .attr("width", WIDTH)
        .attr("height", FRAME_THICKNESS)
        .attr("fill", "url(#woodPattern)");
    }
    [0, WIDTH - FRAME_THICKNESS].forEach((x) => {
      svg
        .append("rect")
        .attr("x", x)
        .attr("y", 0)
        .attr("width", FRAME_THICKNESS)
        .attr("height", HEIGHT)
        .attr("fill", "url(#woodPattern)");
    });

    const guide = svg
      .append("rect")
      .attr("width", BOOK_WIDTH)
      .attr("height", BOOK_HEIGHT)
      .attr("fill", "rgba(255, 255, 255, 0.2)")
      .attr("stroke", "#fff")
      .attr("stroke-width", 2)
      .attr("stroke-dasharray", "8,4")
      .style("visibility", "hidden")
      .attr("rx", 6);

    // ドラッグハンドラー (連鎖押し出しロジック)
    const dragHandler = d3
      .drag()
      .on("start", function (event) {
        if (isModalOpen) return;
        const me = d3.select(this);
        me.attr("data-offsetX", event.x - parseFloat(me.attr("x"))).attr(
          "data-offsetY",
          event.y - parseFloat(me.attr("y"))
        );
        me.raise().transition().duration(200).attr("width", BOOK_WIDTH * 1.05);
        guide.style("visibility", "visible");
      })
      .on("drag", function (event, d) {
        if (isModalOpen) return;
        const me = d3.select(this);
        const newX = event.x - +me.attr("data-offsetX");
        const newY = event.y - +me.attr("data-offsetY");
        me.attr("x", newX).attr("y", newY);

        const target = getGridPos(newX + BOOK_WIDTH / 2, newY + BOOK_HEIGHT / 2);
        const virtualPositions = new Map(
          books.filter((b) => b.isbn !== d.isbn).map((b) => [b.isbn, { x: b.x, y: b.y }])
        );

        // --- 再帰的な押し出しチェック（左右対応） ---
        const findIntruder = (row, col) => {
          return Array.from(virtualPositions.entries()).find(([isbn, pos]) => pos.x === row && pos.y === col);
        };

        const pushRight = (row, col) => {
          const intruder = findIntruder(row, col);
          if (!intruder) return true;

          const [isbn, pos] = intruder;
          if (pos.y + 1 >= currentBooksPerShelf) return false; // 右端ガード

          if (pushRight(row, pos.y + 1)) {
            virtualPositions.set(isbn, { x: pos.x, y: pos.y + 1 });
            return true;
          }
          return false;
        };

        const pushLeft = (row, col) => {
          const intruder = findIntruder(row, col);
          if (!intruder) return true;

          const [isbn, pos] = intruder;
          if (pos.y - 1 < 0) return false; // 左端ガード

          if (pushLeft(row, pos.y - 1)) {
            virtualPositions.set(isbn, { x: pos.x, y: pos.y - 1 });
            return true;
          }
          return false;
        };

        // ★ドラッグ方向で優先押し出し方向を切り替える（右ドラッグ→右優先、左ドラッグ→左優先）
        const dragDx = event.dx;

        const canPush =
          dragDx >= 0
            ? pushRight(target.row, target.col) || pushLeft(target.row, target.col)
            : pushLeft(target.row, target.col) || pushRight(target.row, target.col);

        if (canPush) {
          const snap = getPhysPos(target.row, target.col);
          guide.attr("x", snap.x).attr("y", snap.y).style("visibility", "visible");

          svg
            .selectAll(".book-group")
            .filter((other) => other.isbn !== d.isbn)
            .each(function (other) {
              const vPos = virtualPositions.get(other.isbn);
              const phys = getPhysPos(vPos.x, vPos.y);
              d3.select(this)
                .interrupt()
                .transition()
                .duration(200)
                .ease(d3.easeCubicOut)
                .attr("x", phys.x)
                .attr("y", phys.y);
            });
        } else {
          guide.style("visibility", "hidden");
        }
      })
      .on("end", async function (event, d) {
        const me = d3.select(this);
        const target = getGridPos(+me.attr("x") + BOOK_WIDTH / 2, +me.attr("y") + BOOK_HEIGHT / 2);

        let finalPositions = books.map((b) => ({ isbn: b.isbn, x: b.x, y: b.y }));
        const currentD = finalPositions.find((b) => b.isbn === d.isbn);

        const findIntruder = (row, col) => {
          return finalPositions.find((b) => b.isbn !== d.isbn && b.x === row && b.y === col);
        };

        const applyPushRight = (row, col) => {
          const intruder = findIntruder(row, col);
          if (!intruder) return true;

          if (intruder.y + 1 >= currentBooksPerShelf) return false; // 右端ガード
          if (applyPushRight(row, intruder.y + 1)) {
            intruder.y += 1;
            return true;
          }
          return false;
        };

        const applyPushLeft = (row, col) => {
          const intruder = findIntruder(row, col);
          if (!intruder) return true;

          if (intruder.y - 1 < 0) return false; // 左端ガード
          if (applyPushLeft(row, intruder.y - 1)) {
            intruder.y -= 1;
            return true;
          }
          return false;
        };

        // ★ドラッグ方向で優先押し出し方向を切り替える
        const dragDx = event.dx;

        const pushed =
          dragDx >= 0
            ? applyPushRight(target.row, target.col) || applyPushLeft(target.row, target.col)
            : applyPushLeft(target.row, target.col) || applyPushRight(target.row, target.col);

        // 成功した時だけ位置を更新、失敗なら元の位置
        if (pushed) {
          currentD.x = target.row;
          currentD.y = target.col;
        }

        const finalPhys = getPhysPos(currentD.x, currentD.y);
        me.transition()
          .duration(500)
          .ease(d3.easeElasticOut.amplitude(0.8))
          .attr("x", finalPhys.x)
          .attr("y", finalPhys.y)
          .attr("width", BOOK_WIDTH);

        guide.style("visibility", "hidden");

        // ここで唯一の保存処理
        try {
          await fetch("http://localhost:8000/bookshelf/sync-layout", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(finalPositions),
          });
          fetchBookshelf();
        } catch (err) {
          console.error(err);
          fetchBookshelf();
        }
      });

    // 本の初期配置
    svg
      .selectAll(".book-group")
      .data(booksWithPosition, (d) => d.isbn)
      .join("image")
      .attr("class", "book-group")
      .attr("href", (d) => d.cover)
      .attr("x", (d) => d.x)
      .attr("y", (d) => d.y)
      .attr("width", BOOK_WIDTH)
      .attr("height", BOOK_HEIGHT)
      .attr("preserveAspectRatio", "xMidYMid slice")
      .style("cursor", "grab")
      .call(dragHandler);
  }, [
    booksWithPosition,
    WIDTH,
    HEIGHT,
    shelfCount,
    getGridPos,
    getPhysPos,
    isModalOpen,
    currentBooksPerShelf,
    books,
    fetchBookshelf,
  ]);

  return (
    <div style={containerStyle}>
      <div style={toolbarStyle}>
        <div style={toolGroupStyle}>
          <label style={labelStyle}>段の収容数</label>
          <div style={{ display: "flex", gap: "8px" }}>
            <input
              type="number"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              style={inputStyle}
            />
            <button onClick={() => updateShelfLayout(parseInt(inputValue))} style={primaryButtonStyle}>
              適用
            </button>
          </div>
        </div>
        <div style={dividerStyle} />
        <div style={toolGroupStyle}>
          <label style={labelStyle}>棚の管理</label>
          <div style={{ display: "flex", gap: "10px" }}>
            <button onClick={addShelfRow} style={secondaryButtonStyle}>
              + 段を追加
            </button>
            <button onClick={handleRemoveRow} style={dangerButtonStyle}>
              − 段を削除
            </button>
          </div>
        </div>
      </div>

      <div style={shelfWrapperStyle}>
        <svg ref={svgRef} style={svgStyle} />
      </div>

      {isModalOpen && (
        <div style={{ ...modalOverlayStyle, alignItems: "flex-start", paddingTop: `${modalTopOffset}px` }}>
          <div style={modalContentStyle}>
            <h3 style={{ margin: "0 0 10px 0" }}>{selectedIsbns.length} 冊を選択中</h3>
            <p style={{ color: "#666", fontSize: "14px", marginBottom: "20px" }}>
              本の「意味」を入力してください。
            </p>
            <input
              autoFocus
              type="text"
              value={meaning}
              onChange={(e) => setMeaning(e.target.value)}
              placeholder="例: プログラミングの基礎"
              style={modalInputStyle}
            />
            <div style={{ display: "flex", gap: "12px", marginTop: "25px", justifyContent: "flex-end" }}>
              <button onClick={handleCancelSelection} style={secondaryButtonStyle}>
                キャンセル
              </button>
              <button onClick={handleSaveMeaning} style={primaryButtonStyle}>
                保存する
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// --- Styles ---
const containerStyle = {
  padding: "60px 20px",
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  backgroundColor: "#f4f1ea",
  minHeight: "100vh",
  fontFamily: "'Inter', sans-serif",
};
const toolbarStyle = {
  display: "flex",
  alignItems: "center",
  gap: "30px",
  padding: "15px 30px",
  backgroundColor: "rgba(255, 255, 255, 0.8)",
  backdropFilter: "blur(10px)",
  borderRadius: "20px",
  boxShadow: "0 10px 30px rgba(0,0,0,0.05)",
  marginBottom: "40px",
  border: "1px solid rgba(255,255,255,0.3)",
};
const toolGroupStyle = { display: "flex", flexDirection: "column", gap: "6px" };
const labelStyle = { fontSize: "12px", fontWeight: "bold", color: "#888", textTransform: "uppercase" };
const inputStyle = {
  width: "60px",
  padding: "8px",
  borderRadius: "8px",
  border: "1px solid #ddd",
  textAlign: "center",
  fontSize: "16px",
  fontWeight: "600",
};
const primaryButtonStyle = {
  padding: "10px 20px",
  backgroundColor: "#2c3e50",
  color: "#fff",
  border: "none",
  borderRadius: "10px",
  cursor: "pointer",
  fontWeight: "600",
};
const secondaryButtonStyle = { ...primaryButtonStyle, backgroundColor: "#fff", color: "#2c3e50", border: "1px solid #ddd" };
const dangerButtonStyle = { ...secondaryButtonStyle, color: "#e53935" };
const dividerStyle = { width: "1px", height: "40px", backgroundColor: "#eee" };
const shelfWrapperStyle = { perspective: "1000px" };
const svgStyle = { boxShadow: "0 50px 100px rgba(0,0,0,0.2), 0 15px 35px rgba(0,0,0,0.1)", borderRadius: "4px" };
const modalOverlayStyle = {
  position: "fixed",
  top: 0,
  left: 0,
  width: "100vw",
  height: "100vh",
  backgroundColor: "rgba(0,0,0,0.4)",
  backdropFilter: "blur(4px)",
  display: "flex",
  justifyContent: "center",
  alignItems: "center",
  zIndex: 9999,
};
const modalContentStyle = { backgroundColor: "#fff", padding: "30px", borderRadius: "24px", width: "420px", boxShadow: "0 25px 50px rgba(0,0,0,0.3)" };
const modalInputStyle = {
  width: "100%",
  padding: "15px",
  borderRadius: "12px",
  border: "2px solid #eee",
  fontSize: "16px",
  outline: "none",
  boxSizing: "border-box",
};

export default ShelfView;
