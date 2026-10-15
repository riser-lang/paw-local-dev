"""Standard streams for a reader child must not depend on a desktop console."""
import subprocess
import sys


def process_options():
    return {'stdin':subprocess.DEVNULL, 'stdout':subprocess.PIPE, 'stderr':subprocess.PIPE,
            'text':True, 'encoding':'utf-8', 'errors':'replace',
            'creationflags':subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0}
