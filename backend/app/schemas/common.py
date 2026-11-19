"""Shared response envelopes."""
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    page_size: int = 50

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.page_size))


class Message(BaseModel):
    detail: str


class CountByKey(BaseModel):
    key: str
    label: str
    count: int
