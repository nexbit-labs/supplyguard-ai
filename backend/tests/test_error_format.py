import uuid

import pytest
from rest_framework import exceptions
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView


class Conflict(exceptions.APIException):
    status_code = 409
    default_detail = "Invalid state transition."


def make_view(exc=None, permission=AllowAny):
    class View(APIView):
        permission_classes = [permission]

        def get(self, request):
            if exc is not None:
                raise exc
            return None

    return View.as_view()


def call(view, **headers):
    return view(APIRequestFactory().get("/x", **headers))


def error_of(response):
    assert set(response.data) == {"error"}
    error = response.data["error"]
    assert set(error) == {"code", "message", "details", "correlation_id"}
    uuid.UUID(error["correlation_id"])  # well-formed
    return error


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (exceptions.ValidationError({"qty": ["Required."]}), 400, "VALIDATION_ERROR"),
        (exceptions.PermissionDenied(), 403, "FORBIDDEN"),
        (exceptions.NotFound(), 404, "NOT_FOUND"),
        (Conflict(), 409, "CONFLICT"),
        (exceptions.MethodNotAllowed("PUT"), 405, "VALIDATION_ERROR"),
    ],
)
def test_errors_use_contract_body(exc, status, code):
    response = call(make_view(exc))
    assert response.status_code == status
    assert error_of(response)["code"] == code


def test_validation_details_are_preserved():
    response = call(make_view(exceptions.ValidationError({"qty": ["Required."]})))
    assert error_of(response)["details"] == {"qty": ["Required."]}


def test_unauthenticated_is_401_with_contract_body():
    response = call(make_view(permission=IsAuthenticated))
    assert response.status_code == 401
    assert error_of(response)["code"] == "UNAUTHENTICATED"


def test_correlation_id_is_echoed_when_valid():
    cid = str(uuid.uuid4())
    response = call(make_view(exceptions.NotFound()), HTTP_X_CORRELATION_ID=cid)
    assert error_of(response)["correlation_id"] == cid


def test_bad_correlation_id_is_replaced():
    response = call(make_view(exceptions.NotFound()), HTTP_X_CORRELATION_ID="not-a-uuid")
    assert error_of(response)["correlation_id"] != "not-a-uuid"


def test_request_correlation_id_beats_header():
    """U-05 middleware sets request.correlation_id; the handler must reuse it."""
    mw_id, header_id = str(uuid.uuid4()), str(uuid.uuid4())
    http_request = APIRequestFactory().get("/x", HTTP_X_CORRELATION_ID=header_id)
    http_request.correlation_id = mw_id

    class View(APIView):
        permission_classes = [AllowAny]

        def get(self, request):
            raise exceptions.NotFound()

    response = View.as_view()(http_request)
    assert error_of(response)["correlation_id"] == mw_id


def test_list_validation_error_details_are_wrapped():
    response = call(make_view(exceptions.ValidationError(["bad", "worse"])))
    assert response.status_code == 400
    assert error_of(response)["details"] == {"errors": ["bad", "worse"]}


def test_dict_detail_api_exception_keeps_message_and_empty_details():
    class Teapot(exceptions.APIException):
        status_code = 409
        default_detail = "Short and stout."

    response = call(make_view(Teapot({"reason": "x"})))
    error = error_of(response)
    assert error["code"] == "CONFLICT"
    assert error["details"] == {}
    assert error["message"]
