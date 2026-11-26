"""通过已保存的 SecureCRT SSH 会话打开一次性登录入口；不读取平台账号密码。"""

import argparse
import os
import re
import socket
import subprocess
import tempfile
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--securecrt", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--session", default="Collector")
    parser.add_argument("--port", type=int, default=18120, choices=range(1024, 65536))
    parser.add_argument("--container", default="realsexy-browser-login")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", args.container):
        raise ValueError("容器名无效")
    if '"' in args.session or any(c in args.session for c in "\r\n"):
        raise ValueError("SSH 会话名无效")
    with socket.socket() as probe:
        connected = probe.connect_ex(("127.0.0.1", args.port)) == 0
    connection = '/S "' + args.session + '"' + ('' if connected else f' /LOCAL {args.port}:127.0.0.1:{args.port}')
    with tempfile.TemporaryDirectory(prefix="collector-entry-") as temporary:
        receipt, script = Path(temporary) / "entry.txt", Path(temporary) / "open.vbs"
        def quoted(value):
            return '"' + str(value).replace('"', '""') + '"'
        command = f"printf '\\n%s%s\\n' '__ENTRY_' 'BEGIN__'; docker exec {args.container} python -m app.plugin.infra.crawler.login_access --port {args.port}; printf '\\n%s%s\\n' '__ENTRY_' 'END__'"
        script.write_text('\n'.join([
            '# $language = "VBScript"', '# $interface = "1.0"', 'Sub Main',
            'Dim tab, result, fso, output',
            'Set tab = crt.Session.ConnectInTab(' + quoted(connection) + ', True)',
            'If Not tab.Session.Connected Then Exit Sub',
            'tab.Screen.Synchronous = True', 'crt.Sleep 500',
            'tab.Screen.Send ' + quoted(command) + ' & vbCr',
            'result = tab.Screen.ReadString("__ENTRY_BEGIN__", 30)',
            'result = tab.Screen.ReadString("__ENTRY_END__", 30)',
            'Set fso = CreateObject("Scripting.FileSystemObject")',
            'Set output = fso.OpenTextFile(' + quoted(receipt) + ', 2, True, -1)',
            'output.Write result', 'output.Close',
            'tab.Screen.Synchronous = False',
            *(['tab.Session.Disconnect', 'tab.Close'] if connected else []), 'End Sub',
        ]), encoding='utf-16')
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        subprocess.Popen([args.securecrt, '/F', args.config, '/SCRIPT', str(script)], startupinfo=startup)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if receipt.exists():
                match = re.search(rf'http://127\.0\.0\.1:{args.port}/entry#access=[a-zA-Z0-9.]+', receipt.read_text(encoding='utf-16'))
                if match:
                    os.startfile(match.group(0))
                    print("已打开采集登录页面，无需输入独立页面密码。")
                    return
                raise RuntimeError("未取得一次性入口，请检查 Collector 登录容器")
            time.sleep(0.5)
        raise RuntimeError("SSH 连接未完成，请先在 SecureCRT 解锁并确认 Collector 会话可连接")


if __name__ == "__main__":
    main()
