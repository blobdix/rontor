#!/usr/bin/env python3

import sys
import os
import subprocess
import logging

# ロギングの設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def run_command(command):
    try:
        subprocess.run(command, check=True, shell=True)
        logging.info(f"Command executed successfully: {command}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Command failed: {command}. Error: {str(e)}")
        raise

def main(site, operation):
    try:
        # カレントディレクトリを変更
        os.chdir(f"/rontor/main/{site}")
        logging.info(f"Changed directory to /rontor/main/{site}")

        # 操作に基づいてコマンドを実行
        if operation == "start":
            run_command("docker compose up -d")
        elif operation == "stop":
            run_command("docker compose stop")
        elif operation == "reload":
            run_command("docker compose up -d")
        else:
            logging.error(f"Unknown operation: {operation}")
            sys.exit(1)

    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: site-ctl.py <site> <operation>")
        sys.exit(1)
    
    site = sys.argv[1]
    operation = sys.argv[2]
    main(site, operation)