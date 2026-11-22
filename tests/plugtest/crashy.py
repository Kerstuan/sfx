PLUGIN = {"id": "crashy", "name": "Crashy", "version": "0.1", "api": 1}
def boom(arr, v, **_):
    raise RuntimeError("плагин упал")
def register(api):
    api.add_effect("boom", "Бум", "Boom", params=[{"key": "x", "default": [1.0]}], fn=boom)
