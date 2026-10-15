import runpy
import subprocess
import sys
import types
from pathlib import Path
import pytest
from backend.pm.reader_process import process_options


def test_reader_child_has_eof_input_without_inherited_console(monkeypatch):
    monkeypatch.setattr(sys,'stdin',None)
    child=subprocess.Popen([sys.executable,'-c','import sys; data=sys.stdin.read(); print("EOF:"+repr(data))'],**process_options())
    out,err=child.communicate(timeout=5)
    assert child.returncode == 0
    assert out.strip() == "EOF:''"
    assert err == ''


@pytest.mark.parametrize('args',[['--reader-worker','input.json','out'],['-m','backend.reader.subprocess_worker','input.json','out']])
def test_reader_entry_does_not_initialize_desktop_or_settings(monkeypatch,args):
    invoked=[]
    stub=types.ModuleType('backend.reader.subprocess_worker')
    before=set(sys.modules)
    def main(received):
        invoked.append(received)
        added=set(sys.modules)-before
        assert not {'uvicorn','backend.core.config','backend.tray.tray_app'}.intersection(added)
        return 0
    stub.main=main
    monkeypatch.setitem(sys.modules,'backend.reader.subprocess_worker',stub)
    monkeypatch.setattr(sys,'path',sys.path.copy())
    monkeypatch.setattr(sys,'argv',['run.py']+args)
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(Path(__file__).resolve().parents[2]/'run.py'),run_name='__main__')
    assert result.value.code == 0
    assert invoked == [['input.json','out']]
