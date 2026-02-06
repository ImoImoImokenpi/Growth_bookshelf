# 

# schemas.py
from pydantic import BaseModel
from typing import List, Optional


class BookBase(BaseModel):
    book_id: str
    title: str
    authors: Optional[List[str]] = None
    isbn: Optional[str] = None
    published_date: Optional[str] = None
    description: Optional[str] = None
    thumbnail: Optional[str] = None


class BookWithConcepts(BookBase):
    concepts: List[str] = []


class AddFromHandRequest(BaseModel):
    isbns: List[str]
