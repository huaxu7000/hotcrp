#!/usr/bin/env python3
"""Manage a project-local HotCRP stack. No system services or sudo required."""
import argparse
import datetime
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
VAR = ROOT / 'var' / 'local'
BREW = Path(subprocess.check_output(['brew', '--prefix'], text=True).strip())
ENV = {**os.environ, 'PATH': f'{BREW}/bin:{BREW}/sbin:' + os.environ['PATH']}

def run(args, **kw):
    return subprocess.run([str(a) for a in args], cwd=ROOT, env=ENV, check=True, **kw)

def php(*args, **kw):
    return run([BREW/'bin/php', '-c', VAR/'php.ini', *args], **kw)

def configure():
    for d in ['', 'run', 'logs', 'sessions', 'backups', 'tmp']:
        (VAR/d).mkdir(parents=True, exist_ok=True)
    VAR.chmod(0o700)
    (ROOT/'conf').mkdir(exist_ok=True)
    (ROOT/'conf').chmod(0o700)
    (VAR/'php.ini').write_text(f'''[PHP]
date.timezone = America/New_York
memory_limit = 256M
upload_max_filesize = 15M
post_max_size = 20M
max_input_vars = 4096
session.gc_maxlifetime = 86400
session.save_path = "{VAR}/sessions"
upload_tmp_dir = "{VAR}/tmp"
log_errors = On
error_log = "{VAR}/logs/php-error.log"
display_errors = Off
mysqli.default_socket = "{VAR}/run/mysql.sock"
pdo_mysql.default_socket = "{VAR}/run/mysql.sock"
sendmail_path = "{BREW}/bin/mailpit sendmail -S 127.0.0.1:1025"
opcache.validate_timestamps = 1
opcache.revalidate_freq = 0
''')
    (VAR/'php-fpm.conf').write_text(f'''[global]
pid = {VAR}/run/php-fpm.pid
error_log = {VAR}/logs/php-fpm.log
daemonize = no
[hotcrp]
listen = {VAR}/run/php-fpm.sock
listen.mode = 0600
pm = ondemand
pm.max_children = 4
pm.process_idle_timeout = 10s
catch_workers_output = yes
''')
    (VAR/'nginx.conf').write_text(f'''worker_processes 1;
pid {VAR}/run/nginx.pid;
error_log {VAR}/logs/nginx-error.log;
events {{ worker_connections 256; }}
http {{
    include {BREW}/etc/nginx/mime.types;
    access_log {VAR}/logs/nginx-access.log;
    client_body_temp_path {VAR}/tmp/client;
    fastcgi_temp_path {VAR}/tmp/fastcgi;
    client_max_body_size 20m;
    server {{
        listen 127.0.0.1:8080;
        server_name 127.0.0.1 localhost;
        root {ROOT};
        location ~ ^/(images|scripts|stylesheets)/ {{ try_files $uri =404; }}
        location / {{
            include {BREW}/etc/nginx/fastcgi_params;
            fastcgi_param SCRIPT_FILENAME {ROOT}/index.php;
            fastcgi_param SCRIPT_NAME /index.php;
            fastcgi_param HTTP_PROXY "";
            fastcgi_pass unix:{VAR}/run/php-fpm.sock;
            fastcgi_read_timeout 120s;
        }}
    }}
}}
''')

def specs():
    return {
        'mariadb': [BREW/'bin/mariadbd', '--no-defaults', f'--datadir={VAR}/mysql', f'--socket={VAR}/run/mysql.sock', f'--pid-file={VAR}/run/mariadb.pid', '--skip-networking', '--max-allowed-packet=32M', f'--log-error={VAR}/logs/mariadb.log'],
        'mailpit': [BREW/'bin/mailpit', '--listen', '127.0.0.1:8025', '--smtp', '127.0.0.1:1025', '--database', VAR/'mailpit.db'],
        'php-fpm': [BREW/'sbin/php-fpm', '-c', VAR/'php.ini', '-y', VAR/'php-fpm.conf', '-F'],
        'nginx': [BREW/'bin/nginx', '-p', str(ROOT)+'/', '-c', VAR/'nginx.conf', '-g', 'daemon off;']
    }

def alive(name):
    p = VAR/'run'/f'{name}.process'
    try:
        pid = int(p.read_text())
        os.kill(pid, 0)
        # Check identity before acting on a potentially reused PID.
        command = subprocess.check_output(['ps', '-p', str(pid), '-o', 'command='], text=True)
        return pid if Path(specs()[name][0]).name in command and str(VAR) in command else None
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None

def wait_ready(name):
    for _ in range(100):
        if not alive(name):
            raise RuntimeError(f'{name} exited; see {VAR}/logs/{name}.out.log')
        try:
            if name == 'mariadb':
                r = subprocess.run([str(BREW/'bin/mariadb-admin'), '--no-defaults', f'--socket={VAR}/run/mysql.sock', '-u', 'root', 'ping'], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if r.returncode == 0: return
            elif name == 'php-fpm':
                with socket.socket(socket.AF_UNIX) as s:
                    s.connect(str(VAR/'run/php-fpm.sock'))
                return
            else:
                port = 8025 if name == 'mailpit' else 8080
                with socket.create_connection(('127.0.0.1', port), timeout=.2): return
        except OSError:
            pass
        time.sleep(.2)
    raise RuntimeError(f'{name} did not become ready')

def start(names=None):
    configure()
    if not (VAR/'mysql/mysql').exists():
        run([BREW/'bin/mariadb-install-db', '--no-defaults', f'--datadir={VAR}/mysql', '--auth-root-authentication-method=normal', '--skip-test-db'], stdout=subprocess.DEVNULL)
    for name, args in specs().items():
        if names and name not in names: continue
        if not alive(name):
            with (VAR/'logs'/f'{name}.out.log').open('ab') as log:
                p = subprocess.Popen([str(a) for a in args], cwd=ROOT, env=ENV, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            (VAR/'run'/f'{name}.process').write_text(str(p.pid))
        wait_ready(name)
        print(f'{name}: running')

def stop(names=None):
    for name in reversed(specs()):
        if names and name not in names: continue
        pid = alive(name)
        if pid:
            os.kill(pid, signal.SIGTERM)
            for _ in range(100):
                if not alive(name): break
                time.sleep(.1)
            if alive(name): raise RuntimeError(f'{name} has not stopped')
        print(f'{name}: stopped')

def backup():
    out = VAR/'backups'/('hotcrp-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.sql')
    with out.open('wb') as f:
        php('batch/backupdb.php', '--check-table=Paper', '--check-table=ContactInfo', stdout=f)
    if out.stat().st_size < 1000 or b'CREATE TABLE' not in out.read_bytes():
        raise RuntimeError(f'Backup validation failed: {out}')
    out.chmod(0o600)
    print(out)
    return out

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command', choices=['start','stop','restart','status','backup','restore','reset','php','init'])
    ap.add_argument('args', nargs=argparse.REMAINDER)
    ns = ap.parse_args()
    if ns.command == 'start': start()
    elif ns.command == 'stop': stop()
    elif ns.command == 'restart': stop(); start()
    elif ns.command == 'status':
        for name in specs(): print(f'{name}: {"running" if alive(name) else "stopped"}')
    elif ns.command == 'php':
        result = subprocess.run([str(BREW/'bin/php'), '-c', str(VAR/'php.ini'), *ns.args], cwd=ROOT, env=ENV)
        sys.exit(result.returncode)
    elif ns.command == 'backup': backup()
    elif ns.command in ['restore', 'reset']:
        if ns.command == 'restore' and len(ns.args) != 1: ap.error('restore requires a SQL backup filename')
        src = Path(ns.args[0]).resolve() if ns.command == 'restore' else VAR/'backups/clean-test.sql'
        if not src.is_file(): raise RuntimeError(f'Missing backup: {src}')
        stop(['nginx','php-fpm'])
        try:
            backup()
            php('batch/backupdb.php', '-r', src)
        finally: start(['php-fpm','nginx'])
    elif ns.command == 'init':
        start()
        if not (ROOT/'conf/options.php').exists():
            php('batch/createdb.php', '--batch', '--minimal', '--user=root', '--name=hotcrp_local')
            with (ROOT/'conf/options.php').open('a') as f:
                f.write('''\n$Opt["dbSocket"] = __DIR__ . "/../var/local/run/mysql.sock";
$Opt["shortName"] = "Local Test 2026";
$Opt["longName"] = "Local HotCRP Test Conference";
$Opt["paperSite"] = "http://127.0.0.1:8080";
$Opt["contactName"] = "Local Administrator";
$Opt["contactEmail"] = "admin@hotcrp.localhost";
$Opt["emailFrom"] = "HotCRP Local <admin@hotcrp.localhost>";
$Opt["sendEmail"] = true;
$Opt["sendmailParam"] = "";
$Opt["timezone"] = "America/New_York";
$Opt["stylesheets"][] = "stylesheets/aitcc-theme.css";
$Opt["pages"][] = "local-dev/ui-components.json";
$Opt["collectAuthorPCConflicts"] = false;
$Opt["pdftohtmlCommand"] = "'''+str(BREW)+'''/bin/pdftohtml";
''')
            (ROOT/'conf/options.php').chmod(0o600)
        print('HotCRP: http://127.0.0.1:8080 | Mail: http://127.0.0.1:8025')

if __name__ == '__main__':
    try: main()
    except (RuntimeError, subprocess.CalledProcessError) as e:
        print(e, file=sys.stderr)
        sys.exit(1)
