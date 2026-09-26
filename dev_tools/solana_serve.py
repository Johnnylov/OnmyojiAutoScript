"""Isolated desktop integration server: never auto-start configured game tasks."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def create_app():
    from module.server.app import app
    app.state.script_instances = []
    return app


if __name__ == '__main__':
    import os
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=22370)
    args = parser.parse_args()
    os.chdir(ROOT)
    uvicorn.run(create_app(), host='127.0.0.1', port=args.port, log_level='warning', log_config=None)
