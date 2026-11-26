"""在 Collector 本机交互准备登录配置，不输出或保存明文密码。"""

import argparse
import getpass
import os
import shutil
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--sites", type=Path, required=True)
    args = parser.parse_args()
    for path in (args.config, args.data):
        if path.is_symlink():
            raise ValueError("目录不能为符号链接")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (args.config / "htpasswd").exists():
        raise ValueError("认证配置已存在，不覆盖；如需修改请在 Collector 本机运行 htpasswd")
    password = getpass.getpass("设置远程登录页面密码（至少 12 位）：")
    if len(password) < 12 or password != getpass.getpass("再次输入：") or any(c in password for c in "\n\r\0"):
        raise ValueError("密码长度不足或两次输入不一致")
    result = subprocess.run(["htpasswd", "-niB", "operator"], input=password + "\n", text=True, capture_output=True, check=True)
    path = args.config / "htpasswd"
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as file:
        file.write(result.stdout)
    shutil.copyfile(args.sites, args.config / "sites.json")
    (args.config / "sites.json").chmod(0o600)
    print("配置已创建，登录用户名为 operator。请确保容器 UID 10001 能读取配置并写入数据目录。")


if __name__ == "__main__":
    main()
