class SystemPrincipal:
    """The authenticated caller of an API key (Boomi). Not a User: no pk, no staff rights."""

    is_authenticated = True
    is_anonymous = False
    is_active = True
    is_staff = False
    is_superuser = False
    pk = None
    id = None
    role = "SYSTEM"

    def __init__(self, api_key):
        self.api_key = api_key
        self.tenant = api_key.tenant
        self.tenant_id = api_key.tenant_id

    def __str__(self):
        return "SYSTEM"
