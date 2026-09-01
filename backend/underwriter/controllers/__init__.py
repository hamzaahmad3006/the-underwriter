"""Controllers — one module per resource.

A controller owns its request/response shapes and the logic behind one group of
endpoints. Routers stay thin: they bind a path to a controller function and
declare auth. Nothing here talks to FastAPI's Request object, so every
controller is callable directly from a test.
"""
