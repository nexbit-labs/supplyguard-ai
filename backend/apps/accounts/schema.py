"""drf-spectacular: show the two auth schemes in the OpenAPI document."""
from drf_spectacular.extensions import OpenApiAuthenticationExtension


class TenantJWTScheme(OpenApiAuthenticationExtension):
    target_class = "apps.accounts.authentication.TenantJWTAuthentication"
    name = "jwtAuth"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}


class ApiKeyScheme(OpenApiAuthenticationExtension):
    target_class = "apps.accounts.authentication.ApiKeyAuthentication"
    name = "apiKeyAuth"

    def get_security_definition(self, auto_schema):
        return {"type": "apiKey", "in": "header", "name": "X-Api-Key"}
