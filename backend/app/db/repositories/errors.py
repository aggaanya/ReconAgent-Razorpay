"""Repository errors."""


class RepositoryError(Exception):
    """Base class for data-access failures."""


class DuplicateRecordError(RepositoryError):
    """A record with the same provider-unique id already exists."""

    def __init__(self, entity: str, unique_field: str, unique_value: str) -> None:
        self.entity = entity
        self.unique_field = unique_field
        self.unique_value = unique_value
        super().__init__(
            f"{entity} with {unique_field}={unique_value!r} already exists"
        )
