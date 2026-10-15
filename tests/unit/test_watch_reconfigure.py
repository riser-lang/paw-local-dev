"""Execute the legacy integration method without loading its optional Reader stack."""
import ast
from pathlib import Path
from types import SimpleNamespace


def operation():
    source=Path('backend/watcher/observer.py')
    tree=ast.parse(source.read_text(encoding='utf-8-sig'))
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='LocalSourceWatcher')
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='set_sources_dirs')
    namespace={'Path':Path}
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(source),'exec'),namespace)
    return namespace['set_sources_dirs']


def test_saving_same_watch_directories_does_not_stop_busy_observer(tmp_path):
    def busy_stop():raise AssertionError('Stopping a busy watchdog observer can wait on its dispatch lock')
    watcher=SimpleNamespace(sources_dirs=[tmp_path],sources_dir=tmp_path,
        observer=SimpleNamespace(is_alive=lambda:True),stop=busy_stop,start=lambda:None)
    operation()(watcher,[str(tmp_path)])
    assert watcher.sources_dir==tmp_path


def test_changed_directories_still_reconfigure_observer(tmp_path):
    events=[]
    watcher=SimpleNamespace(sources_dirs=[tmp_path],sources_dir=tmp_path,
        observer=SimpleNamespace(is_alive=lambda:True),stop=lambda:events.append('stop'),start=lambda:events.append('start'))
    changed=tmp_path/'changed'
    operation()(watcher,[changed])
    assert watcher.sources_dirs==[changed] and watcher.sources_dir==changed
    assert events==['stop','start']
