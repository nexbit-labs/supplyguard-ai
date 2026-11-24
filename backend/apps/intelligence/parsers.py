"""JSON parser for the analysis endpoint: bad or hostile JSON is a clean 400 `INVALID_JSON`."""
from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser

from .exceptions import InvalidJSON

MAX_JSON_DEPTH = 64


def nesting_depth(value, limit: int = MAX_JSON_DEPTH) -> int:
    """Depth of nested lists / dicts, counted without recursion; stops once `limit` is passed."""
    deepest = 0
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, dict):
            children = item.values()
        elif isinstance(item, list):
            children = item
        else:
            continue
        deepest = max(deepest, depth)
        if deepest > limit:
            return deepest
        stack.extend((child, depth + 1) for child in children)
    return deepest


class StrictJSONParser(JSONParser):
    def parse(self, stream, media_type=None, parser_context=None):
        try:
            data = super().parse(stream, media_type, parser_context)
        except (ParseError, RecursionError, ValueError) as exc:
            raise InvalidJSON("The request body is not valid JSON.") from exc
        if nesting_depth(data) > MAX_JSON_DEPTH:
            raise InvalidJSON(f"The JSON is nested more than {MAX_JSON_DEPTH} levels deep.")
        return data
