"""Shared read-only policy for interactive tools and durable jobs."""
from .jobs import validate_query


class ClinicalQueryValidator:
    @staticmethod
    def is_read_only_clinical_query(query: str) -> bool:
        try:
            validate_query(query, 'sql')
            return True
        except ValueError:
            return False
