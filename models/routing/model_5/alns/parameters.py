"""Calendar search configuration for M5."""
from dataclasses import dataclass

from models.routing.alns.search_control import ALNSParameters as SearchParameters


@dataclass(frozen=True)
class ALNSParameters(SearchParameters):
    calendar_reconstruction_period: int = 1


def validate_calendar_reconstruction_period(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError('calendar_reconstruction_period must be a positive integer')
    return value
