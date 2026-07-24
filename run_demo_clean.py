import os
import runpy
import sys

for _path in list(sys.path):
    if "site-packages" in _path and ".hermes" in _path:
        sys.path.remove(_path)

sys.meta_path[:] = [
    finder
    for finder in sys.meta_path
    if type(finder).__name__ not in {"_EditableFinder", "_EditableNamespaceFinder"}
]

os.environ["PYTHONPATH"] = ""
os.chdir(os.path.dirname(os.path.abspath(__file__)))
runpy.run_path("demo.py", run_name="__main__")
