import subprocess
import sys
import unittest
from bot.fast_ocr import environment


class OCRSandboxTests(unittest.TestCase):
    def test_network_and_core_dumps_are_blocked_in_child_only(self):
        code = '''
import socket, resource
from bot.ocr_sandbox import restrict_worker
restrict_worker()
assert resource.getrlimit(resource.RLIMIT_CORE) == (0, 0)
for family in (socket.AF_INET, socket.AF_INET6):
    try:
        socket.socket(family, socket.SOCK_STREAM)
    except PermissionError:
        pass
    else:
        raise AssertionError('Network socket allowed')
print('blocked')
'''
        result = subprocess.run([sys.executable, '-c', code], env=environment(),
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'blocked')
