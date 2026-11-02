import sys

from . import server

if "--port" in sys.argv:  # for testing next to a running copy
    server.PORT = int(sys.argv[sys.argv.index("--port") + 1])
server.main(open_browser="--no-browser" not in sys.argv)
