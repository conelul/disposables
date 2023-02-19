#!/usr/bin/env python3.10
import argparse
import json
from itertools import islice
import boto3
import time

REGION: str = "us-east-2"
SRC_REPO = "https://github.com/conelul/disposables"


def chunks(data: dict[str, list[str]], size):
    it = iter(data)
    for i in range(0, len(data), size):
        yield {k: data[k] for k in islice(it, size)}


# def run_server(client, input: dict[str, list[str]]) -> dict:


def main() -> None:
    #! Args
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-n",
        "--num-servers",
        help="Number of servers to shard collection over",
        default=1,
    )
    # parser.add_argument("input")
    args = parser.parse_args()
    #! Load input
    # with open(args.input) as f:
    #     sites = json.load(f)
        # input_name = f.name
    #! Deploy
    # ssm = boto3.client("ssm", region_name=REGION)
    ec2 = boto3.client("ec2", region_name=REGION)
    # waiter = ec2.get_waiter('instance_status_ok')
    # Config to install SSM
    init = f"""#cloud-config
repo_update: true
repo_upgrade: all

runcmd:
    - export HOME=/home/ec2-user
    - sudo amazon-linux-extras install epel -y
    - sudo yum install -y https://s3.amazonaws.com/ec2-downloads-windows/SSMAgent/latest/linux_amd64/amazon-ssm-agent.rpm
    - sudo yum install -y git chromium gcc
    - sudo chown -R ec2-user /home/ec2-user/
    - curl https://sh.rustup.rs -sSf | sh -s -- --default-toolchain nightly -y
    - cd /home/ec2-user/
    - git clone {SRC_REPO} disposables && cd disposables/collection
    - wget https://s3.amazonaws.com/rds-downloads/rds-combined-ca-bundle.pem
    
"""
    # Create servers
    servers = ec2.run_instances(
        LaunchTemplate={"LaunchTemplateId": "lt-02c15b50d3a70c40f"},
        UserData=init,
        # SecurityGroupIds=["sg-0816ba1f92424b2f6"],
        MinCount=int(args.num_servers),
        MaxCount=int(args.num_servers),
    )
    instance_ids = [instance["InstanceId"] for instance in servers["Instances"]]
    print(f"instance ids: {instance_ids}")
    # print("waiting for server init")
    # waiter.wait(InstanceIds=instance_ids)
    # input("press enter when the servers are ready")
    # for (instance, inp) in zip(servers["Instances"], chunks(sites, args.num_servers)):
    #     cmds = [
    #         f"echo {str(inp)} > input.json",
    #         f"git clone {SRC_REPO} -o src",
    #         f"cd src/collection && go mod tidy && go run main.go > log.txt",
    #     ]
    #     resp = ssm.send_command(
    #         InstanceIds=[instance["InstanceId"]],
    #         DocumentName="AWS-RunShellScript",
    #         Parameters={"commands": cmds},
    #     )


# Clean input - run over 10 servers (~2k each) and then meet

if __name__ == "__main__":
    main()
