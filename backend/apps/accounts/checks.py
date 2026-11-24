import os

from django.conf import settings
from django.core.checks import Tags, Warning, register


@register(Tags.security, deploy=False)
def num_proxies_check(app_configs, **kwargs):
    """Without NUM_PROXIES the per-IP throttles see only the proxy's address (or, if guessed
    wrongly, a spoofable one). Warn in production-like runs where it was left unset."""
    if settings.DEBUG or "NUM_PROXIES" in os.environ:
        return []
    return [
        Warning(
            "NUM_PROXIES is not set. Set it to the number of trusted reverse proxies in front "
            "of Django (1 behind the U-12 proxy, 0 for none) so login throttles and failed-key "
            "counters use the real client address.",
            id="accounts.W001",
        )
    ]
