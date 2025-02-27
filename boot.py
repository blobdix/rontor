#!/usr/bin/env python3

import subprocess
import time
import os
import logging
import shutil
from datetime import datetime
import pytz
import threading

def get_instance_metadata(key, token):
    import requests
    url = f"http://169.254.169.254/latest/meta-data/{key}"
    headers = {"X-aws-ec2-metadata-token": token}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.text

def get_instance_tags(instance_id, region):
    import boto3
    ec2 = boto3.client('ec2', region_name=region)
    response = ec2.describe_tags(Filters=[
        {'Name': 'resource-id', 'Values': [instance_id]}
    ])
    tags = {tag['Key']: tag['Value'] for tag in response['Tags']}
    return tags

def get_log_file_name(instance_id, base_dir):
    current_time = datetime.now().strftime("%y%m%d_%H%M%S")
    log_dir = os.path.join(base_dir, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, f"{current_time}-ec2-{instance_id}.log")

def setup_logging(log_file):
    logging.basicConfig(filename=log_file, level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s', force = True)
    logging.info(f"Logging to {log_file}")

def switch_logging(new_log_file):
    root = logging.getLogger()
    for handler in root.handlers[:]:
        handler.flush()
        handler.close()
        root.removeHandler(handler)
    setup_logging(new_log_file)

def run_command(command):
    try:
        result = subprocess.run(command.split(), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result.stdout.decode('utf-8')
    except subprocess.CalledProcessError as e:
        logging.error(f"Command '{command}' failed with error: {e.stderr.decode('utf-8')}")
        raise

def retry_operation(operation, max_retries=3, delay=5):
    for attempt in range(max_retries):
        try:
            return operation()
        except Exception as e:
            logging.warning(f"Attempt {attempt + 1} failed: {e}")
            if attempt == max_retries - 1:
                raise
            time.sleep(delay)

def associate_elastic_ip(ec2_client, instance_id, elastic_ip):
    def _associate():
        response = ec2_client.describe_addresses(PublicIps=[elastic_ip])
        current_instance_id = response['Addresses'][0].get('InstanceId')

        if current_instance_id == instance_id:
            logging.info(f"Elastic IP {elastic_ip} is already associated with this instance {instance_id}")
            return

        if current_instance_id:
            ec2_client.disassociate_address(PublicIp=elastic_ip)
            logging.info(f"Disassociated Elastic IP {elastic_ip} from instance {current_instance_id}")

        ec2_client.associate_address(InstanceId=instance_id, PublicIp=elastic_ip)
        logging.info(f"Elastic IP {elastic_ip} associated with instance {instance_id}")

    retry_operation(_associate)

import ipaddress

def normalize_ipv6(address):
    return str(ipaddress.IPv6Address(address).exploded)

def associate_ipv6_address(ec2_client, instance_id, ipv6_address):
    def _associate():
        network_interfaces = ec2_client.describe_network_interfaces(
            Filters=[{'Name': 'attachment.instance-id', 'Values': [instance_id]}]
        )['NetworkInterfaces']
        
        if network_interfaces:
            network_interface = network_interfaces[0]
            network_interface_id = network_interface['NetworkInterfaceId']
            
            # 既存のIPv6アドレスを正規化
            existing_ipv6 = [normalize_ipv6(addr['Ipv6Address']) for addr in network_interface.get('Ipv6Addresses', [])]
            
            # 指定されたIPv6アドレスを正規化
            normalized_ipv6_address = normalize_ipv6(ipv6_address)
            
            if normalized_ipv6_address in existing_ipv6:
                logging.info(f"IPv6 address {ipv6_address} is already associated with instance {instance_id}")
                return
            
            ec2_client.assign_ipv6_addresses(
                NetworkInterfaceId=network_interface_id,
                Ipv6Addresses=[ipv6_address]
            )
            logging.info(f"IPv6 address {ipv6_address} associated with instance {instance_id}")
        else:
            logging.error(f"No network interface found for instance {instance_id}")
    
    retry_operation(_associate)

def attach_ebs_volume(ec2_client, instance_id, volume_id):
    def _attach():
        while True:
            response = ec2_client.describe_volumes(VolumeIds=[volume_id])
            attachments = response['Volumes'][0]['Attachments']

            if not attachments:
                ec2_client.attach_volume(VolumeId=volume_id, InstanceId=instance_id, Device='/dev/sdf')
                logging.info(f"EBS volume {volume_id} attached to instance {instance_id}")
                return

            if attachments[0]['InstanceId'] == instance_id:
                logging.info(f"EBS volume {volume_id} is already attached to this instance {instance_id}")
                return

            logging.info(f"EBS volume {volume_id} is attached to another instance. Waiting...")
            time.sleep(1)

    retry_operation(_attach)

def get_token():
    import requests
    token_response = requests.put("http://169.254.169.254/latest/api/token", headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"})
    token_response.raise_for_status()
    return token_response.text

def check_for_termination_notice():
    import requests
    try:
        token = get_token()
        headers = {"X-aws-ec2-metadata-token": token}
        response = requests.get("http://169.254.169.254/latest/meta-data/spot/instance-action", headers=headers)
        if response.status_code == 200:
            data = response.json()
            if data.get("action") == "terminate":
                logging.info("Termination notice received. Shutting down...")
                run_command("shutdown -h now")
    except requests.exceptions.RequestException as err:
        logging.error(f"Error checking for termination notice: {err}")
    except Exception as err:
        logging.exception("An error occurred:")

def mount_zfs_dataset():
    def is_zfs_mounted(dataset):
        try:
            result = subprocess.run(['zfs', 'list', '-H', '-o', 'mounted', dataset], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return result.stdout.decode('utf-8').strip() == 'yes'
        except subprocess.CalledProcessError as e:
            logging.warning(f"Failed to check if ZFS dataset {dataset} is mounted: {e.stderr.decode('utf-8')}")
            return False  # エラーの場合はマウントされていないものとして扱う
    datasets = ["rontor/main", "rontor/docker"]
    all_mounted = all(is_zfs_mounted(dataset) for dataset in datasets)

    if all_mounted:
        logging.info("All ZFS datasets are already mounted")
        return

    try:
        # ZFS プールをインポート
        retry_operation(lambda: run_command("zpool import -f rontor"))
        logging.info("ZFS pool 'rontor' imported successfully")
    except Exception as e:
        logging.error(f"Failed to import ZFS pool 'rontor': {str(e)}")
        raise

    # インポート後にマウント状態を再確認
    for dataset in datasets:
        if is_zfs_mounted(dataset):
            logging.info(f"ZFS dataset {dataset} is mounted")
        else:
            logging.warning(f"ZFS dataset {dataset} is not mounted after pool import")

def run_startup_script():
    retry_operation(lambda: run_command("python3 /rontor/main/startup.py"))
    logging.info("Startup script executed")

def setup_docker_bind_mount():
    docker_dir = "/var/lib/docker"
    zfs_docker_dir = "/rontor/docker"

    # /var/lib/docker ディレクトリを作成（存在しない場合）
    os.makedirs(docker_dir, exist_ok=True)

    # バインドマウントを実行
    run_command(f"mount --bind {zfs_docker_dir} {docker_dir}")

    logging.info(f"Docker directory {docker_dir} bind mounted to ZFS dataset {zfs_docker_dir}")

def install_docker():
    run_command("apt install -y docker.io docker-compose-v2")
    run_command("systemctl enable docker --now")
    logging.info("Docker installed and enabled")

def setup_apt_cache_bind_mount():
    apt_cache_dir = "/var/cache/apt"
    zfs_apt_cache_dir = "/rontor/apt-cache"

    # /rontor/apt-cache ディレクトリを作成（存在しない場合）
    os.makedirs(zfs_apt_cache_dir, exist_ok=True)

    # バインドマウントを実行
    run_command(f"mount --bind {zfs_apt_cache_dir} {apt_cache_dir}")

    logging.info(f"APT cache directory {apt_cache_dir} bind mounted to ZFS dataset {zfs_apt_cache_dir}")

def setup_base():
    run_command("apt update")
    run_command("apt install -y zfsutils-linux python3-boto3 python3-requests")
    logging.info("Base packages installed")

def setup_system():
    run_command("apt update")
    run_command("apt install -y caddy zsh rclone")
    logging.info("Additional packages installed")

def is_disk_initialized(device):
    try:
        result = subprocess.run(["sudo", "blkid", device], capture_output=True, text=True)
        return result.returncode == 0
    except subprocess.CalledProcessError:
        return False

def setup_instance_store_swap():
    try:
        # インスタンスストアのデバイス名を取得（通常は /dev/nvme1n1 または /dev/xvdb）
        result = subprocess.run(["lsblk", "-ndo", "NAME,TYPE"], capture_output=True, text=True)
        instance_store = None
        for line in result.stdout.splitlines():
            if "disk" in line and ("nvme1n1" in line or "xvdb" in line):
                instance_store = "/dev/" + line.split()[0]
                break

        if not instance_store:
            logging.info("No instance store device found. Skipping swap setup.")
            return

        # デバイスが既にマウントされていないか確認
        result = subprocess.run(["mount"], capture_output=True, text=True)
        if instance_store in result.stdout:
            logging.info(f"Instance store {instance_store} is already mounted. Skipping swap setup.")
            return

        # ディスクが初期化されているか確認
        if is_disk_initialized(instance_store):
            logging.warning(f"Instance store {instance_store} is already initialized. Skipping swap setup.")
            return

        # スワップとして初期化
        subprocess.run(["sudo", "mkswap", instance_store], check=True)
        
        # スワップをオンにする
        subprocess.run(["sudo", "swapon", instance_store], check=True)

        logging.info(f"Instance store {instance_store} has been set up as swap.")

    except subprocess.CalledProcessError as e:
        logging.error(f"Error setting up instance store as swap: {str(e)}")
    except Exception as e:
        logging.error(f"Unexpected error in setup_instance_store_swap: {str(e)}")

def set_timezone():
    try:
        # タイムゾーンを Asia/Tokyo に設定
        subprocess.run(["timedatectl", "set-timezone", "Asia/Tokyo"], check=True)
        
        # 設定が反映されたことを確認
        result = subprocess.run(["timedatectl", "show", "--property=Timezone", "--value"], 
                                check=True, capture_output=True, text=True)
        current_timezone = result.stdout.strip()
        
        if current_timezone == "Asia/Tokyo":
            logging.info("Timezone successfully set to Asia/Tokyo")
        else:
            logging.warning(f"Failed to set timezone. Current timezone: {current_timezone}")
        
        # ロギングのタイムゾーンも更新
        logging.Formatter.converter = lambda *args: datetime.now(pytz.timezone('Asia/Tokyo')).timetuple()
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to set timezone: {e}")

def main():
    try:
        swap_thread = threading.Thread(target=setup_instance_store_swap)
        swap_thread.start()

        # EBSボリュームのマウントまでに使うパッケージをインストール
        setup_base()

        # boto3のインポート（パッケージのインストール後）
        import boto3
        import requests

        # メタデータサービスのセッショントークンを取得
        token = get_token()

        # インスタンスIDの取得
        instance_id = get_instance_metadata('instance-id', token)

        # 初期ログの設定
        tmp_log_file = get_log_file_name(instance_id, '/tmp')
        setup_logging(tmp_log_file)
        logging.info("Starting user data script")

        set_timezone()

        # インスタンスが動作しているアベイラビリティゾーンの取得
        az = get_instance_metadata('placement/availability-zone', token)
        region = az[:-1]  # リージョンはAZの最後の文字を除いた部分

        # インスタンスタグの取得
        tags = get_instance_tags(instance_id, region)

        # 設定の取得
        elastic_ip = tags['ElasticIP']
        ebs_volume_id = tags['EBSVolumeID']
        ebs_mount_point = tags.get('EBSMountPoint', '/rontor/main')

        ec2_client = boto3.client('ec2', region_name=region)
        attach_ebs_volume(ec2_client, instance_id, ebs_volume_id)

        mount_zfs_dataset()

        setup_apt_cache_bind_mount()

        setup_system()

        # ログファイルのフラッシュと移動
        for handler in logging.getLogger().handlers:
            handler.flush()
        if os.path.exists(tmp_log_file):
            final_log_file = get_log_file_name(instance_id, ebs_mount_point)
            shutil.copy2(tmp_log_file, final_log_file)
            switch_logging(final_log_file)
            os.remove(tmp_log_file)
            logging.info("Log file moved to final destination")
        else:
            logging.error(f"Temporary log file does not exist: {tmp_log_file}")

        setup_docker_bind_mount()
        install_docker()

        associate_elastic_ip(ec2_client, instance_id, elastic_ip)

        ipv6_address = tags.get('IPv6Address')
        if ipv6_address:
            associate_ipv6_address(ec2_client, instance_id, ipv6_address)
        else:
            logging.warning("No IPv6 address specified in instance tags")

        run_startup_script()

        while True:
            check_for_termination_notice()
            time.sleep(5)

    except Exception as e:
        logging.exception("An error occurred:")

if __name__ == "__main__":
    main()
