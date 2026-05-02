from app.core.exceptions.base import PermissionDeniedError


class PathTraversalError(PermissionDeniedError):
    type = "path-traversal"
    title = "路径穿越"
