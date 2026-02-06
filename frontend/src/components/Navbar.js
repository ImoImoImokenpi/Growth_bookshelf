import { useState, useContext, useEffect } from "react";
import { MyHandContext } from "../context/MyHandContext";
import HandPanel from "./HandPanel";
import { Link, useLocation } from "react-router-dom";
import axios from "axios";

export default function Navbar() {
    const { myHand } = useContext(MyHandContext);
    const [isOpen, setIsOpen] = useState(false);
    const [setHandBooks] = useState([]);

    const location = useLocation();
    const currentPath = location.pathname;

    const links = [
        { path: "/", label: "本棚空間" },
        { path: "/Search", label: "本を探す" },
        // { path: "/KnowledgeGraph", label: "可視化用" },
    ];

    // ★ パネルが開いた瞬間に API を叩く
    useEffect(() => {
        if (isOpen) {
            axios.get("http://localhost:8000/books/myhand")
                .then(res => setHandBooks(res.data))
                .catch(err => console.error(err));
        }
    }, [isOpen]);

    return (
        <>
            {/* 1つの Navbar に統合 */}
            <nav
                style={{
                    display: "flex",
                    justifyContent: "space-between", // 左と右に分割！
                    alignItems: "center",
                    padding: "10px 20px",
                    backgroundColor: "#f8f9fa",
                    boxShadow: "0 4px 6px rgba(0,0,0,0.1)",
                    marginBottom: "20px",
                }}
            >
                {/* 左側リンク一覧 */}
                <div style={{ display: "flex", gap: "10px" }}>
                    {links
                        .filter((link) => link.path !== currentPath)
                        .map((link) => (
                            <Link
                                key={link.path}
                                to={link.path}
                                style={{
                                    textDecoration: "none",
                                    padding: "8px 16px",
                                    borderRadius: "8px",
                                    backgroundColor: "#e9ecef",
                                    color: "#495057",
                                    fontWeight: "500",
                                    transition: "0.2s",
                                }}
                                onMouseEnter={(e) =>
                                    (e.target.style.backgroundColor = "#dee2e6")
                                }
                                onMouseLeave={(e) =>
                                    (e.target.style.backgroundColor = "#e9ecef")
                                }
                            >
                                {link.label}
                            </Link>
                        ))}
                </div>

                {/* 右上：手元（カート） */}
                <div
                    style={{
                        position: "relative",
                        cursor: "pointer",
                        fontSize: "18px",
                        paddingRight: "10px",
                    }}
                    onClick={() => setIsOpen(true)}
                >
                    📖 手元
                    <span
                        style={{
                            position: "absolute",
                            top: "-8px",
                            right: "-8px",
                            background: "#ff9900",
                            color: "#fff",
                            borderRadius: "50%",
                            padding: "3px 7px",
                            fontSize: "12px",
                        }}
                    >
                        {myHand.length}
                    </span>
                </div>
            </nav>

            {/* 右からスライドして出てくるパネル */}
            <HandPanel
                isOpen={isOpen}
                onClose={() => setIsOpen(false)}
            />
        </>
    );
}
