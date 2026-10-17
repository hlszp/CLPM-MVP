import os,sys,socket
from pathlib import Path
root=Path('/private/tmp/clpm-g5-followup-20261014/backend')
os.chdir(root)
sys.path.insert(0,str(root))
os.environ.setdefault('JWT_SECRET_KEY','ci-test-secret-key-at-least-32-characters-long!!!')
os.environ.update(DEBUG='true',CLPM_TEST_MODE='1',CLPM_SKIP_EXIT_HOOKS='1')
original_connect=socket.socket.connect
original_connect_ex=socket.socket.connect_ex
attempts=[]
def blocked(self,address):
 if self.family in (socket.AF_INET,socket.AF_INET6):
  attempts.append(self.family)
  raise OSError('G5 offline validation blocks all IP sockets')
 return original_connect(self,address)
def blocked_ex(self,address):
 if self.family in (socket.AF_INET,socket.AF_INET6):
  attempts.append(self.family)
  raise OSError('G5 offline validation blocks all IP sockets')
 return original_connect_ex(self,address)
socket.socket.connect=blocked
socket.socket.connect_ex=blocked_ex
import pytest
rc=pytest.main(['-q','-p','no:cacheprovider','--basetemp=/private/tmp/g5-full-pytest-20261014'])
print('OFFLINE_NETWORK_ATTEMPTS_BLOCKED=',len(attempts),'REAL_IP_CONNECTIONS=0','PYTEST_EXIT=',rc)
sys.exit(rc)
