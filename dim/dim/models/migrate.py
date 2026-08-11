

import re
import subprocess
from collections import deque

import importlib.resources

from dim.models import SchemaInfo, SCHEMA_VERSION, db


def migrate():
    start_version = SchemaInfo.current_version()
    if start_version == SCHEMA_VERSION:
        print('Nothing to do')
        return
    print(('From', start_version, 'to', SCHEMA_VERSION))
    graph = gather_graph()
    q = deque([start_version])
    prev = {start_version: None}
    while q:
        ver = q.pop()
        if ver == SCHEMA_VERSION:
            plan = []
            while ver != start_version:
                prev_ver, script = prev[ver]
                plan.append((ver, script))
                ver = prev_ver
            for new_version, script in reversed(plan):
                run_script(new_version, script)
            return
        for nv, script in graph[ver]:
            if nv not in prev:
                prev[nv] = (ver, script)
                q.append(nv)
    raise Exception("Migration path not found from %s to %s" % (start_version, SCHEMA_VERSION))


def gather_graph():
    graph = {}
    for script in importlib.resources.files("dim").joinpath("sql").iterdir():
        m = re.match(r'(migrate|rollback)_(.*)_to_(.*).sql', script.name)
        if m:
            x, y = m.group(2), m.group(3)
            graph.setdefault(x, []).append((y, script.name))
    return graph


def run_script(new_version, script):
    print(("Changing schema version %s to %s: %s"
          % (SchemaInfo.current_version(), new_version, script)))
    url = db.engine.url
    cmd = ['mysql',
           '-h%s' % url.host,
           url.database]
    if url.port:
        cmd.append('-P%s' % url.port)
    if url.username:
        cmd.append('-u%s' % url.username)
    if url.password:
        cmd.append('-p%s' % url.password)
    ref = importlib.resources.files("dim") / "sql" / script
    with importlib.resources.as_file(ref) as path:
        with open(path) as stdin:
            subprocess.check_call(cmd, stdin=stdin)
