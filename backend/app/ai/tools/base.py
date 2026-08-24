"""Shared contract for the Finance Tools layer.

Finance Tools are a controlled adapter between the deterministic Finance
Intelligence Engine (``app.services.metrics``) and the future AI
orchestration layer. They perform **no financial calculation of their
own** — each tool calls exactly one ``FinanceService`` method and returns
that method's response verbatim inside a JSON-serializable envelope.

Guarantees enforced here (in one place, for every tool):

- Inputs are validated Pydantic models; raw dicts are coerced once at the
  boundary, everything else is rejected.
- The caller supplies the SQLAlchemy session per invocation — tools never
  create, store, or expose sessions, and never touch repositories.
- Any unexpected engine/repository failure becomes a typed, secret-free
  :class:`FinanceEngineError`; the original exception stays chained as
  ``__cause__`` for server-side logs only. SQL text, connection strings,
  and credentials never reach the message.
- Output is plain JSON-native data (Pydantic ``model_dump(mode="json")``
  plus a hard ``json.dumps`` round-trip check) — safe to hand to the LLM
  layer verbatim.

Error messages are concise and machine-readable: ``code``, ``tool``, and
a short human string.
"""

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, ClassVar, Mapping

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core.periods import REPORTING_TIMEZONE, named_period
from app.db.session import DatabaseNotConfiguredError

logger = logging.getLogger(__name__)


# --- typed, machine-readable errors -----------------------------------------


class FinanceToolError(Exception):
    """Base class for all Finance Tool failures."""

    code: ClassVar[str] = "tool_error"

    def __init__(self, message: str, *, tool: str | None = None) -> None:
        super().__init__(message)
        self.tool = tool


class InvalidToolInputError(FinanceToolError):
    """Input failed validation (unknown period/metric, bad dates, ...)."""

    code: ClassVar[str] = "invalid_input"


class UnknownToolError(FinanceToolError):
    """A tool name was requested that the registry does not define."""

    code: ClassVar[str] = "unknown_tool"


class FinanceEngineError(FinanceToolError):
    """The FinanceService call failed; details are sanitized.

    The message never includes exception text from lower layers (which can
    carry SQL or DSN material); only the exception *class name* is kept for
    triage. The original exception remains available via ``__cause__`` for
    server-side logging.
    """

    code: ClassVar[str] = "engine_error"


def resolve_named_period(period: str) -> tuple[datetime, datetime]:
    """Named preset -> half-open ``(start, end)`` in the reporting tz.

    Same IST clock policy as the finance API (spec §6.2): all calendar
    math comes from :func:`app.core.periods.named_period` — this adapter
    adds no date logic of its own.
    """
    return named_period(period, datetime.now(tz=REPORTING_TIMEZONE))


# --- tool base ---------------------------------------------------------------


class FinanceTool(ABC):
    """One finance capability exposed to the AI orchestration layer.

    Subclasses declare identity metadata plus two hooks:

    - ``_fetch(session, params)``: call exactly one ``FinanceService``
      method with parameters derived from the validated input.
    - ``_render(result)``: project the engine response into the output
      envelope (verbatim fields; no arithmetic).

    ``run()`` wraps both with input coercion, session guarding, error
    sanitization, and the JSON-safety guarantee.

    ``requires_session`` marks tools whose data source needs no database
    (e.g. ``reconcile_transactions`` runs over caller-supplied synthetic
    records). The default ``True`` preserves the historical contract for
    every database-backed tool.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    input_model: ClassVar[type[BaseModel]]
    requires_session: ClassVar[bool] = True

    def run(self, session: Session | None, payload: object) -> dict[str, Any]:
        """Execute the tool and return a JSON-serializable dict."""
        if self.requires_session and session is None:
            raise InvalidToolInputError(
                "A database session is required to run finance tools",
                tool=self.name,
            )
        params = self._coerce(payload)
        try:
            result = self._fetch(session, params)
        except FinanceToolError:
            raise
        except DatabaseNotConfiguredError as exc:
            # Its message is a deliberate public-facing string (no DSN).
            raise FinanceEngineError(str(exc), tool=self.name) from exc
        except Exception as exc:
            logger.error(
                "Finance tool %r failed", self.name, exc_info=True
            )
            raise FinanceEngineError(
                f"Finance engine failed while running '{self.name}' "
                f"({type(exc).__name__}).",
                tool=self.name,
            ) from exc
        return self._finalize(self._render(result))

    # --- hooks for subclasses ------------------------------------------------

    @abstractmethod
    def _fetch(self, session: Session, params: BaseModel) -> Any:
        """Call one FinanceService method; return its response model."""

    @abstractmethod
    def _render(self, result: Any) -> dict[str, Any]:
        """Envelope body: context keys + ``data`` (all JSON-native)."""

    # --- plumbing --------------------------------------------------------------

    def _coerce(self, payload: object) -> BaseModel:
        if isinstance(payload, self.input_model):
            return payload
        if isinstance(payload, Mapping):
            try:
                return self.input_model.model_validate(dict(payload))
            except ValidationError as exc:
                raise InvalidToolInputError(
                    self._validation_message(exc), tool=self.name
                ) from exc
        raise InvalidToolInputError(
            f"Expected {self.input_model.__name__} or a mapping, got "
            f"{type(payload).__name__}",
            tool=self.name,
        )

    def _validation_message(self, exc: ValidationError) -> str:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ()))
        detail = f"{location}: {first.get('msg')}" if location else first.get("msg", "")
        return f"Invalid input for '{self.name}' ({detail})"

    def _finalize(self, rendered: dict[str, Any]) -> dict[str, Any]:
        envelope = {"tool": self.name, **rendered}
        try:
            json.dumps(envelope)
        except (TypeError, ValueError) as exc:
            logger.error(
                "Finance tool %r produced non-serializable output", self.name
            )
            raise FinanceEngineError(
                f"Tool '{self.name}' produced non-serializable output.",
                tool=self.name,
            ) from exc
        return envelope
