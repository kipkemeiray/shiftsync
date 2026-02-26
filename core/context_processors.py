# Re-export context processors from middleware module for settings compatibility
from datetime import datetime

from core.middleware import unread_notification_count  


def global_context(request):
    """
    Provides global context variables for templates.
    """
    return {
        "year": datetime.now().year,
        "user_role": getattr(request.user, "role", None) if request.user.is_authenticated else None,
    }
