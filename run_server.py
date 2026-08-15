import sys, os
REPO = "/Users/scottthomasswitzer/Documents/OAI_Build_Week"
if REPO not in sys.path:
    sys.path.insert(0, REPO)
import importlib
_appmod = importlib.import_module("app.api.app")
print("LOADED app from:", _appmod.__file__, file=sys.stderr)
print("ROOT/static exists:", os.path.isdir(_appmod.ROOT / "static"),
      "submission.html:", os.path.isfile(_appmod.ROOT / "static" / "submission.html"), file=sys.stderr)
import uvicorn
uvicorn.run(_appmod.app, host="127.0.0.1", port=8000)
