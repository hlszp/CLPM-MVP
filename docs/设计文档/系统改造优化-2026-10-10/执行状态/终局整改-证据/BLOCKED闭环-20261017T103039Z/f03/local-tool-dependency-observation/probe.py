import sys,json,hashlib
from pathlib import Path
root=Path('/Users/zhangping/.cache/uv/archive-v0/7r6NfAjmuokYoUGS')
sys.path.insert(0,str(root))
import psutil
print(json.dumps({'python':sys.version,'psutilVersion':psutil.__version__,'origin':psutil.__file__,'nativeModuleSha256':hashlib.sha256((root/'psutil/_psutil_osx.abi3.so').read_bytes()).hexdigest(),'recordSha256':hashlib.sha256((root/'psutil-7.2.2.dist-info/RECORD').read_bytes()).hexdigest(),'metadataSha256':hashlib.sha256((root/'psutil-7.2.2.dist-info/METADATA').read_bytes()).hexdigest(),'installedByThisProbe':False}))
