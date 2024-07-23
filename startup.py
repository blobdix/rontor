#!/usr/bin/env python3
import subprocess
import logging
import os
import shutil

# ロギングの設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def run_command(command):
    try:
        subprocess.run(command, check=True, shell=True)
        logging.info(f"Command executed successfully: {command}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Command failed: {command}. Error: {str(e)}")
        raise

# Caddyfileの内容を置き換える
caddyfile_content = "import /rontor/main/Caddyfile\n"
try:
    with open('/etc/caddy/Caddyfile', 'w') as f:
        f.write(caddyfile_content)
    logging.info("Caddyfile updated successfully")
except IOError as e:
    logging.error(f"Failed to update Caddyfile: {str(e)}")
    raise

# Caddyサービスを有効化して起動
try:
    run_command("systemctl enable caddy")
    run_command("systemctl restart caddy")
except Exception as e:
    logging.error(f"Failed to enable and start Caddy service: {str(e)}")
    raise

# サイトの配列
sites = ["org.alpha-carinae.mattermost"]

# サービスファイルをコピーする関数
def copy_service_file():
    source = "/rontor/main/rontor-site@.service"
    destination = "/etc/systemd/system/rontor-site@.service"
    try:
        shutil.copy2(source, destination)
        logging.info(f"Service file copied from {source} to {destination}")
    except IOError as e:
        logging.error(f"Failed to copy service file: {str(e)}")
        raise

# site-ctl.pyに実行権限を付与
try:
    run_command("chmod +x /rontor/main/site-ctl.py")
    logging.info("Execution permission granted to site-ctl.py")
except Exception as e:
    logging.error(f"Failed to set execution permission for site-ctl.py: {str(e)}")
    raise

# サービスファイルのコピーとsystemdのリロード
try:
    copy_service_file()
    run_command("systemctl daemon-reload")
    logging.info("Service file copied and system reloaded")
except Exception as e:
    logging.error(f"Failed to setup service file: {str(e)}")
    raise

# 各サイトに対してサービスをenable --now
for site in sites:
    try:
        run_command(f"systemctl enable rontor-site@{site}.service")
        run_command(f"systemctl reload-or-restart rontor-site@{site}.service")
        logging.info(f"Service for {site} enabled and started")
    except Exception as e:
        logging.error(f"Failed to enable and start service for {site}: {str(e)}")

logging.info("All services have been created and started")

logging.info("Startup script completed successfully")