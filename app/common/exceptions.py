class APIError(Exception):
    pass


class AuthenticationError(Exception):
    pass


class ConfigurationError(Exception):
    pass


class NullValueError(ValueError):
    pass
