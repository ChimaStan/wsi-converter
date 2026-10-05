"""Domain exceptions raised by wsi_converter."""


class WSIConverterError(Exception):
    """Base class for expected, user-actionable application errors."""


class InputError(WSIConverterError):
    """An input path is missing, inaccessible, or unsuitable for the operation."""


class OpenSlideError(WSIConverterError):
    """OpenSlide could not perform a requested image operation."""


class BackendError(WSIConverterError):
    """An external backend could not be located or run successfully."""


class InspectionError(BackendError):
    """Bio-Formats inspection failed or returned metadata that could not be parsed."""


class SelectionError(WSIConverterError):
    """A series could not be selected safely from the inspection results."""


class ConversionError(BackendError):
    """Bio-Formats conversion failed or could not create the requested output."""
