from .plugin import FloodPlugin

def classFactory(iface):
    return FloodPlugin(iface)
