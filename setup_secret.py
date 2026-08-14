#!/usr/bin/env python3
"""
GitHub Secret 设置工具
使用 GitHub API 加密并设置 DEEPSEEK_API_KEY 到仓库 Secrets
"""
import os
import sys
import json
import base64
import ssl
import urllib3
import requests
from nacl import encoding, public

# 禁用 SSL 警告（Windows 环境证书链不完整）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

REPO_OWNER = "haonL-7"
REPO_NAME = "bioTriage"
SECRET_NAME = "DEEPSEEK_API_KEY"

# API Key — 从项目 .env 或参数读取
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
if not API_KEY:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith("DEEPSEEK_API_KEY="):
                    API_KEY = line.split("=", 1)[1].strip()
                    break

def encrypt_secret(public_key: str, secret_value: str) -> str:
    """使用 PyNaCl 加密 secret"""
    key = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(key)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")

def set_github_secret(github_token: str):
    """设置 GitHub Actions Secret"""
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Step 1: 获取仓库公钥
    key_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/secrets/public-key"
    resp = requests.get(key_url, headers=headers, verify=False)
    if resp.status_code != 200:
        print(f"[FAIL] 获取公钥失败: {resp.status_code} {resp.text}")
        return False
    pubkey_data = resp.json()
    key_id = pubkey_data["key_id"]
    public_key = pubkey_data["key"]

    # Step 2: 加密 secret
    encrypted_value = encrypt_secret(public_key, API_KEY)

    # Step 3: 上传 secret
    put_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/secrets/{SECRET_NAME}"
    payload = {
        "encrypted_value": encrypted_value,
        "key_id": key_id,
    }
    resp = requests.put(put_url, headers=headers, json=payload, verify=False)
    if resp.status_code in (201, 204):
        print(f"[OK] GitHub Secret '{SECRET_NAME}' 已设置成功")
        return True
    else:
        print(f"[FAIL] 设置失败: {resp.status_code} {resp.text}")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python setup_secret.py <GITHUB_PAT>")
        print()
        print("需要 GitHub Personal Access Token (classic)，权限: repo 或 workflow")
        print("创建地址: https://github.com/settings/tokens/new")
        print()
        print(f"将要设置的 Key: {API_KEY[:15]}...{API_KEY[-4:] if API_KEY else '(未找到)'}" if API_KEY else "未找到 DEEPSEEK_API_KEY")
        sys.exit(1)

    if not API_KEY:
        print("[FAIL] 未找到 DEEPSEEK_API_KEY。请确认 .env 文件存在或设置了环境变量")
        sys.exit(1)

    token = sys.argv[1]
    set_github_secret(token)
