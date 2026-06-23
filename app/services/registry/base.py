from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class RegistryResult:
    found: bool
    registry: str = ""
    registration_number: str = ""
    legal_name: str = ""
    status: str = ""
    founded: str | None = None
    address: str | None = None
    raw: dict = field(default_factory=dict)
    error: str | None = None


class RegistryVerifier(ABC):
    """Base class for all registry verifiers."""

    @property
    @abstractmethod
    def registry_name(self) -> str: ...

    @property
    @abstractmethod
    def country_code(self) -> str: ...

    @abstractmethod
    async def search(self, company_name: str) -> list[RegistryResult]:
        """Search for a company by name. Returns up to 5 matches."""
        ...

    @abstractmethod
    async def get_by_id(self, registration_number: str) -> RegistryResult | None:
        """Look up a company by its registration number."""
        ...

    def _normalize_name(self, name: str) -> str:
        """Lowercase, strip legal suffixes for fuzzy matching."""
        import re
        suffixes = r"\b(ltd|limited|llc|inc|corp|gmbh|ag|sa|nv|bv|kft|oy|as|ab|plc|kg|ohg|eg|se|tov|pp|fop)\b"
        name = name.lower().strip()
        name = re.sub(suffixes, "", name, flags=re.IGNORECASE)
        return name.strip(" ,.")
