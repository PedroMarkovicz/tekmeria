"""Errors the tools turn into `invalid_input` results."""


class InvalidInputError(ValueError):
    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint
