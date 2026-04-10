from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages


def role_required(role):
    """Decorator that restricts view access to users with the specified role."""
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            profile = getattr(request.user, 'userprofile', None)
            if not profile or profile.role != role:
                messages.error(request, "You don't have permission to access that page.")
                return redirect('home')
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


def supplier_required(view_func):
    return role_required('supplier')(view_func)


def rider_required(view_func):
    return role_required('rider')(view_func)


def owner_required(view_func):
    return role_required('owner')(view_func)
