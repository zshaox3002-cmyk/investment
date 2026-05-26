"""Domain exceptions."""


class InvestmentError(Exception):
    """Base for all domain errors."""


class DBError(InvestmentError):
    """Database-related error."""


class MigrationError(InvestmentError):
    """Migration-related error."""


class ParseError(InvestmentError):
    """Document parsing error."""
