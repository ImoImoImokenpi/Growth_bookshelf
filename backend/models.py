from sqlalchemy import Column, Integer, String, Float, JSON
from database import Base

class MyHand(Base):
    __tablename__ = "myhand"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    isbn = Column(String, unique=True, index=True, nullable=False)  # Google Books API の ID
    title = Column(String)
    authors = Column(String)
    cover = Column(String)

class ShelfLayout(Base):
    __tablename__ = "shelflayout"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    isbn = Column(String, unique=True, index=True)  # Google Books API の ID
    x = Column(Integer)
    y = Column(Integer)
    books_per_shelf = Column(Integer)

class ShelfDesign(Base):
    __tablename__ = "shelfdesign"
    id = Column(Integer, primary_key=True, index=True)
    books_per_shelf = Column(Integer)
    total_shelves = Column(Integer)