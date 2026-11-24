"""JSON parser that turns a pathologically nested body into a 400 instead of a 500."""
from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser


class SafeJSONParser(JSONParser):
    def parse(self, stream, media_type=None, parser_context=None):
        try:
            return super().parse(stream, media_type, parser_context)
        except RecursionError:
            raise ParseError("JSON body is nested too deeply.") from None
