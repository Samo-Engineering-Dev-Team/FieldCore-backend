from fastapi import HTTPException, status


class BadRequestException(HTTPException):
    """
    Exception raised for HTTP 400 Bad Request errors.
    This exception is thrown when the client sends a malformed request that the server
    cannot process due to client error (e.g., invalid parameters, missing required fields).
    Attributes:
        status_code: HTTP status code 400 (Bad Request)
        message: Description of the bad request error
        headers: Optional dictionary of HTTP headers to include in the response
    """

    def __init__(
        self, message: str = "bad request", headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(status.HTTP_400_BAD_REQUEST, message, headers)


class UnauthorizedException(HTTPException):
    """
    Exception raised for HTTP 401 Unauthorized errors.
    This exception is thrown when authentication is required but has failed or has not been provided.
    Attributes:
        status_code: HTTP status code 401 (Unauthorized)
        message: Description of the authentication error
        headers: Optional dictionary of HTTP headers to include in the response
    """

    def __init__(
        self, message: str = "unauthorized", headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(status.HTTP_401_UNAUTHORIZED, message, headers)


class ForbiddenException(HTTPException):
    """
    Exception raised for HTTP 403 Forbidden errors.
    This exception is thrown when the client is authenticated but does not have permission
    to access the requested resource.
    Attributes:
        status_code: HTTP status code 403 (Forbidden)
        message: Description of the forbidden access error
        headers: Optional dictionary of HTTP headers to include in the response
    """

    def __init__(
        self, message: str = "forbidden", headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(status.HTTP_403_FORBIDDEN, message, headers)


class NotFoundException(HTTPException):
    """
    Exception raised for HTTP 404 Not Found errors.
    This exception is thrown when the requested resource cannot be found on the server.
    Attributes:
        status_code: HTTP status code 404 (Not Found)
        message: Description of the not found error
        headers: Optional dictionary of HTTP headers to include in the response
    """

    def __init__(
        self, message: str = "not found", headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(status.HTTP_404_NOT_FOUND, message, headers)


class RequestTimeoutException(HTTPException):
    """
    Exception raised for HTTP 408 Request Timeout errors.
    This exception is thrown when the server times out waiting for the client's request.
    Attributes:
        status_code: HTTP status code 408 (Request Timeout)
        message: Description of the timeout error
        headers: Optional dictionary of HTTP headers to include in the response
    """

    def __init__(
        self, message: str = "request timeout", headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(status.HTTP_408_REQUEST_TIMEOUT, message, headers)


class ConflictException(HTTPException):
    """
    Exception raised for HTTP 409 Conflict errors.
    This exception is thrown when the request conflicts with the current state of the server
    (e.g., duplicate resource, version conflicts).
    Attributes:
        status_code: HTTP status code 409 (Conflict)
        message: Description of the conflict error
        headers: Optional dictionary of HTTP headers to include in the response
    """

    def __init__(
        self, message: str = "conflict", headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(status.HTTP_409_CONFLICT, message, headers)


class TooManyRequestsException(HTTPException):
    """
    Exception raised for HTTP 429 Too Many Requests errors.
    This exception is thrown when the client has sent too many requests in a given amount of time
    (rate limiting).
    Attributes:
        status_code: HTTP status code 429 (Too Many Requests)
        message: Description of the rate limit error
        headers: Optional dictionary of HTTP headers to include in the response
    """

    def __init__(
        self, message: str = "too many requests", headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(status.HTTP_429_TOO_MANY_REQUESTS, message, headers)


class FormValidationException(HTTPException):
    """
    Exception raised for HTTP 422 Unprocessable Entity errors when a form
    submission fails server-side validation against its template.

    The detail carries a structured per-field error map so clients can map
    messages back to the fields that produced them. Rendered consistently
    with FastAPI's RequestValidationError handler (also 422).
    Attributes:
        status_code: HTTP status code 422 (Unprocessable Entity)
        errors: Mapping of field key -> list of error messages
    """

    def __init__(
        self,
        errors: dict[str, list[str]],
        message: str = "form submission validation failed",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.errors = errors
        super().__init__(
            422,  # Unprocessable Content (literal avoids deprecated starlette constant)
            {"message": message, "errors": errors},
            headers,
        )


class InternalServerErrorException(HTTPException):
    """
    Exception raised for HTTP 500 Internal Server Error errors.
    This exception is thrown when the server encounters an unexpected condition that prevents
    it from fulfilling the request.
    Attributes:
        status_code: HTTP status code 500 (Internal Server Error)
        message: Description of the server error
        headers: Optional dictionary of HTTP headers to include in the response
    """

    def __init__(
        self,
        message: str = "internal server error",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status.HTTP_500_INTERNAL_SERVER_ERROR, message, headers)


class NotImplementedException(HTTPException):
    """
    Exception raised for HTTP 501 Not Implemented errors.
    This exception is thrown when the server does not support the functionality required
    to fulfill the request.
    Attributes:
        status_code: HTTP status code 501 (Not Implemented)
        message: Description of the not implemented error
        headers: Optional dictionary of HTTP headers to include in the response
    """

    def __init__(
        self, message: str = "not implemented", headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(status.HTTP_501_NOT_IMPLEMENTED, message, headers)


class InsufficientStorageException(HTTPException):
    """
    Exception raised for HTTP 507 Insufficient Storage errors.
    This exception is thrown when the server is unable to store the representation needed
    to complete the request.
    Attributes:
        status_code: HTTP status code 507 (Insufficient Storage)
        message: Description of the insufficient storage error
        headers: Optional dictionary of HTTP headers to include in the response
    """

    def __init__(
        self,
        message: str = "insufficient storage",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status.HTTP_507_INSUFFICIENT_STORAGE, message, headers)
