from rest_framework.permissions import BasePermission

class ActiveAccount(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(user.is_authenticated and user.is_active and not user.must_change_password and request.session.get('version') == user.session_version)

class SignedIn(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(user.is_authenticated and user.is_active and request.session.get('version') == user.session_version)

class Admin(ActiveAccount):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.role == 'admin'
