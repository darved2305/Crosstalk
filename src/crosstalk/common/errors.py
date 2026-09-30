class CrosstalkError(Exception):
    pass


class ConfigError(CrosstalkError):
    pass


class CaptureError(CrosstalkError):
    pass


class ReconstructionError(CrosstalkError):
    pass


class ValidationError(CrosstalkError):
    pass


class UpstreamError(CrosstalkError):
    pass


class OracleAccessError(CrosstalkError):
    pass
