from abc import ABC, abstractmethod

from sqlalchemy.orm import Session

from app.models import Product, Provider, QuoteSnapshot
from app.schemas.recommend import NormalizedInquiry


class ProviderAdapter(ABC):
    provider_code: str
    product_line: str

    @abstractmethod
    def get_provider(self, session: Session) -> Provider:
        raise NotImplementedError

    @abstractmethod
    def get_product(self, session: Session) -> Product:
        raise NotImplementedError

    @abstractmethod
    def find_matching_snapshots(
        self, session: Session, normalized: NormalizedInquiry
    ) -> list[QuoteSnapshot]:
        raise NotImplementedError
