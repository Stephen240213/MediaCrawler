"""Generate a per-user LaunchAgent; requires explicit --install to load it."""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--env-file',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--install',action='store_true')
args=parser.parse_args()
root=Path(__file__).resolve().parents[2]
if sys.platform!='darwin' and args.install:
    parser.error('仅可在采集 Mac 上安装')
if not args.env_file.is_file():parser.error('私有环境文件不存在')
log=root/'.runtime'/'service';log.mkdir(parents=True,exist_ok=True)
config={'Label':'com.mediacrawler.workbench','ProgramArguments':[sys.executable,'-m','api.serve','--env-file',str(args.env_file.resolve())],
        'WorkingDirectory':str(root),'RunAtLoad':True,'KeepAlive':{'SuccessfulExit':False},'ThrottleInterval':5,
        'StandardOutPath':str(log/'stdout.log'),'StandardErrorPath':str(log/'stderr.log'),
        'EnvironmentVariables':{'PATH':os.environ.get('PATH','/usr/bin:/bin'),'UV_FROZEN':'1'}}
args.output.parent.mkdir(parents=True,exist_ok=True)
if args.output.exists():parser.error('目标文件已存在，请先保留并检查旧配置')
with args.output.open('xb') as file:plistlib.dump(config,file)
args.output.chmod(0o600)
if args.install:subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(args.output.resolve())],check=True)
print('LaunchAgent configuration generated. Browser actions require this user to remain logged in.')
