"""Teacher-distillation Search: one worker serves the teacher, grows the corpus, trains, and scores."""

from __future__ import annotations

from .search import TeacherDistillSearch

SEARCH_TYPE = TeacherDistillSearch

__all__ = ["SEARCH_TYPE", "TeacherDistillSearch"]
