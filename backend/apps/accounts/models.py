from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Project user model. Declared now so AUTH_USER_MODEL never has to be swapped later.

    U-03 adds the `tenant` FK and `role` field (docs/CONTRACTS.md section 2).
    """
