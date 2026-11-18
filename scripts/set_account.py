"""Provision or update a local BOM account. Never pass passwords on the command line."""
import argparse
from getpass import getpass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bom_change_assistant.accounts import GROUPS, ROLES, set_account


def main():
    parser = argparse.ArgumentParser(description="本机管理员设置核查账号和部门组")
    parser.add_argument("--accounts", type=Path, required=True)
    parser.add_argument("username")
    parser.add_argument("group", choices=GROUPS)
    parser.add_argument("--roles", nargs="+", choices=ROLES, help="授权角色，首项为登录后的默认角色")
    args = parser.parse_args()
    password = getpass("设置口令：")
    if password != getpass("再次输入口令："):
        parser.error("两次口令不一致")
    try:
        set_account(args.accounts, args.username, args.group, password, args.roles)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
