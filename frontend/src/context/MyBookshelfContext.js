import { createContext, useState } from "react";
import axios from "axios";

export const MyBookshelfContext = createContext();

export function MyBookshelfProvider({ children }) {
  const [myBookshelf, setMyBookshelf] = useState([]);

  const fetchBookshelf = async () => {
    try {
      const res = await axios.get("http://localhost:8000/bookshelf/");
      setMyBookshelf(res.data);
    } catch (error) {
      console.error("本棚取得エラー:", error);
    }
  };

  // 2. 段数（1段あたりの冊数）の更新・再構築
  const updateShelfLayout = async (newSize) => {
    // サーバー側のAPIエンドポイントに合わせてパスを変更してください
    await axios.post(`http://localhost:8000/bookshelf/add_per_shelf?books_per_shelf=${newSize}`);
    fetchBookshelf();
  };

  const addShelfRow = async () => {
    await axios.post("http://localhost:8000/bookshelf/add_shelves");
    fetchBookshelf();
  };

  const removeShelfRow = async () => {
    try {
      await axios.post("http://localhost:8000/bookshelf/remove_shelves");
      fetchBookshelf();
    } catch (error) {
      // 400エラー（これ以上減らせない等）をアラートで表示
      alert(error.response?.data?.detail || "段数を減らせませんでした");
    }
  };

  return (
    <MyBookshelfContext.Provider value={{ myBookshelf, fetchBookshelf, updateShelfLayout, addShelfRow, removeShelfRow }}>
      {children}
    </MyBookshelfContext.Provider>
  );
}
