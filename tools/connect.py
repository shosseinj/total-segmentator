"""Foreground SSH tunnel; never publishes database/storage ports."""
import argparse,re,subprocess
from pathlib import Path
from .common import read_connection

def command(target,values,options):
    if not target or target.startswith('-') or re.search(r'\s',target):raise ValueError('INVALID_SSH_TARGET')
    if str(options.get('CLINIC_REVERSE_PORT','8098'))!='8098':raise ValueError('REVERSE_PORT_MUST_BE_8098')
    def port(name,default):
        value=str(options.get(name,values.get(name,default)))
        if not value.isdigit() or not 1<=int(value)<=65535:raise ValueError('INVALID_PORT')
        return value
    argv=['ssh','-N','-T','-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3']
    for name,local,default in [('POSTGRES',15432,5432),('REDIS',16379,6379),('MINIO',19000,9000)]:
        host=values.get('CLINIC_'+name+'_HOST','127.0.0.1')
        if not re.fullmatch('[A-Za-z0-9._-]+',host):raise ValueError('INVALID_FORWARD_HOST')
        argv+=['-L',f'127.0.0.1:{local}:{host}:'+port('CLINIC_'+name+'_PORT',default)]
    argv+=['-R','127.0.0.1:'+port('CLINIC_REVERSE_PORT',8098)+':127.0.0.1:'+port('SEG_API_PORT',8096),target]
    return argv

def main():
    parser=argparse.ArgumentParser(description='Run in a separate terminal on the GPU host; leave open.')
    parser.add_argument('target',help='SSH account on the existing clinic host, e.g. operator@192.168.1.10')
    args=parser.parse_args()
    try:
        values=read_connection('connection.env');options=read_connection('.env')
        raise SystemExit(subprocess.call(command(args.target,values,options)))
    except (ValueError,OSError):raise SystemExit('SSH_TUNNEL_FAILED: check target, SSH and local port availability') from None

if __name__=='__main__':main()
