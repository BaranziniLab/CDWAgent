"""CDWAgent database connector."""
__version__ = "0.6.0"
__all__ = ['create_cdw_server', 'main', 'CDWConfig', '__version__']


def __getattr__(name):
    if name in __all__:
        from . import server
        return getattr(server, name)
    raise AttributeError(name)
